import hashlib
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples"))

from make_demo import create
from trigger_gaps import checker
from trigger_gaps.checker import InputError, Limits, check_snapshot
from trigger_gaps.header import UnsupportedHeader, fold, parse_header


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        # A deliberately awkward name (spaces and punctuation) that is still valid on every OS.
        # "?" is reserved on Windows, so it cannot appear here — the file could not be created.
        self.path = self.directory / "snapshot #1 with spaces & punctuation.sqlite"

    def database(self, sql):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript(sql)
        return self.path

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "trigger_gaps", "check", str(self.path), *args],
                              env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                              capture_output=True, text=True, timeout=10)

    def trigger(self, columns, table='items', timing='AFTER', body="SELECT 1;"):
        return f'CREATE TRIGGER t {timing} UPDATE OF {columns} ON {table} BEGIN {body} END;'

    def report(self, schema, columns, **kwargs):
        self.database(schema + self.trigger(columns, **kwargs))
        return check_snapshot(self.path)

    def test_main_cli_exact_names_and_locations(self):
        create(self.path)
        result = self.cli("--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["summary"], {"triggers": 3, "lists_analyzed": 3, "missing_occurrences": 2,
                                            "triggers_with_missing": 2, "omissions": 0})
        by_name = {t["name"]: t for t in report["triggers"]}
        self.assertEqual(by_name["good"]["status"], "all-recognized")
        self.assertEqual(by_name["mixed"]["status"], "mixed")
        self.assertEqual(by_name["typo"]["status"], "none-recognized")
        self.assertEqual([x["name"] for x in by_name["mixed"]["recognized"]], ["price"])
        self.assertEqual(by_name["mixed"]["missing"][0]["location"], {"offset": 44, "line": 1, "column": 45, "length": 5})
        self.assertEqual(by_name["typo"]["missing"][0]["location"], {"offset": 36, "line": 1, "column": 37, "length": 5})
        for t in report["triggers"]:
            for entry in t["declared"]:
                loc = entry["location"]
                self.assertEqual(t["sql"][loc["offset"]:loc["offset"] + loc["length"]], entry["raw"])

    def test_corrected_fixture(self):
        create(self.path, fixed=True)
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0 missing names, 0 omissions", result.stdout)

    def test_repeated_names_preserve_every_occurrence(self):
        r = self.report("CREATE TABLE items(price);", "price, prcie, price, prcie")
        t = r["triggers"][0]
        self.assertEqual(len(t["declared"]), 4)
        self.assertEqual(len(t["recognized"]), 2)
        self.assertEqual(len(t["missing"]), 2)
        self.assertNotEqual(t["missing"][0]["location"], t["missing"][1]["location"])

    def test_ascii_case_and_generated_columns(self):
        r = self.report('CREATE TABLE items(Price, doubled GENERATED ALWAYS AS (Price*2) VIRTUAL);', 'pRICE, DOUBLED', timing='BEFORE')
        self.assertEqual(r["exit_code"], 0)
        self.assertEqual([e["column"] for e in r["triggers"][0]["recognized"]], ["Price", "doubled"])

    def test_double_quotes_escapes_and_unicode_no_casefold(self):
        r = self.report('CREATE TABLE items("a""b", "ON", "ß", "İ");', '"a""b", "ON", "ß", "SS", "i"')
        t = r["triggers"][0]
        self.assertEqual([e["name"] for e in t["recognized"]], ['a"b', 'ON', 'ß'])
        self.assertEqual([e["name"] for e in t["missing"]], ['SS', 'i'])
        self.assertEqual(fold("ÅAß"), "Åaß")

    def test_comments_multiline_and_body_are_not_event_names(self):
        self.database('''CREATE TABLE items(price); CREATE TABLE log(kind);
CREATE TRIGGER t /* UPDATE OF wrong ON other */ AFTER UPDATE /* ON */ OF
 price, -- UPDATE OF fake ON fake
 prcie ON items WHEN 'UPDATE OF prcie ON items' != ''
BEGIN
 INSERT INTO log VALUES('UPDATE OF bogus ON items');
 /* UPDATE OF absent ON items */ SELECT 1;
END;''')
        t = check_snapshot(self.path)["triggers"][0]
        self.assertEqual([x["name"] for x in t["declared"]], ["price", "prcie"])
        self.assertEqual(t["missing"][0]["location"]["line"], 3)
        self.assertEqual(t["missing"][0]["location"]["column"], 2)

    def test_not_applicable_events(self):
        self.database('''CREATE TABLE items(price);
CREATE TRIGGER u AFTER UPDATE ON items BEGIN SELECT 'UPDATE OF prcie ON items'; END;
CREATE TRIGGER i BEFORE INSERT ON items BEGIN SELECT 1; END;
CREATE TRIGGER d AFTER DELETE ON items BEGIN SELECT 1; END;''')
        r = check_snapshot(self.path)
        self.assertEqual(r["summary"]["triggers"], 3)
        self.assertEqual(r["summary"]["lists_analyzed"], 0)
        self.assertTrue(all(t["status"] == "not-applicable" for t in r["triggers"]))
        self.assertEqual(r["exit_code"], 0)

    def test_default_timing_and_quoted_table_trigger(self):
        self.database('CREATE TABLE "t""b"(price); CREATE TRIGGER "n""m" UPDATE OF price ON "t""b" BEGIN SELECT 1; END;')
        r = check_snapshot(self.path)
        self.assertEqual(r["exit_code"], 0)
        self.assertEqual(r["triggers"][0]["timing"], "BEFORE")

    def test_unsupported_identifier_forms_are_localized(self):
        for columns in ('[price]', '`price`', "'price'", 'café'):
            with self.subTest(columns=columns):
                if self.path.exists():
                    self.path.unlink()
                r = self.report('CREATE TABLE items(price, café);', columns)
                self.assertEqual(r["exit_code"], 2)
                self.assertEqual(r["summary"]["missing_occurrences"], 0)
                self.assertEqual(r["omissions"][0]["reason"], "unsupported-identifier")
                self.assertEqual(r["omissions"][0]["location"]["column"], 34)

    def test_view_is_visible(self):
        self.database('CREATE TABLE items(price); CREATE VIEW v AS SELECT price FROM items; CREATE TRIGGER tv INSTEAD OF UPDATE OF prcie ON v BEGIN SELECT 1; END;')
        r = check_snapshot(self.path)
        self.assertEqual(r["exit_code"], 2)
        self.assertEqual(r["summary"]["triggers"], 1)
        self.assertEqual(r["omissions"][0]["reason"], "instead-of-trigger")

    def test_shadow_target_is_not_analyzed(self):
        # table_list reports a shadow table's type as "shadow"; the checker must omit a trigger
        # on one. We mock the metadata (as test_virtual_target_is_not_analyzed does) instead of
        # creating a real trigger on an FTS5 shadow table: recent SQLite (>= 3.51) refuses
        # "CREATE TRIGGER ... ON docs_content" ("cannot create triggers on shadow tables"), so
        # the old setup errored on new engines while testing SQLite's rule, not the checker's.
        self.database('CREATE TABLE items(price); CREATE TRIGGER st AFTER UPDATE OF prcie ON items BEGIN SELECT 1; END;')
        real_connect = sqlite3.connect
        class MetadataConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                cursor = super().execute(sql, *args)
                if sql == 'PRAGMA main.table_list':
                    return [tuple('shadow' if i == 2 and row[1] == 'items' else value
                                  for i, value in enumerate(row)) for row in cursor]
                return cursor
        def connect(*args, **kwargs):
            return real_connect(*args, **kwargs, factory=MetadataConnection)
        with patch.object(checker.sqlite3, 'connect', side_effect=connect):
            r = check_snapshot(self.path)
        self.assertEqual(r["exit_code"], 2)
        self.assertEqual(r["summary"]["triggers"], 1)
        self.assertEqual(r["summary"]["missing_occurrences"], 0)
        self.assertEqual(r["omissions"][0]["reason"], "target-shadow")

    def test_virtual_target_is_not_analyzed(self):
        self.database('CREATE TABLE items(price); CREATE TRIGGER t AFTER UPDATE OF prcie ON items BEGIN SELECT 1; END;')
        real_connect = sqlite3.connect
        class MetadataConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                cursor = super().execute(sql, *args)
                if sql == 'PRAGMA main.table_list':
                    return [tuple('virtual' if i == 2 and row[1] == 'items' else value
                                  for i, value in enumerate(row)) for row in cursor]
                return cursor
        def connect(*args, **kwargs):
            return real_connect(*args, **kwargs, factory=MetadataConnection)
        with patch.object(checker.sqlite3, 'connect', side_effect=connect):
            r = check_snapshot(self.path)
        self.assertEqual(r["exit_code"], 2)
        self.assertEqual(r["omissions"][0]["reason"], 'target-virtual')

    def test_malformed_stored_sql_is_an_input_error(self):
        create(self.path)
        data = self.path.read_bytes().replace(b'CREATE TRIGGER typo AFTER UPDATE', b'BROKEN TRIGGER typo AFTER UPDATE')
        self.assertNotEqual(data, self.path.read_bytes())
        self.path.write_bytes(data)
        result = self.cli('--json')
        self.assertEqual(result.returncode, 3)
        self.assertIn('error', json.loads(result.stdout))

    def test_implicit_rowid_aliases_preserve_other_findings(self):
        r = self.report('CREATE TABLE items(price);', 'rowid, OID, _rowid_, prcie, price')
        self.assertEqual(r["exit_code"], 2)
        self.assertEqual(r["summary"]["missing_occurrences"], 1)
        self.assertEqual(r["summary"]["omissions"], 3)
        self.assertTrue(all(x["reason"] == 'implicit-rowid-not-analyzed' for x in r["omissions"]))
        self.assertEqual([e["name"] for e in r["triggers"][0]["recognized"]], ["price"])

    def test_declared_rowid_aliases(self):
        r = self.report('CREATE TABLE items(rowid, oid, _rowid_);', 'ROWID, OID, _ROWID_')
        self.assertEqual(r["exit_code"], 0)
        self.assertEqual(len(r["triggers"][0]["recognized"]), 3)

    def test_without_rowid_alias_is_absent(self):
        r = self.report('CREATE TABLE items(price PRIMARY KEY) WITHOUT ROWID;', 'rowid, price')
        self.assertEqual(r["exit_code"], 1)
        self.assertEqual([e["name"] for e in r["triggers"][0]["missing"]], ["rowid"])

    def test_empty_inventory(self):
        self.database("CREATE TABLE items(price);")
        result = self.cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("Empty trigger inventory", result.stdout)

    def test_unsupported_trigger_and_findings_both_survive(self):
        create(self.path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TRIGGER other AFTER UPDATE OF [price] ON items BEGIN SELECT 1; END;')
        result = self.cli("--json")
        r = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(r["summary"]["missing_occurrences"], 2)
        self.assertEqual(r["summary"]["omissions"], 1)

    def test_wrong_body_does_not_mean_absent_event(self):
        r = self.report('CREATE TABLE items(price);', 'price', body='SELECT NEW.prcie;')
        self.assertEqual(r["exit_code"], 0)
        self.assertEqual(r["summary"]["missing_occurrences"], 0)

    def test_input_bytes_sidecars_and_log_unchanged(self):
        create(self.path)
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        files = sorted(p.name for p in self.directory.iterdir())
        check_snapshot(self.path)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), files)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT count(*) FROM log').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT price FROM items').fetchone()[0], 10)

    def test_missing_file_is_not_created(self):
        result = self.cli("--json")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["exit_code"], 3)
        self.assertFalse(self.path.exists())

    def test_invalid_database(self):
        self.path.write_bytes(b'not a database')
        self.assertEqual(self.cli().returncode, 3)
        self.path.write_bytes(b'SQLite format 3\x00' + b'x' * 500)
        self.assertEqual(self.cli().returncode, 3)

    def test_each_sidecar_rejected_even_empty(self):
        create(self.path)
        for suffix in ('-wal', '-journal', '-shm'):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(self.path) + suffix)
                sidecar.touch()
                self.assertEqual(self.cli().returncode, 3)
                sidecar.unlink()

    def test_limits_and_timeout(self):
        create(self.path)
        for args in (("--max-file-bytes", "100"), ("--max-sql-bytes", "10"), ("--timeout", "0.000000001")):
            with self.subTest(args=args):
                self.assertEqual(self.cli(*args).returncode, 3)

    def test_invalid_options(self):
        for args in (("--timeout", "nan"), ("--timeout", "inf"), ("--max-file-bytes", "0"), ("--max-file-bytes", "no")):
            with self.subTest(args=args):
                self.assertEqual(self.cli(*args).returncode, 3)

    def test_input_replacement_detected(self):
        create(self.path)
        original = checker.analyze
        def replace(db, limits, deadline):
            result = original(db, limits, deadline)
            replacement = self.directory / 'other.sqlite'
            create(replacement, fixed=True)
            os.replace(replacement, self.path)
            return result
        with patch.object(checker, 'analyze', side_effect=replace):
            with self.assertRaises(InputError) as caught:
                check_snapshot(self.path)
        # On POSIX the rename-over-open succeeds and the checker's own signature check fires
        # ("input changed during analysis"). Windows forbids replacing a file that is still
        # open, so the swap itself is refused (WinError 5, surfaced as InputError) — the
        # tampering is rejected either way, but only POSIX exercises the "changed" message.
        if os.name != 'nt':
            self.assertRegex(str(caught.exception), 'changed')

    def test_in_place_changes_detected(self):
        create(self.path)
        original = checker.analyze
        def change(db, limits, deadline):
            result = original(db, limits, deadline)
            with closing(sqlite3.connect(self.path)) as writer, writer:
                writer.execute('INSERT INTO log VALUES ("external writer")')
            return result
        with patch.object(checker, 'analyze', side_effect=change):
            with self.assertRaisesRegex(InputError, 'changed'):
                check_snapshot(self.path)

    def test_sidecar_appearing_during_analysis(self):
        create(self.path)
        original = checker.analyze
        def change(db, limits, deadline):
            result = original(db, limits, deadline)
            Path(str(self.path) + '-wal').touch()
            return result
        with patch.object(checker, 'analyze', side_effect=change):
            with self.assertRaisesRegex(InputError, 'sidecar'):
                check_snapshot(self.path)

    def test_authorizer_denies_rows_writes_attach_and_extensions(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE items(price)')
        db.set_authorizer(checker.authorize)
        for sql in ('SELECT * FROM items', 'UPDATE items SET price=2', "ATTACH ':memory:' AS other", "SELECT load_extension('x')"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                db.execute(sql)

    def test_readonly_input(self):
        create(self.path)
        self.path.chmod(0o444)
        self.assertEqual(check_snapshot(self.path)["exit_code"], 1)

    def test_symlink_rejected(self):
        create(self.directory / 'real.sqlite')
        try:
            self.path.symlink_to(self.directory / 'real.sqlite')
        except OSError as exc:
            self.skipTest("OS does not permit test symlink: " + str(exc))
        with self.assertRaises(InputError):
            check_snapshot(self.path)


class HeaderTests(unittest.TestCase):
    def test_uninterpretable_sql_localized(self):
        for sql in ('CREATE TRIGGER t AFTER UPDATE OF', 'CREATE TRIGGER t AFTER UPDATE OF "bad',
                    'CREATE TRIGGER t AFTER UPDATE OF price /* no end', 'not a trigger'):
            with self.subTest(sql=sql), self.assertRaises(UnsupportedHeader) as context:
                parse_header(sql)
            self.assertGreaterEqual(context.exception.token.line, 1)

    def test_schema_qualification(self):
        header = parse_header('CREATE TRIGGER IF NOT EXISTS main.t AFTER UPDATE OF "ON" ON main.items BEGIN SELECT 1; END;')
        self.assertEqual(header.table.value, 'items')
        self.assertEqual(header.columns[0].value, 'ON')
        with self.assertRaises(UnsupportedHeader):
            parse_header('CREATE TRIGGER other.t AFTER UPDATE ON items BEGIN SELECT 1; END;')


if __name__ == '__main__':
    unittest.main()

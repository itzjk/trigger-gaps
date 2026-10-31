from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
from contextlib import closing
import stat
import tempfile
import time
from dataclasses import dataclass

from . import __version__
from .header import UnsupportedHeader, fold, parse_header


class InputError(Exception):
    pass


@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = 256 * 1024 * 1024
    max_sql_bytes: int = 1024 * 1024
    timeout: float = 5.0


class Deadline:
    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds

    def tick(self) -> None:
        if time.monotonic() >= self.end:
            raise InputError("analysis timeout exceeded")

    def progress(self) -> int:
        return int(time.monotonic() >= self.end)


def signature(info: os.stat_result) -> tuple:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def reject_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-journal", "-shm"):
        if os.path.lexists(str(path) + suffix):
            raise InputError("snapshot has sidecar " + path.name + suffix + "; supply a consistent standalone copy")


def fingerprint(stream, deadline: Deadline, maximum: int, target=None) -> str:
    digest = hashlib.sha256()
    total = 0
    while True:
        deadline.tick()
        chunk = stream.read(min(65536, maximum - total + 1))
        if not chunk:
            return digest.hexdigest()
        total += len(chunk)
        if total > maximum:
            raise InputError("file byte limit exceeded")
        digest.update(chunk)
        if target is not None:
            target.write(chunk)


def authorize(action, first, second, database, source):
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and first in {"sqlite_master", "sqlite_schema"} and database == "main":
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and second in {"length", "count", "sum", "coalesce"}:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and first in {"table_list", "table_xinfo"}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def omission(trigger: dict, reason: str, location=None) -> dict:
    item = {"trigger": trigger["name"], "table": trigger["table"], "reason": reason}
    if location is not None:
        item["location"] = location
    trigger["omissions"].append(item)
    return item


def analyze(db: sqlite3.Connection, limits: Limits, deadline: Deadline) -> dict:
    if sqlite3.sqlite_version_info < (3, 37, 0):
        raise InputError("SQLite 3.37.0 or newer is required for table classification")
    deadline.tick()
    count, sql_bytes = db.execute(
        "SELECT count(*), coalesce(sum(length(cast(sql AS blob))),0) FROM main.sqlite_schema"
    ).fetchone()
    if count > 10000:
        raise InputError("schema object limit exceeded (10000)")
    if sql_bytes > limits.max_sql_bytes:
        raise InputError("schema SQL byte limit exceeded")
    objects = {fold(row[1]): row for row in db.execute("PRAGMA main.table_list") if row[0] == "main"}
    catalog = list(db.execute(
        "SELECT name, tbl_name, sql FROM main.sqlite_schema WHERE type='trigger' ORDER BY name COLLATE BINARY"
    ))
    results = []
    column_cache = {}
    for name, table, sql in catalog:
        deadline.tick()
        result = {"name": name, "table": table, "sql": sql, "event": None,
                  "timing": None, "status": "not-analyzed", "declared": [],
                  "recognized": [], "missing": [], "unresolved": [], "omissions": []}
        results.append(result)
        if not isinstance(sql, str):
            omission(result, "missing-trigger-sql")
            continue
        try:
            header = parse_header(sql, deadline.tick)
        except UnsupportedHeader as exc:
            omission(result, exc.reason, exc.token.location())
            continue
        result.update(event=header.event, timing=header.timing)
        if fold(header.name.value) != fold(name) or fold(header.table.value) != fold(table):
            omission(result, "catalog-header-mismatch", header.table.location())
            continue
        if header.columns is not None:
            result["declared"] = [{"name": t.value, "raw": t.raw, "location": t.location()} for t in header.columns]
        obj = objects.get(fold(table))
        if obj is None or obj[2] != "table":
            omission(result, "target-" + (obj[2] if obj else "not-found"), header.table.location())
            continue
        if header.columns is None:
            result["status"] = "not-applicable"
            continue
        if fold(table) not in column_cache:
            column_cache[fold(table)] = {
                fold(r[1]): r[1] for r in db.execute("PRAGMA main.table_xinfo(" + quote(table) + ")")
            }
        actual = column_cache[fold(table)]
        if not actual:
            omission(result, "column-metadata-unavailable", header.table.location())
            continue
        for entry in result["declared"]:
            deadline.tick()
            key = fold(entry["name"])
            if key in actual:
                result["recognized"].append({**entry, "column": actual[key]})
            elif key in {"rowid", "oid", "_rowid_"} and not obj[4]:
                result["unresolved"].append(entry)
                omission(result, "implicit-rowid-not-analyzed", entry["location"])
            else:
                result["missing"].append(entry)
        if result["unresolved"]:
            result["status"] = "incomplete"
        elif not result["missing"]:
            result["status"] = "all-recognized"
        elif result["recognized"]:
            result["status"] = "mixed"
        else:
            result["status"] = "none-recognized"
    omissions = [o for r in results for o in r["omissions"]]
    missing = sum(len(r["missing"]) for r in results)
    return {
        "schema_version": 1,
        "engine": {"name": "trigger-gaps", "version": __version__, "sqlite_version": sqlite3.sqlite_version},
        "scope": {"schema": "main", "temporary": False, "attached": False,
                  "locations": "stored-trigger-sql", "offset_unit": "unicode-code-point",
                  "line_base": 1, "column_base": 1, "offset_base": 0},
        "complete": not omissions,
        "exit_code": 2 if omissions else 1 if missing else 0,
        "summary": {"triggers": len(results),
                    "lists_analyzed": sum(r["status"] in {"all-recognized", "mixed", "none-recognized", "incomplete"} for r in results),
                    "missing_occurrences": missing,
                    "triggers_with_missing": sum(bool(r["missing"]) for r in results),
                    "omissions": len(omissions)},
        "triggers": results,
        "omissions": omissions,
    }


def check_snapshot(path: str | Path, limits: Limits = Limits()) -> dict:
    deadline = Deadline(limits.timeout)
    source = Path(os.path.abspath(path))
    try:
        if source.is_symlink():
            raise InputError("input must not be a symbolic link")
        reject_sidecars(source)
        fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            initial = os.fstat(stream.fileno())
            if not stat.S_ISREG(initial.st_mode):
                raise InputError("input must be a regular standalone SQLite file")
            if initial.st_size > limits.max_file_bytes:
                raise InputError("file byte limit exceeded")
            if stream.read(16) != b"SQLite format 3\x00":
                raise InputError("input is not a SQLite database")
            stream.seek(0)
            with tempfile.TemporaryDirectory(prefix="trigger-gaps-") as directory:
                private = Path(directory) / "snapshot.sqlite"
                with private.open("wb") as target:
                    digest = fingerprint(stream, deadline, limits.max_file_bytes, target)
                if signature(initial) != signature(os.fstat(stream.fileno())):
                    raise InputError("input changed while copying")
                reject_sidecars(source)
                uri = private.as_uri() + "?mode=ro&immutable=1"
                with closing(sqlite3.connect(uri, uri=True, timeout=0.0)) as db:
                    if hasattr(db, "enable_load_extension"):
                        db.enable_load_extension(False)
                    db.execute("PRAGMA trusted_schema=OFF")
                    db.execute("PRAGMA query_only=ON")
                    db.set_progress_handler(deadline.progress, 1000)
                    db.set_authorizer(authorize)
                    report = analyze(db, limits, deadline)
                stream.seek(0)
                final_digest = fingerprint(stream, deadline, limits.max_file_bytes)
                if (digest != final_digest or signature(initial) != signature(os.fstat(stream.fileno()))
                        or signature(initial) != signature(source.stat())):
                    raise InputError("input changed during analysis")
                reject_sidecars(source)
                deadline.tick()
                report["input"] = {"name": source.name, "sha256": digest, "bytes": initial.st_size}
                return report
    except InputError:
        raise
    except (OSError, sqlite3.Error, UnicodeError, ValueError) as exc:
        raise InputError(str(exc)) from exc

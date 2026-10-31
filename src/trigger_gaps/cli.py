import argparse
import json
import math
import sqlite3
import sys

from . import __version__
from .checker import InputError, Limits, check_snapshot


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return number


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(3, "trigger-gaps: " + message + "\n")


def label(entry: dict) -> str:
    loc = entry["location"]
    return json.dumps(entry["name"], ensure_ascii=True) + f"@{loc['line']}:{loc['column']}"


def render(report: dict) -> str:
    summary = report["summary"]
    engine = report["engine"]
    lines = [f"trigger-gaps {engine['version']} | SQLite {engine['sqlite_version']} | main",
             f"{summary['triggers']} triggers, {summary['lists_analyzed']} UPDATE OF lists, "
             f"{summary['missing_occurrences']} missing names, {summary['omissions']} omissions"]
    if not report["triggers"]:
        lines.append("Empty trigger inventory. Application behavior is not certified.")
    for trigger in report["triggers"]:
        lines.append(f"{json.dumps(trigger['name'])} on {json.dumps(trigger['table'])}: {trigger['status']}")
        if trigger["declared"]:
            for key in ("declared", "recognized", "missing", "unresolved"):
                lines.append("  " + key + ": " + (", ".join(label(x) for x in trigger[key]) or "(none)"))
        for item in trigger["omissions"]:
            loc = item.get("location")
            suffix = f" at {loc['line']}:{loc['column']}" if loc else ""
            lines.append("  not analyzed: " + item["reason"] + suffix)
    lines.append("Locations refer to stored trigger SQL. Bodies and WHEN expressions are not checked.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = ArgumentParser(prog="trigger-gaps", description="Find absent column names in SQLite UPDATE OF trigger events.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=ArgumentParser)
    check = sub.add_parser("check", help="inspect a standalone SQLite snapshot")
    check.add_argument("snapshot")
    check.add_argument("--json", action="store_true", help="emit report schema version 1")
    check.add_argument("--max-file-bytes", type=positive_int, default=Limits.max_file_bytes)
    check.add_argument("--max-sql-bytes", type=positive_int, default=Limits.max_sql_bytes)
    check.add_argument("--timeout", type=positive_float, default=Limits.timeout, metavar="SECONDS")
    args = parser.parse_args(argv)
    try:
        report = check_snapshot(args.snapshot, Limits(args.max_file_bytes, args.max_sql_bytes, args.timeout))
    except InputError as exc:
        if args.json:
            print(json.dumps({"schema_version": 1, "exit_code": 3, "error": str(exc),
                              "engine": {"name": "trigger-gaps", "version": __version__,
                                         "sqlite_version": sqlite3.sqlite_version}}))
        else:
            print("trigger-gaps: " + str(exc), file=sys.stderr)
        return 3
    print(json.dumps(report, ensure_ascii=True, indent=2) if args.json else render(report))
    return report["exit_code"]

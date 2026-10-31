import sqlite3
from contextlib import closing

from make_demo import SCHEMA


def run(fixed: bool) -> list[str]:
    with closing(sqlite3.connect(":memory:")) as db, db:
        db.executescript(SCHEMA.replace("prcie", "price") if fixed else SCHEMA)
        print("integrity_check:", db.execute("PRAGMA integrity_check").fetchone()[0])
        db.execute("UPDATE items SET price=11 WHERE id=1")
        return [r[0] for r in db.execute("SELECT kind FROM log ORDER BY kind")]


print("SQLite", sqlite3.sqlite_version)
print("Before:", run(False))
print("After:", run(True))

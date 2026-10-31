import argparse
from pathlib import Path
import sqlite3
from contextlib import closing


SCHEMA = """CREATE TABLE items(id INTEGER PRIMARY KEY, price INTEGER, title TEXT);
CREATE TABLE log(kind TEXT);
INSERT INTO items VALUES (1, 10, 'Sample');
CREATE TRIGGER good AFTER UPDATE OF price ON items
BEGIN INSERT INTO log VALUES ('good'); END;
CREATE TRIGGER typo AFTER UPDATE OF prcie ON items
BEGIN INSERT INTO log VALUES ('typo'); END;
CREATE TRIGGER mixed AFTER UPDATE OF price, prcie ON items
BEGIN INSERT INTO log VALUES ('mixed'); END;
"""


def create(path: Path, fixed: bool = False) -> None:
    if path.exists():
        raise FileExistsError(path.name + " already exists")
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(SCHEMA.replace("prcie", "price") if fixed else SCHEMA)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic demo snapshots; never overwrite existing files.")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    create(args.directory / "before.sqlite")
    create(args.directory / "after.sqlite", fixed=True)
    print("Created before.sqlite and after.sqlite")


if __name__ == "__main__":
    main()

import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


with closing(sqlite3.connect(Path(sys.argv[1]).resolve().as_uri() + '?mode=ro', uri=True)) as db:
    for name, table, sql in db.execute("SELECT name,tbl_name,sql FROM sqlite_schema WHERE type='trigger'"):
        columns = {r[1].lower() for r in db.execute('SELECT * FROM pragma_table_xinfo(?)', (table,))}
        match = re.search(r'UPDATE\s+OF\s+(.*?)\s+ON\s', sql, re.I | re.S)
        declared = [s.strip() for s in match[1].split(',')] if match else []
        print(name, 'declared=', declared, 'missing=', [s for s in declared if s.lower() not in columns])

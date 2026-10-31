# Report contract, version 1

`trigger-gaps check snapshot.sqlite --json` emits one JSON object. A completed report contains:

- `schema_version`: integer `1`, the report format version.
- `engine`: tool name, tool version, and runtime `sqlite_version`.
- `input`: basename, byte size, and SHA-256 of the inspected snapshot.
- `scope`: main only, no TEMP or attached schemas, and location conventions.
- `complete`: true if no in-scope omissions were found.
- `exit_code`: process status, with the precedence below.
- `summary`: trigger count, lists analyzed, absent name occurrences, triggers with absent names, and omission count.
- `triggers`: the complete persistent trigger inventory, ordered by name. Each has `name`, `table`, `sql`, `event`, `timing`, `status`, `declared`, `recognized`, `missing`, `unresolved`, and `omissions`.
- `omissions`: all per-trigger omissions, flattened. Nothing is suppressed by another trigger's result.

`declared`, `recognized`, `missing`, and `unresolved` contain occurrences, not deduplicated sets. Each occurrence has the decoded `name`, stored `raw` token, and `location`. Recognized entries also name their actual metadata `column`. Locations use one-based lines/columns and zero-based offsets measured in Unicode code points. `length` includes identifier quotes and escapes. Newlines are LF boundaries. A trigger's stored SQL is its source; comments before SQLite's stored `CREATE TRIGGER` are not part of that source.

| Trigger status | Meaning |
| --- | --- |
| `all-recognized` | Every event name occurs in column metadata |
| `mixed` | At least one recognized and at least one absent name |
| `none-recognized` | Every declared event name is absent from metadata |
| `not-applicable` | INSERT, DELETE, or UPDATE without an OF list on an ordinary table |
| `not-analyzed` | Header or target outside the supported subset |
| `incomplete` | Parsed list includes unresolved implicit rowid aliases; known findings remain |

`none-recognized` describes name resolution, not a proof about application behavior. The tool does not simulate updates. A list naming only generated columns is still recognized. It does not imply those columns are directly assignable. On WITHOUT ROWID tables, undeclared `rowid`, `oid`, and `_rowid_` are absent names, because those tables have no implicit rowid. On rowid tables each undeclared alias is unresolved, even when an INTEGER PRIMARY KEY exists.

Omission reasons include `implicit-rowid-not-analyzed`, `unsupported-identifier`, `instead-of-trigger`, `target-view`, `target-virtual`, `target-shadow`, `column-metadata-unavailable`, `catalog-header-mismatch`, and specific `expected-*` parse failures. Each omission names its trigger and table; header and occurrence omissions include a location. An unsupported header skips that trigger's list rather than guessing where it ends. Other triggers are still checked.

| Exit | Meaning |
| --- | --- |
| 0 | Analysis complete within scope, no absent event names |
| 1 | Complete, one or more absent names |
| 2 | Coverage incomplete, with or without absent names |
| 3 | Input, command, size, timeout, or SQLite error; no completed report |

Input errors with `--json` produce `schema_version`, `engine`, `exit_code: 3`, and `error`. Argument parsing errors go to stderr, including when `--json` was supplied. Limits cover the source file, total schema SQL bytes in the database encoding, a fixed 10000-object schema cap, and a cooperative deadline across copying, SQLite work, tokenization, and verification. Blocking filesystem operations are subject to the operating system. Reports include decoded schema SQL, so their JSON byte size can exceed the SQL limit.

The checker holds an input descriptor, copies bounded bytes to a private temporary directory, rejects sidecars before and after, and checks hashes and device/inode/size/mtime/ctime after analysis. SQLite opens only the private copy with `mode=ro&immutable=1`, `query_only=ON`, and `trusted_schema=OFF`. A SQL authorizer restricts queries to schema reads and the two metadata pragmas. Loadable extensions are disabled (or unavailable in the Python build). No user functions or migration SQL are loaded. Detected changes invalidate the whole report. Use a consistent snapshot; these checks are not a lock on a live writer.

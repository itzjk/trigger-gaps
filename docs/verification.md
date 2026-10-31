# Verification on 2026-10-31

Runtime: Python 3.15.1, SQLite 3.54.1, macOS. All database fixtures are synthetic.

| Check | Observed result |
| --- | --- |
| `python3 -m unittest discover -s tests -v` from a clean local Git clone | 33 tests passed, no skips |
| Separate reviewer, documented local installation and suite | Wheel installed; 33 tests passed |
| Separate installed-CLI adversarial harness | 15 cases passed |
| Before fixture | Exit 1; 3 triggers, 3 lists, 2 absent occurrences in 2 triggers, no omissions |
| After fixture | Exit 0; 3 triggers, 3 lists, no absent names or omissions |
| Source locations | `mixed.prcie` at 1:45, offset 44; `typo.prcie` at 1:37, offset 36; both length 5 |
| Independent mutation runs | All 3 mutations detected: accept every name, prefilter unknown names, hide mixed-list findings |
| Local pipx installation | Installed 0.1.0; entry point and corrected fixture worked |
| Public GitHub pipx installation | Attempted; remote returned `Repository not found`, Git clone exit 128 |
| Demo | Real VHS capture, 18.04 seconds, 1160 by 880, 451 frames; both report states visually checked |
| Remote CI | Configured for Linux, macOS, Windows and Python 3.10, 3.13, 3.15; not run remotely |

The fixture's separate log reproduction returned `integrity_check: ok` for both databases; updating `price` logged `good,mixed` before and `good,mixed,typo` afterward. This script uses only an in-memory fixture. The checker does not execute those updates.

Input safety checks include unchanged input SHA-256 and directory entries, untouched business rows and log, chmod-444 input, missing/invalid inputs, each empty sidecar, in-place writes, path replacement, late sidecars, resource limits, and authorizer rejection of business-row reads, writes, ATTACH and load_extension. Header tests include strings/comments, generated columns, repeated and quoted names, ASCII comparison without Unicode case folding, rowid aliases, and unsupported syntax.

The virtual-target guard is exercised through controlled metadata substitution: SQLite rejects ordinary CREATE TRIGGER statements on virtual tables, so a valid snapshot with such a trigger cannot be generated. The shadow-target test uses a real FTS5 shadow table. Malformed stored SQL is treated as an input error rather than a column finding.

The standard VHS browser launcher could not start Chromium in the recording environment. The recording used a local VHS launcher with Chromium in a single process; the installed CLI and tape commands were unchanged. Recreate fixtures with `python3 examples/make_demo.py .work/demo`, install into `.venv`, then run `vhs docs/demo.tape` on a machine where its Chromium launcher works.

Publication checks still required: install from the public GitHub URL after the repository exists, and observe the remote CI matrix. The alternatives review is bounded, especially duck-sqllsp, which could not be executed here. These results establish the implemented check, not market demand or general trigger correctness.

#!/usr/bin/env python3
"""Refuses (exit 1) if any migration file contains a destructive statement
(DROP, RENAME, SET NOT NULL, or a column type change) unless that same file
also contains an explicit approval comment.

Why: deploy/scripts/release.sh runs migrations against a tenant's database
*before* swapping that tenant's Cloud Run revision to the new image (see
deploy/README.md "Releasing code vs changing infrastructure" and the
expand/contract rule it documents). Other tenants, and the old revision of
this tenant during the brief overlap, are still running the OLD code against
the NEW schema until their own release goes out. A migration that drops a
column, renames something, tightens a NOT NULL, or changes a column's type
in the same release that also removes the old code's use of it breaks that
overlap window. Migrations shipped with a release must be purely additive
(new tables, nullable columns, new indexes); a destructive change belongs in
a LATER release, once no running revision needs the old shape.

This is a lint, not a schema analyzer — it pattern-matches SQL statement
keywords, so it can be wrong in either direction (e.g. it would flag "DROP
INDEX" too, which is usually safe; it won't catch every conceivable
destructive statement). That's why a genuinely intended destructive
migration isn't blocked outright, just gated behind a deliberate, reviewed
comment naming the migration and why it's safe now:

    -- destructive-migration-approved: <reason>

Usage:
    python3 check_migration_safety.py                       # scan backend/db/migrations
    python3 check_migration_safety.py --migrations-dir DIR
    python3 check_migration_safety.py file1.sql file2.sql    # scan only these files
"""

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MIGRATIONS_DIR = REPO_ROOT / "backend" / "db" / "migrations"

APPROVAL_MARKER_RE = re.compile(r"--\s*destructive-migration-approved\b", re.IGNORECASE)

# Each pattern is matched against the SQL with comments already stripped.
# Kept intentionally broad (over-flagging is a nuisance; under-flagging is a
# production incident) — see module docstring.
DESTRUCTIVE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("DROP", re.compile(r"\bDROP\s+(TABLE|COLUMN|INDEX|CONSTRAINT|VIEW|TYPE|SEQUENCE)\b", re.IGNORECASE)),
    ("RENAME", re.compile(r"\bRENAME\s+(TO|COLUMN)\b", re.IGNORECASE)),
    ("SET NOT NULL", re.compile(r"\bSET\s+NOT\s+NULL\b", re.IGNORECASE)),
    ("TYPE change", re.compile(r"\bALTER\s+COLUMN\s+\S+\s+(SET\s+DATA\s+)?TYPE\b", re.IGNORECASE)),
]


def _strip_sql_comments(sql: str) -> str:
    """Strips `--` line comments and `/* ... */` block comments, so a
    destructive keyword mentioned only in prose (like this file's own
    docstring, or a migration's header) never triggers a false positive.
    Not a full SQL parser — doesn't handle a `--`/`/*` inside a string
    literal, which none of this repo's migrations currently contain."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", "", sql)
    return sql


def find_violations(path: Path) -> list[str]:
    """Returns the list of destructive-statement kinds found in `path`
    (e.g. ["DROP", "RENAME"]), empty if none. Does not consult the approval
    marker — that's applied by the caller, once per file."""
    text = path.read_text()
    code = _strip_sql_comments(text)
    found = []
    for kind, pattern in DESTRUCTIVE_PATTERNS:
        if pattern.search(code):
            found.append(kind)
    return found


def is_approved(path: Path) -> bool:
    return bool(APPROVAL_MARKER_RE.search(path.read_text()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--migrations-dir",
        default=str(DEFAULT_MIGRATIONS_DIR),
        help=f"Directory to scan when no files are given explicitly (default: {DEFAULT_MIGRATIONS_DIR})",
    )
    parser.add_argument("files", nargs="*", help="Specific migration file(s) to check instead of scanning a directory.")
    args = parser.parse_args()

    if args.files:
        paths = [Path(f) for f in args.files]
    else:
        migrations_dir = Path(args.migrations_dir)
        if not migrations_dir.is_dir():
            print(f"REFUSING: migrations directory not found: {migrations_dir}", file=sys.stderr)
            return 2
        paths = sorted(migrations_dir.glob("*.sql"))

    if not paths:
        print("OK: no migration files to check.", file=sys.stderr)
        return 0

    blocked: list[tuple[Path, list[str]]] = []
    for path in paths:
        if not path.is_file():
            print(f"REFUSING: not a file: {path}", file=sys.stderr)
            return 2
        kinds = find_violations(path)
        if kinds and not is_approved(path):
            blocked.append((path, kinds))

    if blocked:
        print("REFUSING: destructive statement(s) found without an approval comment:", file=sys.stderr)
        for path, kinds in blocked:
            print(f"  {path}: {', '.join(kinds)}", file=sys.stderr)
        print(
            "\nMigrations shipped with a release must be additive (see deploy/README.md's "
            "expand/contract rule) — a DROP, RENAME, SET NOT NULL, or column type change can "
            "break tenants/revisions still running the old code against the new schema during "
            "a rolling release.\n"
            "If this migration is genuinely safe to ship now (no running revision needs the old "
            "shape anymore), add this line to the file and re-run:\n"
            "  -- destructive-migration-approved: <reason>",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(paths)} migration file(s) checked, no unapproved destructive statements.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

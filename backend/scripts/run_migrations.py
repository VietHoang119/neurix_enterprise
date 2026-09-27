"""Migration runner: applies backend/db/migrations/*.sql against a database,
tracking what's applied in a `schema_migrations` table.

Problem this replaces: the runbook's manual `psql -f 0001_...sql`,
`psql -f 0002_...sql`, ... sequence, with no record anywhere of which
migrations a given database has actually had applied. Run this instead:

    python -m scripts.run_migrations

(from `backend/`, so `db`/`core` resolve — same convention as `main.py`.)
It reads `backend/db/migrations/`, checks `schema_migrations` for what's
already recorded, and runs only the missing files, each in its own
transaction; a migration is recorded as applied only after its transaction
commits. On failure it stops immediately and reports exactly which file
failed — migrations before it stay committed and recorded, the failing one
is rolled back and NOT recorded, so re-running the script after a fix
resumes from there instead of re-attempting already-applied migrations.

Idempotency: 0002-0005 were retrofitted (see their own file headers) to use
`IF NOT EXISTS` / a `pg_constraint` guard on every statement, so re-running
any of them against a database that already has them applied is a no-op,
not an error. 0001 is the one exception — its CREATE TABLE statements
predate this runner and are deliberately left untouched (see its header) —
so it is not safe to blindly re-run. See _RECONCILE_PROBE below for how
that's handled for a database that already has it applied.

Connects via db.session.get_engine(), i.e. whatever INSTANCE_CONNECTION_NAME
/ LOCAL_DATABASE_URL is set in the environment — the same connection the app
itself would use. There is no separate --database-url flag: point this at a
database the same way you'd point the app at one.
"""

import logging
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"

logger = logging.getLogger(__name__)


class MigrationError(RuntimeError):
    """A migration failed to apply. Carries which one, and why, so the
    caller can report both without re-parsing a message string."""

    def __init__(self, version: str, cause: Exception):
        super().__init__(f"migration {version} failed: {cause}")
        self.version = version
        self.cause = cause


# One-time reconciliation for a database that already has a migration's
# changes applied but no schema_migrations row for it (every database that
# predates this runner). Only 0001 needs this: it is the sole migration not
# retrofitted for safe re-running (see module docstring), so for it alone we
# probe for its result — the table it creates — rather than just trying to
# run it and letting Postgres reject the duplicate CREATE TABLE. 0002-0005
# need no entry here: they are idempotent, so simply running them (below)
# already does the right thing whether or not they were applied before.
_RECONCILE_PROBE: dict[str, str] = {
    "0001_init_from_neurix": "SELECT to_regclass('public.node_pool') IS NOT NULL",
}


def _ensure_tracking_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "  version text PRIMARY KEY,"
                "  applied_at timestamptz NOT NULL DEFAULT now()"
                ")"
            )
        )


def _applied_versions(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version FROM schema_migrations")).all()
    return {row[0] for row in rows}


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _record_applied(engine: Engine, version: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO schema_migrations (version) VALUES (:v)"), {"v": version})


def _reconcile_if_already_applied(engine: Engine, version: str) -> bool:
    """-> True if `version`'s changes were already present in the database
    (checked via _RECONCILE_PROBE) and its tracking row has now been seeded
    without re-running its SQL. False if it has no probe, or the probe says
    it genuinely hasn't been applied yet — the caller should run it."""
    probe = _RECONCILE_PROBE.get(version)
    if probe is None:
        return False
    with engine.connect() as conn:
        already_applied = bool(conn.execute(text(probe)).scalar())
    if not already_applied:
        return False
    logger.info("MIGRATION_RECONCILED | version=%s | already present, recording without re-running", version)
    _record_applied(engine, version)
    return True


def run_migrations(engine: Engine) -> list[str]:
    """Apply every migration not yet recorded in schema_migrations, in
    filename order, each in its own transaction. Returns the versions
    actually executed (a version reconciled via _RECONCILE_PROBE, or already
    recorded, is not included). Raises MigrationError on the first failure;
    everything applied before it stays committed and recorded."""
    _ensure_tracking_table(engine)
    applied = _applied_versions(engine)
    executed: list[str] = []

    for path in _migration_files():
        version = path.stem
        if version in applied:
            continue

        if _reconcile_if_already_applied(engine, version):
            continue

        logger.info("MIGRATION_APPLYING | version=%s", version)
        sql = path.read_text()
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(sql)
                conn.execute(text("INSERT INTO schema_migrations (version) VALUES (:v)"), {"v": version})
        except Exception as exc:
            raise MigrationError(version, exc) from exc
        logger.info("MIGRATION_APPLIED | version=%s", version)
        executed.append(version)

    return executed


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Local import: keeps `python -m scripts.run_migrations --help`-style
    # startup from pulling in the full app settings/engine machinery until
    # we're actually about to connect.
    from db.session import get_engine

    try:
        executed = run_migrations(get_engine())
    except MigrationError as exc:
        logger.error("MIGRATION_FAILED | version=%s | error=%s", exc.version, exc.cause)
        print(f"Migration failed: {exc.version}\n  {exc.cause}", file=sys.stderr)
        return 1

    if executed:
        logger.info("MIGRATIONS_DONE | applied=%s", ", ".join(executed))
    else:
        logger.info("MIGRATIONS_DONE | nothing to apply, already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Grants backend/scripts/run_migrations.py's tables/sequences to a tenant's
app DB user, and sets ALTER DEFAULT PRIVILEGES so every future migration's
new tables/sequences are granted automatically too — see
deploy/scripts/sql/grant_app_user.sql.tmpl for why.

Connects exactly the way backend/scripts/run_migrations.py does: via
backend/db/session.py's get_engine(), i.e. whatever INSTANCE_CONNECTION_NAME
/ LOCAL_DATABASE_URL + DB_USER/DB_PASSWORD/DB_NAME are set in the
environment. Run this immediately after the migration runner, as the same
role (DB_USER=postgres) — never as the app user itself, which has no grant
privilege.

Usage (from anywhere; it locates backend/ itself):

    INSTANCE_CONNECTION_NAME=... DB_USER=postgres DB_PASSWORD=... DB_NAME=neurix_acme \\
      python3 deploy/scripts/grant_tenant.py --db-app-user app_acme \\
        --project-id my-test-project --tenant-id acme

--project-id and --tenant-id are required specifically for the guard below —
this script does not otherwise need them to connect (DB_NAME/DB_USER/
DB_PASSWORD/INSTANCE_CONNECTION_NAME already fully determine the connection).

This script does not modify backend/ — it imports backend's existing
db.session module unchanged, the same way run_migrations.py does.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
SQL_TEMPLATE = Path(__file__).resolve().parent / "sql" / "grant_app_user.sql.tmpl"

# Mirrors deploy/scripts/lib.sh's NEURIX_PRODUCTION_PROJECT_ID. Duplicated
# rather than sourced (this is Python, lib.sh is bash) — see deploy/README.md
# "Hard safety rule" for the single source of truth this constant reflects.
NEURIX_PRODUCTION_PROJECT_ID = "neurix-enterprise"

logger = logging.getLogger(__name__)


def guard_sql_target(project_id: str, tenant_id: str, db_name: str, db_user: str, instance: str) -> None:
    """Defense-in-depth check, mirroring deploy/scripts/lib.sh's
    guard_sql_target: refuses outright if db_name isn't exactly this
    tenant's own database, always prints instance/db/user before
    connecting, and requires a typed confirmation when project_id is the
    production project. This is what stands between a bad --tenant-id (or a
    DB_NAME set by hand, bypassing provision.sh entirely) and ALTER DEFAULT
    PRIVILEGES / GRANT ever landing on 'neurix', 'postgres', or any database
    that isn't this tenant's own."""
    expected = f"neurix_{tenant_id}"
    if db_name != expected:
        logger.error(
            "REFUSING | target database '%s' does not match the expected tenant database '%s'. "
            "This guard exists specifically to stop a grant from ever landing on 'neurix', 'postgres', "
            "or any database that isn't this tenant's own.",
            db_name,
            expected,
        )
        raise SystemExit(1)

    logger.info("SQL target | instance=%s | database=%s | user=%s", instance, db_name, db_user)

    if project_id == NEURIX_PRODUCTION_PROJECT_ID:
        logger.warning("About to run a SQL step against the PRODUCTION project's Cloud SQL instance.")
        typed = input(f"Type 'yes' to confirm this SQL step against '{db_name}' on '{instance}': ")
        if typed.strip() != "yes":
            logger.error("Aborted at SQL guard.")
            raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-app-user", required=True, help="Tenant app DB role, e.g. app_acme.")
    parser.add_argument("--project-id", required=True, help="GCP project id — used only by guard_sql_target below.")
    parser.add_argument("--tenant-id", required=True, help="Tenant id — DB_NAME must equal neurix_<tenant-id>.")
    parser.add_argument(
        "--migration-role",
        default=os.environ.get("DB_USER", "postgres"),
        help="Role migrations run as (defaults to $DB_USER, normally 'postgres'). "
        "ALTER DEFAULT PRIVILEGES is scoped to objects this role creates.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not SQL_TEMPLATE.exists():
        logger.error("GRANT_TEMPLATE_MISSING | path=%s", SQL_TEMPLATE)
        return 1

    db_name = os.environ.get("DB_NAME", "")
    instance = os.environ.get("INSTANCE_CONNECTION_NAME") or os.environ.get("LOCAL_DATABASE_URL", "")
    guard_sql_target(args.project_id, args.tenant_id, db_name, args.db_app_user, instance)

    sql = SQL_TEMPLATE.read_text()
    sql = sql.replace("__DB_APP_USER__", args.db_app_user).replace("__MIGRATION_ROLE__", args.migration_role)

    sys.path.insert(0, str(BACKEND_DIR))
    from db.session import get_engine  # noqa: E402  (path must be set up first, same as run_migrations.py)

    # guard_sql_target above has already asserted DB_NAME == neurix_<tenant_id>
    # for the environment get_engine() reads from — this connection, and
    # every statement executed on it below, can only ever target that
    # tenant's own database.
    engine = get_engine()

    with engine.begin() as conn:
        for statement in filter(None, (s.strip() for s in sql.split(";"))):
            conn.execute(text(statement))

    logger.info(
        "GRANTS_APPLIED | db_app_user=%s | migration_role=%s",
        args.db_app_user,
        args.migration_role,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

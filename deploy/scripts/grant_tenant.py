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
      python3 deploy/scripts/grant_tenant.py --db-app-user app_acme

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

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-app-user", required=True, help="Tenant app DB role, e.g. app_acme.")
    parser.add_argument(
        "--migration-role",
        default=os.environ.get("DB_USER", "postgres"),
        help="Role migrations run as (defaults to $DB_USER, normally 'postgres'). "
        "ALTER DEFAULT PRIVILEGES is scoped to objects this role creates.",
    )
    args = parser.parse_args()

    if not SQL_TEMPLATE.exists():
        logger.error("GRANT_TEMPLATE_MISSING | path=%s", SQL_TEMPLATE)
        return 1

    sql = SQL_TEMPLATE.read_text()
    sql = sql.replace("__DB_APP_USER__", args.db_app_user).replace("__MIGRATION_ROLE__", args.migration_role)

    sys.path.insert(0, str(BACKEND_DIR))
    from db.session import get_engine  # noqa: E402  (path must be set up first, same as run_migrations.py)

    engine = get_engine()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

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

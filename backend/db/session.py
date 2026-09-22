"""Cloud SQL connection via the Cloud SQL Python Connector.

One code path that works unchanged on Cloud Run and locally against the
same instance (mutual TLS, no proxy process, no hardcoded TCP host/IP) —
per the handoff's "never a hardcoded TCP IP" constraint. Local development
without a Cloud SQL instance falls back to LOCAL_DATABASE_URL (e.g. a
docker-compose Postgres).
"""

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from core.config import settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    if settings.uses_cloud_sql():
        from google.cloud.sql.connector import Connector

        connector = Connector()

        def creator():
            return connector.connect(
                settings.INSTANCE_CONNECTION_NAME,
                "pg8000",
                user=settings.DB_USER,
                password=settings.DB_PASSWORD,
                db=settings.DB_NAME,
            )

        return create_engine("postgresql+pg8000://", creator=creator, pool_pre_ping=True)

    if settings.LOCAL_DATABASE_URL:
        return create_engine(settings.LOCAL_DATABASE_URL, pool_pre_ping=True)

    raise RuntimeError(
        "No database configured: set INSTANCE_CONNECTION_NAME (Cloud SQL) "
        "or LOCAL_DATABASE_URL (local dev)."
    )

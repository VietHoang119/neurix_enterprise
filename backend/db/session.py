"""Cloud SQL connection scaffolding.

No product feature queries against this yet (Week 1 is infra-only) — this
exists so Week 2 has a ready-made, environment-portable way to get a
connection instead of reaching for a raw TCP host/IP.

Two paths, both avoiding hardcoded IPs:
  * On Cloud Run, with the Cloud SQL instance attached to the service,
    `/cloudsql/<INSTANCE_CONNECTION_NAME>` is a Unix socket mounted for you —
    `get_connection_string()` targets it directly.
  * Anywhere else (local dev, CI), `get_cloud_sql_connector_creator()` returns
    a `creator` callable for `sqlalchemy.create_engine(..., creator=...)` that
    uses the Cloud SQL Python Connector (mutual TLS, no proxy process needed).
"""

from collections.abc import Callable

from core.config import settings


def get_connection_string() -> str:
    """Unix-socket DSN for use inside Cloud Run (Cloud SQL instance attached)."""
    if not settings.uses_cloud_sql():
        if settings.LOCAL_DATABASE_URL:
            return settings.LOCAL_DATABASE_URL
        raise RuntimeError(
            "No database configured: set INSTANCE_CONNECTION_NAME (Cloud SQL) "
            "or LOCAL_DATABASE_URL (local dev)."
        )

    socket_path = f"/cloudsql/{settings.INSTANCE_CONNECTION_NAME}"
    return (
        f"postgresql+pg8000://{settings.DB_USER}:{settings.DB_PASSWORD}"
        f"@/{settings.DB_NAME}?unix_sock={socket_path}/.s.PGSQL.5432"
    )


def get_cloud_sql_connector_creator() -> Callable:
    """Return a `creator` callable for `create_engine(creator=...)` using the
    Cloud SQL Python Connector — for environments without the Unix socket
    mount (local dev against a real Cloud SQL instance, CI, etc.).
    """
    from google.cloud.sql.connector import Connector

    if not settings.uses_cloud_sql():
        raise RuntimeError("INSTANCE_CONNECTION_NAME is not set.")

    connector = Connector()

    def creator():
        return connector.connect(
            settings.INSTANCE_CONNECTION_NAME,
            "pg8000",
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            db=settings.DB_NAME,
        )

    return creator

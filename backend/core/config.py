"""Runtime settings for the Enterprise backend.

Replaces neurix's Supabase-centric Settings (app/core/settings.py) with
Cloud SQL + Firebase equivalents. Supabase env vars are intentionally gone —
this is a clean cutover, not a dual-stack config.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    MODE: str = "dev"

    # Cloud Run injects PORT; never hardcode 8080 in code that reads this.
    PORT: int = 8080

    # Firebase Auth (ID token verification in core/auth.py)
    FIREBASE_PROJECT_ID: str | None = None

    # Cloud SQL (Postgres 15+, pgvector), via the Cloud SQL Python Connector —
    # works both on Cloud Run and locally against the same instance, with no
    # hardcoded TCP host/IP either way.
    INSTANCE_CONNECTION_NAME: str | None = None  # "project:region:instance"
    DB_USER: str | None = None
    DB_PASSWORD: str | None = None
    DB_NAME: str | None = None

    # Local/dev-only escape hatch (e.g. docker-compose postgres). Never used
    # in Cloud Run — INSTANCE_CONNECTION_NAME takes priority when both are set.
    LOCAL_DATABASE_URL: str | None = None

    FRONTEND_URL: str = "http://localhost:5173"

    def is_dev(self) -> bool:
        return self.MODE == "dev"

    def uses_cloud_sql(self) -> bool:
        return bool(self.INSTANCE_CONNECTION_NAME)


settings = Settings()

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

    # SQLAlchemy engine pool sizing (db/session.py). Defaults match
    # SQLAlchemy's own defaults (5 + 10 = 15 connections/instance), so this
    # is a no-op until set. Needs to be tunable per deployment: N Cloud Run
    # services against one shared Cloud SQL instance will exhaust
    # max_connections long before CPU or memory becomes the bottleneck, and
    # the right pool_size depends on how many instances a given tenant's
    # service scales to.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10

    FRONTEND_URL: str = "http://localhost:5173"

    # Slack App (Week 2). Both come from Secret Manager on Cloud Run
    # (slack-bot-token / slack-signing-secret).
    SLACK_BOT_TOKEN: str | None = None
    SLACK_SIGNING_SECRET: str | None = None
    # Slack OAuth install (Add to Slack / shareable install URL). Its only
    # job here is linking the installer's team to its workspace
    # (orgs.slack_team_id / slack_workspace_url). Unset: /slack/oauth/callback
    # is 404 and workspaces are linked from slash commands alone.
    SLACK_CLIENT_ID: str | None = None
    SLACK_CLIENT_SECRET: str | None = None
    # Only if the install URL passes redirect_uri (Slack then requires the same value here).
    SLACK_OAUTH_REDIRECT_URI: str | None = None
    # Hard cap for file ingest (v1: PDF, txt, md, docx, xlsx, images).
    SLACK_MAX_FILE_BYTES: int = 20 * 1024 * 1024

    # Cloud Storage bucket for original ingest content (raw/{uid}/...), which
    # `view_raw` grants access to. Unset: nothing is stored and
    # `/neurix file` says so.
    RAW_STORAGE_BUCKET: str | None = None
    RAW_URL_TTL_SECONDS: int = 600

    # Gemini — same thresholds as neurix's app/core/settings.py.
    GOOGLE_API_KEY: str | None = None
    # Chat model used by every generate call (extraction, file OCR, ask).
    # Plain Cloud Run env vars, not secrets, so a Google deprecation is an
    # env-var update rather than a code change.
    GEMINI_MODEL: str = "gemini-3.6-flash"
    # Comma-separated models tried in order after GEMINI_MODEL, only when a
    # model answers 404 NotFound (see core/llm.py). GEMINI_MODEL itself may
    # appear here too; duplicates are ignored.
    GEMINI_MODEL_FALLBACKS: str = "gemini-3.6-flash,gemini-3.5-flash"
    EMBEDDING_MODEL: str = "models/gemini-embedding-001"
    GEMINI_EMBEDDING_DIM: int = 768
    SIMILARITY_THRESHOLD: float = 0.1  # KeysExtractorAgent's similar-node lookup
    TOP_K_NODE: int = 5
    MIN_KEYS: int = 5
    MAX_KEYS: int = 40
    KEYS_SIMILARITY_SCORE: float = 0.9  # Stage 2 key dedup threshold
    CHAT_RELEVANCE_THRESHOLD: float = 0.48  # absolute floor for /neurix ask sources

    def is_dev(self) -> bool:
        return self.MODE == "dev"

    def uses_cloud_sql(self) -> bool:
        return bool(self.INSTANCE_CONNECTION_NAME)


settings = Settings()

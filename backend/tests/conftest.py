"""Test fixtures.

DB tests run against a real Postgres with pgvector: a throwaway database is
created from TEST_DATABASE_URL's server and the real migrations (0001-0003)
are applied, so every query is exercised against the actual schema. They are
skipped when no such server is reachable.

Gemini and Slack are faked: deterministic bag-of-words embeddings, scripted
structured outputs, and a Slack client that records calls.
"""

import hashlib
import math
import os
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from core import llm  # noqa: E402
from core.config import settings  # noqa: E402
from db import session as db_session  # noqa: E402

SERVER_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+pg8000://postgres:postgres@localhost:5432/postgres")
SIGNING_SECRET = "test-signing-secret"


# ---------------------------------------------------------------- database

@pytest.fixture(scope="session")
def database_url():
    from sqlalchemy import create_engine, text

    try:
        admin = create_engine(SERVER_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"No Postgres available at TEST_DATABASE_URL: {exc}")

    name = f"neurix_test_{uuid.uuid4().hex[:8]}"
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    url = SERVER_URL.rsplit("/", 1)[0] + "/" + name

    engine = create_engine(url)
    with engine.begin() as conn:
        for path in sorted((BACKEND / "db" / "migrations").glob("*.sql")):
            conn.exec_driver_sql(path.read_text())
    engine.dispose()

    yield url

    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))


@pytest.fixture
def db(database_url, monkeypatch):
    """Point the app at the test DB, and empty all app tables afterwards."""
    monkeypatch.setattr(settings, "LOCAL_DATABASE_URL", database_url)
    monkeypatch.setattr(settings, "INSTANCE_CONNECTION_NAME", None)
    db_session.get_engine.cache_clear()
    engine = db_session.get_engine()
    yield engine
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "TRUNCATE notifications, connection, share_node, org_members, orgs, node_keys, key_pool, node_drafts, "
            "retrieval_log, node_pool, auth.users CASCADE"
        )
    engine.dispose()
    db_session.get_engine.cache_clear()


# ---------------------------------------------------------------- gemini

def fake_vector(text: str) -> list[float]:
    vec = [0.0] * settings.GEMINI_EMBEDDING_DIM
    for token in text.lower().split():
        idx = int(hashlib.md5(token.encode()).hexdigest(), 16) % len(vec)
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeStructured:
    def __init__(self, fake, schema):
        self.fake, self.schema = fake, schema

    async def ainvoke(self, messages):
        return self.schema(**self.fake.structured[self.schema.__name__])


class FakeChatModel:
    def __init__(self):
        self.structured: dict[str, dict] = {}
        self.answer = ""
        self.calls: list = []

    def with_structured_output(self, schema):
        return FakeStructured(self, schema)

    async def ainvoke(self, messages):
        from langchain_core.messages import AIMessage

        self.calls.append(messages)
        return AIMessage(content=self.answer)


@pytest.fixture
def gemini(monkeypatch):
    fake = FakeChatModel()
    embed_aliases: dict[str, str] = {}

    async def embed_query(text):
        return fake_vector(embed_aliases.get(text, text))

    async def embed_documents(texts):
        return [fake_vector(embed_aliases.get(t, t)) for t in texts]

    monkeypatch.setattr(llm, "get_chat_model", lambda: fake)
    monkeypatch.setattr(llm, "embed_query", embed_query)
    monkeypatch.setattr(llm, "embed_documents", embed_documents)
    fake.embed_aliases = embed_aliases
    return fake


# ---------------------------------------------------------------- slack

class FakeSlack:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.emails: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.files: dict[str, dict] = {}
        self.file_bytes: dict[str, bytes] = {}
        self._ts = 1000

    def _next_ts(self) -> str:
        self._ts += 1
        return f"{self._ts}.000100"

    def of(self, name):
        return [kwargs for method, kwargs in self.calls if method == name]

    async def open_dm(self, slack_user_id):
        self.calls.append(("open_dm", {"user": slack_user_id}))
        return "D" + slack_user_id

    async def post_message(self, channel, text, blocks=None, thread_ts=None):
        ts = self._next_ts()
        self.calls.append(("post_message", {"channel": channel, "text": text, "blocks": blocks, "thread_ts": thread_ts, "ts": ts}))
        return ts

    async def update_message(self, channel, ts, text, blocks=None):
        self.calls.append(("update_message", {"channel": channel, "ts": ts, "text": text, "blocks": blocks}))

    async def post_ephemeral(self, channel, user, text, thread_ts=None):
        self.calls.append(("post_ephemeral", {"channel": channel, "user": user, "text": text, "thread_ts": thread_ts}))

    async def user_email(self, slack_user_id):
        return self.emails.get(slack_user_id)

    async def user_name(self, slack_user_id):
        return self.names.get(slack_user_id, slack_user_id)

    async def lookup_user_by_email(self, email):
        return next((u for u, e in self.emails.items() if e.lower() == email.lower()), None)

    async def open_view(self, trigger_id, view):
        self.calls.append(("open_view", {"trigger_id": trigger_id, "view": view}))

    async def update_view(self, view_id, view, view_hash=None):
        self.calls.append(("update_view", {"view_id": view_id, "view": view, "hash": view_hash}))

    async def file_info(self, file_id):
        return self.files[file_id]

    async def download_file(self, url):
        return self.file_bytes[url]

    async def respond(self, response_url, payload):
        self.calls.append(("respond", {"response_url": response_url, **payload}))


@pytest.fixture
def slack(monkeypatch):
    from services.slack import handlers, team_handlers

    fake = FakeSlack()
    monkeypatch.setattr(handlers, "slack_client", lambda: fake)
    monkeypatch.setattr(handlers, "_user_cache", {})
    monkeypatch.setattr(team_handlers, "_slack_id_cache", {})
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", SIGNING_SECRET)
    return fake


# ---------------------------------------------------------------- storage

class FakeRawStorage:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []

    def upload(self, path, data, content_type):
        self.objects[path] = (data, content_type)

    def delete(self, path):
        self.deleted.append(path)
        self.objects.pop(path, None)

    def signed_url(self, path, ttl_seconds):
        return f"https://storage.example/{path}?ttl={ttl_seconds}"


@pytest.fixture
def raw_store(monkeypatch):
    from services import raw_storage

    fake = FakeRawStorage()
    monkeypatch.setattr(raw_storage, "get_raw_storage", lambda: fake)
    return fake


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)

"""DB_POOL_SIZE / DB_MAX_OVERFLOW are read by db/session.py and actually
change the engine's pool, on both the LOCAL_DATABASE_URL path (exercised
here) and the Cloud SQL connector path (same create_engine call, same
kwargs — see db/session.py)."""

from core.config import settings
from db import session as db_session


def test_pool_size_and_overflow_are_configurable(database_url, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_DATABASE_URL", database_url)
    monkeypatch.setattr(settings, "INSTANCE_CONNECTION_NAME", None)
    monkeypatch.setattr(settings, "DB_POOL_SIZE", 3)
    monkeypatch.setattr(settings, "DB_MAX_OVERFLOW", 7)
    db_session.get_engine.cache_clear()

    engine = db_session.get_engine()
    try:
        assert engine.pool.size() == 3
        assert engine.pool._max_overflow == 7
    finally:
        engine.dispose()
        db_session.get_engine.cache_clear()


def test_pool_defaults_match_sqlalchemys_own_defaults(database_url, monkeypatch):
    """Unset DB_POOL_SIZE/DB_MAX_OVERFLOW must behave exactly as before this
    change (5 + 10) — existing deployments that don't set these env vars
    shouldn't see their pool size change."""
    monkeypatch.setattr(settings, "LOCAL_DATABASE_URL", database_url)
    monkeypatch.setattr(settings, "INSTANCE_CONNECTION_NAME", None)
    db_session.get_engine.cache_clear()

    engine = db_session.get_engine()
    try:
        assert engine.pool.size() == 5
        assert engine.pool._max_overflow == 10
    finally:
        engine.dispose()
        db_session.get_engine.cache_clear()

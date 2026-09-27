"""Tests for scripts/run_migrations.py: the schema_migrations tracking
table, applying migrations against a fresh database, reconciling against a
database that already has them applied (the pre-runner state every existing
deployment is in), and stop-on-failure/resume behavior.
"""

import pytest
from sqlalchemy import create_engine, text

from scripts.run_migrations import MigrationError, run_migrations


def _versions_in(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version FROM schema_migrations ORDER BY version")).all()
    return [r[0] for r in rows]


def test_runs_all_migrations_against_a_fresh_database(empty_database_url):
    engine = create_engine(empty_database_url)
    try:
        executed = run_migrations(engine)

        assert executed == [
            "0001_init_from_neurix",
            "0002_enterprise_extensions",
            "0003_retrieval_org_scope",
            "0004_share_workflow_indexes",
            "0005_org_slack_workspace",
        ]
        assert _versions_in(engine) == executed

        # The schema is actually usable, not just recorded as applied.
        with engine.connect() as conn:
            assert conn.execute(text("SELECT to_regclass('public.node_pool')")).scalar() is not None
            assert conn.execute(text("SELECT to_regclass('public.orgs')")).scalar() is not None
            assert conn.execute(
                text("SELECT column_name FROM information_schema.columns "
                     "WHERE table_name = 'orgs' AND column_name = 'slack_team_id'")
            ).scalar() == "slack_team_id"

        # Re-running is a clean no-op: nothing left to apply.
        assert run_migrations(engine) == []
    finally:
        engine.dispose()


def test_reconciles_a_database_already_migrated_before_the_runner_existed(database_url):
    """`database_url` (see conftest) applies every migrations/*.sql file
    directly, with no schema_migrations table — exactly the state every
    database migrated by hand via the old runbook is in. The runner must
    not try to blindly re-run 0001 (not idempotent) here."""
    engine = create_engine(database_url)
    try:
        executed = run_migrations(engine)

        # 0001 is reconciled (its table already existed) rather than
        # re-executed; 0002-0005 are idempotent so they're simply run again
        # as harmless no-ops and recorded.
        assert "0001_init_from_neurix" not in executed
        assert executed == [
            "0002_enterprise_extensions",
            "0003_retrieval_org_scope",
            "0004_share_workflow_indexes",
            "0005_org_slack_workspace",
        ]
        assert _versions_in(engine) == [
            "0001_init_from_neurix",
            "0002_enterprise_extensions",
            "0003_retrieval_org_scope",
            "0004_share_workflow_indexes",
            "0005_org_slack_workspace",
        ]

        # Re-running now that everything is tracked: nothing to do.
        assert run_migrations(engine) == []
    finally:
        engine.dispose()


def test_stops_on_failure_and_resumes_after_a_fix(empty_database_url, tmp_path, monkeypatch):
    import scripts.run_migrations as runner

    good = tmp_path / "0001_ok.sql"
    good.write_text("CREATE TABLE ok_marker (id int PRIMARY KEY);")
    bad = tmp_path / "0002_bad.sql"
    bad.write_text("THIS IS NOT VALID SQL;")

    monkeypatch.setattr(runner, "MIGRATIONS_DIR", tmp_path)

    engine = create_engine(empty_database_url)
    try:
        with pytest.raises(MigrationError) as exc_info:
            run_migrations(engine)
        assert exc_info.value.version == "0002_bad"

        # 0001 stayed committed and recorded; 0002 was rolled back and not recorded.
        assert _versions_in(engine) == ["0001_ok"]
        with engine.connect() as conn:
            assert conn.execute(text("SELECT to_regclass('public.ok_marker')")).scalar() is not None

        # Fix the failing migration and re-run: only the fixed 0002 applies,
        # 0001 is not re-attempted.
        bad.write_text("CREATE TABLE fixed_marker (id int PRIMARY KEY);")
        executed = run_migrations(engine)
        assert executed == ["0002_bad"]
        assert _versions_in(engine) == ["0001_ok", "0002_bad"]
        with engine.connect() as conn:
            assert conn.execute(text("SELECT to_regclass('public.fixed_marker')")).scalar() is not None
    finally:
        engine.dispose()

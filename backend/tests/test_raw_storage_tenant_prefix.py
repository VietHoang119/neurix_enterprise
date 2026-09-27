"""object_path() includes TENANT_ID when set, and falls back to the old
unprefixed shape when it isn't (tests/test_team_share.py's
test_ingest_keeps_original_in_raw_storage exercises that unprefixed
default end-to-end through the Slack ingest flow; these are the direct
unit-level cases for the prefix itself)."""

from core.config import settings
from services.raw_storage import object_path


def test_path_has_no_tenant_prefix_when_tenant_id_is_unset(monkeypatch):
    monkeypatch.setattr(settings, "TENANT_ID", None)
    path = object_path("user-1", "report.pdf", now=0)
    assert path.startswith("raw/user-1/")
    assert not path.startswith("/")


def test_path_is_prefixed_with_tenant_id_when_set(monkeypatch):
    monkeypatch.setattr(settings, "TENANT_ID", "acme")
    path = object_path("user-1", "report.pdf", now=0)
    assert path.startswith("acme/raw/user-1/")
    assert path.endswith("-report.pdf")


def test_different_tenants_produce_disjoint_prefixes(monkeypatch):
    monkeypatch.setattr(settings, "TENANT_ID", "acme")
    acme_path = object_path("user-1", "report.pdf", now=0)
    monkeypatch.setattr(settings, "TENANT_ID", "globex")
    globex_path = object_path("user-1", "report.pdf", now=0)

    assert acme_path.split("/", 1)[0] == "acme"
    assert globex_path.split("/", 1)[0] == "globex"
    assert acme_path != globex_path

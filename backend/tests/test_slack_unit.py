import hashlib
import hmac
import time

from services.slack.edit_parser import apply_edit, parse_edit
from services.slack.signature import is_valid_slack_signature

SECRET = "shh"


def _sign(body: bytes, ts: str, secret: str = SECRET) -> str:
    return "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()


def test_signature_accepts_valid_request():
    ts = str(int(time.time()))
    body = b"command=%2Fneurix&text=save+hi"
    assert is_valid_slack_signature(signing_secret=SECRET, body=body, timestamp=ts, signature=_sign(body, ts))


def test_signature_rejects_tampered_body_wrong_secret_and_missing_parts():
    ts = str(int(time.time()))
    body = b"payload=confirm"
    sig = _sign(body, ts)
    assert not is_valid_slack_signature(signing_secret=SECRET, body=body + b"x", timestamp=ts, signature=sig)
    assert not is_valid_slack_signature(signing_secret="other", body=body, timestamp=ts, signature=sig)
    assert not is_valid_slack_signature(signing_secret=None, body=body, timestamp=ts, signature=sig)
    assert not is_valid_slack_signature(signing_secret=SECRET, body=body, timestamp=None, signature=sig)
    assert not is_valid_slack_signature(signing_secret=SECRET, body=body, timestamp=ts, signature=None)
    assert not is_valid_slack_signature(signing_secret=SECRET, body=body, timestamp="nan", signature=sig)


def test_signature_rejects_replayed_old_request():
    ts = str(int(time.time()) - 10 * 60)
    body = b"payload=confirm"
    assert not is_valid_slack_signature(signing_secret=SECRET, body=body, timestamp=ts, signature=_sign(body, ts))


def test_parse_edit_splits_key_commands_from_summary():
    adds, removes, summary = parse_edit("New summary line one\n+key: Rust\n - KEY :  Python \nline two")
    assert adds == ["rust"]
    assert removes == ["python"]
    assert summary == "New summary line one\nline two"


def test_parse_edit_keys_only_leaves_summary_untouched():
    assert parse_edit("+key: a\n-key: b") == (["a"], ["b"], None)


def _draft_preview():
    return {
        "preview": {"title": "T", "summary": "old", "keys": ["python", "fastapi"]},
        "node_payload": {"title": "T", "summary": "old", "summary_vec": [0.1]},
        "keys_payload": {"existing_keys": ["python"], "new_keys": ["fastapi"]},
        "summary_edited": False,
    }


def test_apply_edit_updates_summary_and_keys():
    payload = _draft_preview()
    changes = apply_edit(payload, "Brand new summary\n+key: pydantic\n-key: python")
    assert payload["preview"]["summary"] == "Brand new summary"
    assert payload["node_payload"]["summary"] == "Brand new summary"
    assert payload["summary_edited"] is True
    assert payload["preview"]["keys"] == ["fastapi", "pydantic"]
    # a removed suggestion is no longer "existing"; typed keys are new
    assert payload["keys_payload"] == {"existing_keys": [], "new_keys": ["fastapi", "pydantic"]}
    assert changes == ["summary updated", "removed key 'python'", "added key 'pydantic'"]


def test_apply_edit_noop_reports_no_changes():
    payload = _draft_preview()
    assert apply_edit(payload, "+key: python\n-key: nothing") == []
    assert payload["summary_edited"] is False

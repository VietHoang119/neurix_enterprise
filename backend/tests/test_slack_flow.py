"""End-to-end Slack flows against the real schema (see conftest.py)."""

import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import urlencode

import pytest
from sqlalchemy import text

from conftest import SIGNING_SECRET, fake_vector
from db.vector import to_pgvector
from services import ingest

SLACK_USER = "U1"
EMAIL = "hoang@example.com"


def _signed(body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(SIGNING_SECRET.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


def post_form(client, path, fields, sign=True):
    body = urlencode(fields).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded", **(_signed(body) if sign else {})}
    return client.post(path, content=body, headers=headers)


def post_json(client, path, payload, **extra_headers):
    body = json.dumps(payload).encode()
    return client.post(path, content=body, headers={"Content-Type": "application/json", **_signed(body), **extra_headers})


def click(client, action_id, draft_id, channel, ts, user=SLACK_USER):
    payload = {
        "type": "block_actions",
        "user": {"id": user},
        "container": {"channel_id": channel, "message_ts": ts},
        "actions": [{"action_id": action_id, "value": draft_id}],
    }
    return post_form(client, "/slack/interactions", {"payload": json.dumps(payload)})


def thread_reply(client, channel, thread_ts, reply, user=SLACK_USER):
    return post_json(
        client,
        "/slack/events",
        {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel_type": "im",
                "channel": channel,
                "user": user,
                "text": reply,
                "ts": "9999.0001",
                "thread_ts": thread_ts,
            },
        },
    )


@pytest.fixture
def world(db, gemini, slack):
    """A signed-in user who already has one READY note tagged 'python'."""
    user_id = str(uuid.uuid4())
    with db.begin() as conn:
        conn.execute(text("INSERT INTO auth.users (id, email) VALUES (:id, :email)"), {"id": user_id, "email": EMAIL})
        old_node = conn.execute(
            text(
                "INSERT INTO node_pool (user_id, title, summary, summary_vec, status) "
                "VALUES (:u, 'Python basics', 'python is a language', CAST(:v AS vector), 'READY') RETURNING id"
            ),
            {"u": user_id, "v": to_pgvector(fake_vector("python is a language"))},
        ).scalar_one()
        key_id = conn.execute(
            text("INSERT INTO key_pool (user_id, key_text, key_vec) VALUES (:u, 'python', CAST(:v AS vector)) RETURNING id"),
            {"u": user_id, "v": to_pgvector(fake_vector("python"))},
        ).scalar_one()
        conn.execute(text("INSERT INTO node_keys (node_id, key_id) VALUES (:n, :k)"), {"n": old_node, "k": key_id})

    slack.emails[SLACK_USER] = EMAIL
    gemini.structured = {
        "TitleSummaryResult": {
            "title": "FastAPI notes",
            "summary": "fastapi is a python web framework",
            "generated_keys": ["fastapi", "python"],
        },
        "KeysResult": {"all_keys": ["Python", "FastAPI"]},
    }
    return {"user_id": user_id, "old_node": str(old_node), "db": db, "gemini": gemini, "slack": slack}


def _save(client, world, note="FastAPI is a Python web framework"):
    res = post_form(
        client,
        "/slack/commands",
        {"command": "/neurix", "text": f"save {note}", "user_id": SLACK_USER, "channel_id": "C1", "response_url": "https://hooks/1"},
    )
    assert res.status_code == 200
    assert res.json() == {"response_type": "ephemeral", "text": "⏳ Extracting..."}
    card = world["slack"].of("post_message")[-1]
    draft_id = card["blocks"][-1]["elements"][0]["value"]
    return card, draft_id


def _draft(db, draft_id):
    with db.connect() as conn:
        return conn.execute(text("SELECT preview FROM node_drafts WHERE id = :id"), {"id": draft_id}).scalar_one_or_none()


def test_save_edit_confirm_end_to_end(client, world, monkeypatch):
    db, slack = world["db"], world["slack"]

    card, draft_id = _save(client, world)
    assert card["channel"] == "DU1"  # preview goes to the user's DM
    assert card["blocks"][0]["text"]["text"] == "FastAPI notes"
    assert "`python`, `fastapi`" in card["blocks"][2]["elements"][0]["text"]
    assert slack.of("respond")[-1]["text"].startswith("📬 Preview ready")
    preview = _draft(db, draft_id)
    assert preview["keys_payload"] == {"existing_keys": ["python"], "new_keys": ["fastapi"]}
    assert preview["slack"] == {"user": SLACK_USER, "channel": "DU1", "ts": card["ts"]}

    # Edit: instructions, then a free-text reply in the card's thread.
    click(client, "neurix_edit", draft_id, "DU1", card["ts"])
    assert "`+key: X` / `-key: Y`" in slack.of("post_ephemeral")[-1]["text"]
    world["gemini"].embed_aliases["pythonic"] = "python"  # typed key ~= existing key
    thread_reply(client, "DU1", card["ts"], "FastAPI builds typed APIs in Python\n+key: pythonic")
    preview = _draft(db, draft_id)
    assert preview["preview"]["summary"] == "FastAPI builds typed APIs in Python"
    assert preview["preview"]["keys"] == ["python", "fastapi", "pythonic"]
    assert preview["summary_edited"] is True
    assert slack.of("update_message")[-1]["blocks"][1]["text"]["text"] == "FastAPI builds typed APIs in Python"
    assert slack.of("post_message")[-1]["text"].startswith("✏️ Preview updated: summary updated, added key 'pythonic'")

    # Someone else's reply in the thread is ignored.
    n_calls = len(slack.calls)
    thread_reply(client, "DU1", card["ts"], "-key: python", user="U2")
    assert len(slack.calls) == n_calls

    # Confirm: node is PROCESSING when the background job starts.
    seen_status = {}
    real_process = ingest.process_node

    async def spy(confirmed):
        with db.connect() as conn:
            seen_status["at_start"] = conn.execute(
                text("SELECT status FROM node_pool WHERE id = :id"), {"id": confirmed.node_id}
            ).scalar_one()
        return await real_process(confirmed)

    monkeypatch.setattr(ingest, "process_node", spy)
    click(client, "neurix_confirm", draft_id, "DU1", card["ts"])
    assert seen_status["at_start"] == "PROCESSING"

    updates = [u["text"] for u in slack.of("update_message")[-2:]]
    assert updates == [
        "⏳ Saved — processing connections...",
        "✅ Saved — 1 new connection, key 'pythonic' merged into 'python'",
    ]

    with db.connect() as conn:
        node = conn.execute(
            text("SELECT id, status, summary, summary_vec IS NOT NULL AS has_vec, origin FROM node_pool WHERE title = 'FastAPI notes'")
        ).one()
        assert (node.status, node.summary, node.has_vec, node.origin) == ("READY", "FastAPI builds typed APIs in Python", True, "slack")
        keys = conn.execute(
            text("SELECT kp.key_text, kp.key_vec IS NOT NULL FROM node_keys nk JOIN key_pool kp ON kp.id = nk.key_id WHERE nk.node_id = :n ORDER BY 1"),
            {"n": node.id},
        ).all()
        assert [tuple(k) for k in keys] == [("fastapi", True), ("python", True)]
        assert conn.execute(text("SELECT count(*) FROM key_pool WHERE key_text = 'pythonic'")).scalar_one() == 0
        conns = conn.execute(
            text("SELECT target_node, score, is_share, user_id FROM connection WHERE source_node = :n"), {"n": node.id}
        ).all()
        assert [(str(c.target_node), c.score, c.is_share, str(c.user_id)) for c in conns] == [
            (world["old_node"], 1.0, False, world["user_id"])
        ]
        assert conn.execute(text("SELECT count(*) FROM node_drafts")).scalar_one() == 0

    # A second click on the same card does nothing but say so.
    click(client, "neurix_confirm", draft_id, "DU1", card["ts"])
    assert "already confirmed or discarded" in slack.of("post_ephemeral")[-1]["text"]
    with db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM node_pool")).scalar_one() == 2


def test_discard_leaves_no_node(client, world):
    db, slack = world["db"], world["slack"]
    card, draft_id = _save(client, world)

    click(client, "neurix_discard", draft_id, "DU1", card["ts"], user="U2")  # not the owner
    assert "Only the person who created" in slack.of("post_ephemeral")[-1]["text"]
    assert _draft(db, draft_id) is not None

    click(client, "neurix_discard", draft_id, "DU1", card["ts"])
    assert slack.of("update_message")[-1]["text"] == "Discarded."
    assert _draft(db, draft_id) is None
    with db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM node_pool")).scalar_one() == 1  # just the seed
        assert conn.execute(text("SELECT count(*) FROM key_pool WHERE key_text = 'fastapi'")).scalar_one() == 0


def test_processing_failure_marks_node_failed(client, world, monkeypatch):
    db, slack = world["db"], world["slack"]
    card, draft_id = _save(client, world)

    async def broken(texts):
        raise RuntimeError("embedding quota exceeded")

    from core import llm

    monkeypatch.setattr(llm, "embed_documents", broken)
    click(client, "neurix_confirm", draft_id, "DU1", card["ts"])
    assert slack.of("update_message")[-1]["text"].startswith("⚠️ Saved, but processing failed")
    with db.connect() as conn:
        row = conn.execute(text("SELECT status, metadata->>'error' AS error FROM node_pool WHERE title = 'FastAPI notes'")).one()
    assert row.status == "FAILED"
    assert "embedding quota exceeded" in row.error


def test_confirm_insert_failure_is_reported(client, world, monkeypatch):
    card, draft_id = _save(client, world)

    def boom(draft):
        raise RuntimeError("db down")

    monkeypatch.setattr(ingest, "confirm_draft", boom)
    click(client, "neurix_confirm", draft_id, "DU1", card["ts"])
    assert world["slack"].of("update_message")[-1]["text"] == "⚠️ Couldn't save this note. Please send it again."


def test_unlinked_slack_user_is_told_to_sign_in(client, world):
    world["slack"].emails[SLACK_USER] = "someone-else@example.com"
    post_form(
        client,
        "/slack/commands",
        {"command": "/neurix", "text": "save hello", "user_id": SLACK_USER, "channel_id": "C1", "response_url": "https://hooks/1"},
    )
    assert "Sign in to the Neurix web app" in world["slack"].of("respond")[-1]["text"]
    assert world["slack"].of("post_message") == []


@pytest.mark.parametrize("path", ["/slack/commands", "/slack/interactions", "/slack/events"])
def test_unsigned_or_forged_requests_are_rejected(client, world, path):
    body = urlencode({"payload": json.dumps({"type": "block_actions", "actions": [{"action_id": "neurix_confirm", "value": "x"}]})}).encode()
    assert client.post(path, content=body).status_code == 401
    forged = {"X-Slack-Request-Timestamp": str(int(time.time())), "X-Slack-Signature": "v0=" + "0" * 64}
    assert client.post(path, content=body, headers=forged).status_code == 401
    assert world["slack"].calls == []


def test_events_url_verification_and_retries(client, world):
    assert post_json(client, "/slack/events", {"type": "url_verification", "challenge": "abc"}).json() == {"challenge": "abc"}
    card, draft_id = _save(client, world)
    n_calls = len(world["slack"].calls)
    thread_reply_payload = {
        "type": "event_callback",
        "event": {"type": "message", "channel_type": "im", "channel": "DU1", "user": SLACK_USER,
                  "text": "+key: x", "ts": "9.1", "thread_ts": card["ts"]},
    }
    post_json(client, "/slack/events", thread_reply_payload, **{"X-Slack-Retry-Num": "1"})
    assert len(world["slack"].calls) == n_calls


def _share_file(client, file_id, channel="DU1"):
    return post_json(
        client,
        "/slack/events",
        {"type": "event_callback", "event": {"type": "file_shared", "file_id": file_id, "user_id": SLACK_USER, "channel_id": channel}},
    )


def test_pdf_shared_in_dm_goes_through_preview(client, world):
    db, slack, gemini = world["db"], world["slack"], world["gemini"]
    slack.files["F1"] = {"id": "F1", "name": "report.pdf", "filetype": "pdf", "mimetype": "application/pdf",
                         "size": 1234, "url_private_download": "https://files/F1"}
    slack.file_bytes["https://files/F1"] = b"%PDF-1.4 fake"
    gemini.answer = "FastAPI adoption report. Teams use fastapi with python."

    _share_file(client, "F1")

    placeholder = slack.of("post_message")[-1]
    assert placeholder["text"] == "⏳ Extracting report.pdf..."
    card = slack.of("update_message")[-1]
    assert card["ts"] == placeholder["ts"] and card["blocks"][0]["text"]["text"] == "FastAPI notes"
    # The PDF bytes went to Gemini as an inline media part.
    media = gemini.calls[0][0].content[1]
    assert media == {"type": "media", "mime_type": "application/pdf", "data": b"%PDF-1.4 fake"}
    with db.connect() as conn:
        draft = conn.execute(text("SELECT raw_content, source, preview FROM node_drafts")).one()
    assert draft.raw_content == "FastAPI adoption report. Teams use fastapi with python."
    assert draft.source == "report.pdf"
    assert draft.preview["slack"]["ts"] == placeholder["ts"]


def test_unsupported_oversized_and_channel_files(client, world, monkeypatch):
    slack = world["slack"]
    slack.files["Z"] = {"id": "Z", "name": "a.zip", "filetype": "zip", "mimetype": "application/zip", "size": 10, "url_private": "u"}
    _share_file(client, "Z")
    assert "Unsupported file type" in slack.of("update_message")[-1]["text"]

    slack.files["B"] = {"id": "B", "name": "big.pdf", "filetype": "pdf", "mimetype": "application/pdf",
                        "size": 25 * 1024 * 1024, "url_private": "u"}
    _share_file(client, "B")
    assert "too large" in slack.of("update_message")[-1]["text"]

    n_calls = len(slack.calls)
    _share_file(client, "B", channel="C123")  # shared in a channel, not a DM
    assert len(slack.calls) == n_calls


def _ask(client, question):
    res = post_form(
        client,
        "/slack/commands",
        {"command": "/neurix", "text": f"ask {question}", "user_id": SLACK_USER, "channel_id": "C1", "response_url": "https://hooks/2"},
    )
    assert res.json()["text"] == "🔎 Searching your notes..."


def test_ask_answers_with_citations_and_logs(client, world):
    db, slack, gemini = world["db"], world["slack"], world["gemini"]
    gemini.answer = "Python is a **language** [1]."

    _ask(client, "what is python")

    reply = slack.of("respond")[-1]
    assert reply["replace_original"] is True
    assert reply["text"] == "*Q:* what is python\n\nPython is a *language* [1].\n\n_Sources:_ [1] Python basics"
    system_prompt = gemini.calls[-1][0].content
    assert "[1] Title: Python basics" in system_prompt and "Keys: python" in system_prompt
    with db.connect() as conn:
        log = conn.execute(text("SELECT user_id, question, used_node_ids FROM retrieval_log")).one()
    assert (str(log.user_id), log.question) == (world["user_id"], "what is python")
    assert [str(i) for i in log.used_node_ids] == [world["old_node"]]


def test_ask_without_relevant_notes_still_logs(client, world):
    db, slack = world["db"], world["slack"]
    _ask(client, "quarterly revenue forecast")
    assert "couldn't find anything in your saved notes" in slack.of("respond")[-1]["text"]
    with db.connect() as conn:
        assert conn.execute(text("SELECT cardinality(used_node_ids) FROM retrieval_log")).scalar_one() == 0

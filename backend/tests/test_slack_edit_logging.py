"""The Edit button through the real SlackClient (only httpx's transport is
faked), and proof that a failing Slack call is logged with its traceback
instead of vanishing inside the background task."""

import logging

import httpx
import pytest

from services.slack import api as slack_api
from services.slack import handlers
from services.slack.api import SlackClient
from test_slack_flow import _save, click, world  # noqa: F401


def _real_client(monkeypatch, respond):
    real = httpx.AsyncClient
    monkeypatch.setattr(slack_api.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(handlers, "slack_client", lambda: SlackClient(token="xoxb-test"))


def test_edit_posts_ephemeral_in_card_thread(client, world, monkeypatch):
    card, draft_id = _save(client, world)
    sent = []

    def respond(req):
        sent.append(req)
        return httpx.Response(200, json={"ok": True})

    _real_client(monkeypatch, respond)
    click(client, "neurix_edit", draft_id, card["channel"], card["ts"])

    assert [str(r.url) for r in sent] == ["https://slack.com/api/chat.postEphemeral"]
    assert b'"thread_ts":"%s"' % card["ts"].encode() in sent[0].content


@pytest.mark.parametrize(
    "respond, error",
    [
        (lambda req: httpx.Response(200, json={"ok": False, "error": "channel_not_found"}), "channel_not_found"),
        (lambda req: httpx.Response(502, text="<html>Bad gateway</html>"), "non-JSON (HTTP 502)"),
        (lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=req)), "slow"),
    ],
)
def test_edit_failure_is_logged_with_traceback(client, world, monkeypatch, caplog, respond, error):
    card, draft_id = _save(client, world)
    _real_client(monkeypatch, respond)
    caplog.set_level(logging.INFO)

    click(client, "neurix_edit", draft_id, card["channel"], card["ts"])

    failed = [r for r in caplog.records if r.getMessage().startswith("SLACK_ACTION_FAILED")]
    assert len(failed) == 1 and draft_id in failed[0].getMessage()
    assert failed[0].exc_info and error in str(failed[0].exc_info[1])
    assert any(r.getMessage().startswith("SLACK_EDIT_EPHEMERAL_START") for r in caplog.records)
    assert not any(r.getMessage().startswith("SLACK_EDIT_EPHEMERAL_DONE") for r in caplog.records)

"""Web UI polish: each team's Slack workspace link (from slash commands and
the OAuth install callback) and node keys on both graphs."""

from sqlalchemy import text

from core.config import settings
from routers import slack as slack_router
from services import team as team_service
from services.slack import team_handlers
from test_slack_flow import post_form
from test_team_share import A, B, add_node, graph, make_team, people, share_team  # noqa: F401  (people is a fixture)


def command_from(client, user, text_, team_id, team_domain):
    res = post_form(
        client,
        "/slack/commands",
        {"command": "/neurix", "text": text_, "user_id": user, "channel_id": "C1", "team_id": team_id,
         "team_domain": team_domain, "response_url": f"https://hooks/{user}", "trigger_id": "T1"},
    )
    assert res.status_code == 200


def workspace(client, user_id):
    return (graph(client, "/team", user_id)["org"] or {}).get("slack_workspace_url")


def test_workspace_url_builder():
    assert team_service.slack_workspace_url("T024BE7LD", "acme") == "https://acme.slack.com/"
    assert team_service.slack_workspace_url("T024BE7LD") == "https://app.slack.com/client/T024BE7LD"
    # Never put an unexpected value into a link.
    assert team_service.slack_workspace_url("T024BE7LD", "evil.com/x") == "https://app.slack.com/client/T024BE7LD"
    assert team_service.slack_workspace_url("javascript:alert(1)", "acme") is None


def test_team_is_linked_to_the_workspace_it_was_created_in(client, people):
    make_team(client, people, B)  # teams made before 0005: no team_id on those commands
    assert workspace(client, people[A]) is None

    command_from(client, B, "stats", "T0ACME", "acme")
    assert workspace(client, people[A]) == "https://acme.slack.com/"
    assert workspace(client, people[B]) == "https://acme.slack.com/"

    # First one wins: a command from another workspace doesn't re-point it.
    command_from(client, A, "team list", "T0OTHER", "other")
    assert workspace(client, people[A]) == "https://acme.slack.com/"


def test_team_create_links_in_the_same_request(client, people):
    command_from(client, A, "team create Platform", "T0ACME", "acme")
    assert workspace(client, people[A]) == "https://acme.slack.com/"


def test_command_without_a_team_links_nothing(client, people):
    command_from(client, A, "stats", "T0ACME", "acme")
    with people["db"].connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM orgs")).scalar_one() == 0


def test_oauth_callback_links_the_installers_team(client, people, monkeypatch):
    make_team(client, people, B)
    command_from(client, A, "stats", "T0OLD", "old")
    monkeypatch.setattr(settings, "SLACK_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "SLACK_CLIENT_SECRET", "csecret")
    seen = {}

    async def fake_oauth_access(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "access_token": "xoxb-new", "team": {"id": "T0NEW", "name": "New"}, "authed_user": {"id": B}}

    monkeypatch.setattr(slack_router, "oauth_access", fake_oauth_access)
    monkeypatch.setattr(team_handlers, "SlackClient", lambda token: people["slack"])

    res = client.get("/slack/oauth/callback", params={"code": "abc"}, follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == f"{settings.FRONTEND_URL}/?slack_install=linked"
    assert seen["code"] == "abc" and seen["client_secret"] == "csecret"
    # Installing is explicit, so it replaces the earlier link.
    assert workspace(client, people[A]) == "https://app.slack.com/client/T0NEW"


def test_oauth_callback_outcomes(client, people, monkeypatch):
    assert client.get("/slack/oauth/callback", params={"code": "abc"}).status_code == 404  # not configured

    monkeypatch.setattr(settings, "SLACK_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "SLACK_CLIENT_SECRET", "csecret")
    res = client.get("/slack/oauth/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert res.headers["location"].endswith("?slack_install=cancelled")

    async def failing(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(slack_router, "oauth_access", failing)
    res = client.get("/slack/oauth/callback", params={"code": "abc"}, follow_redirects=False)
    assert res.headers["location"].endswith("?slack_install=error")

    async def ok(**kwargs):  # the installer has no team yet
        return {"ok": True, "access_token": "x", "team": {"id": "T0NEW"}, "authed_user": {"id": A}}

    monkeypatch.setattr(slack_router, "oauth_access", ok)
    monkeypatch.setattr(team_handlers, "SlackClient", lambda token: people["slack"])
    res = client.get("/slack/oauth/callback", params={"code": "abc"}, follow_redirects=False)
    assert res.headers["location"].endswith("?slack_install=installed")


def test_graphs_carry_each_nodes_keys(client, people):
    make_team(client, people, B)
    x = add_node(people["db"], people[A], "FastAPI tips", ["python", "fastapi"])
    y = add_node(people["db"], people[B], "Django notes", ["python"])
    share_team(client, A, x)
    share_team(client, B, y)

    personal = {n["id"]: n["keys"] for n in graph(client, "/graph", people[A])["nodes"]}
    assert personal == {x: ["fastapi", "python"]}
    team = {n["id"]: n["keys"] for n in graph(client, "/team/graph", people[A])["nodes"]}
    assert team == {x: ["fastapi", "python"], y: ["python"]}

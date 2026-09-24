"""Week 3 flows against the real schema: team membership, person share with
Accept/Decline, team share, persisted share connections, notifications,
stats, raw-file access."""

import json
import uuid

import pytest
from sqlalchemy import text

from conftest import fake_vector
from core.auth import get_current_uid
from db.vector import to_pgvector
from services import share as share_service
from services.slack import share_blocks as sb
from services.slack.team_handlers import match_node, parse_raw_access
from test_slack_flow import click, post_form

A, B, C = "UA", "UB", "UC"


# ---------------------------------------------------------------- helpers

@pytest.fixture
def people(db, gemini, slack):
    """Three Slack users; A and B have signed in to the web app, C never has."""
    ids = {}
    with db.begin() as conn:
        for slack_user, email, name in [(A, "a@example.com", "Alice"), (B, "b@example.com", "Bob")]:
            user_id = str(uuid.uuid4())
            conn.execute(text("INSERT INTO auth.users (id, email) VALUES (:id, :email)"), {"id": user_id, "email": email})
            ids[slack_user] = user_id
            slack.emails[slack_user], slack.names[slack_user] = email, name
    slack.emails[C], slack.names[C] = "c@example.com", "Carol"
    return {"db": db, "slack": slack, "gemini": gemini, **ids}


def add_node(db, user_id, title, keys, *, raw_path=None, summary=None):
    summary = summary or f"{title} " + " ".join(keys)
    with db.begin() as conn:
        node_id = conn.execute(
            text(
                "INSERT INTO node_pool (user_id, title, summary, summary_vec, status, metadata) "
                "VALUES (:u, :t, :s, CAST(:v AS vector), 'READY', CAST(:m AS jsonb)) RETURNING id"
            ),
            {
                "u": user_id,
                "t": title,
                "s": summary,
                "v": to_pgvector(fake_vector(summary)),
                "m": json.dumps({"raw_storage_path": raw_path} if raw_path else {}),
            },
        ).scalar_one()
        for key in keys:
            key_id = conn.execute(
                text(
                    "INSERT INTO key_pool (user_id, key_text, key_vec) VALUES (:u, :k, CAST(:v AS vector)) "
                    "ON CONFLICT (user_id, key_text) DO UPDATE SET key_text = excluded.key_text RETURNING id"
                ),
                {"u": user_id, "k": key, "v": to_pgvector(fake_vector(key))},
            ).scalar_one()
            conn.execute(text("INSERT INTO node_keys (node_id, key_id) VALUES (:n, :k)"), {"n": node_id, "k": key_id})
    return str(node_id)


def command(client, user, text_, trigger="T1"):
    res = post_form(
        client,
        "/slack/commands",
        {"command": "/neurix", "text": text_, "user_id": user, "channel_id": "C1",
         "response_url": f"https://hooks/{user}", "trigger_id": trigger},
    )
    assert res.status_code == 200
    return res.json()


def submit(client, user, callback_id, values):
    state = {block: {block: value} for block, value in values.items()}
    payload = {"type": "view_submission", "user": {"id": user}, "view": {"callback_id": callback_id, "state": {"values": state}}}
    res = post_form(client, "/slack/interactions", {"payload": json.dumps(payload)})
    assert res.status_code == 200 and res.json() == {}


def dms_to(slack, user):
    return [m for m in slack.of("post_message") if m["channel"] == "D" + user]


def last_reply(slack, user):
    return [r for r in slack.of("respond") if r["response_url"] == f"https://hooks/{user}"][-1]


def make_team(client, people, *members):
    command(client, A, "team create Platform")
    for member in members:
        submit(client, A, sb.TEAM_INVITE_MODAL, {sb.MEMBER_BLOCK: {"selected_user": member}})


def share_to(client, sender, node_id, recipient, message=None):
    values = {sb.NODE_BLOCK: {"selected_option": {"value": node_id}}, sb.RECIPIENT_BLOCK: {"selected_user": recipient}}
    if message:
        values[sb.MESSAGE_BLOCK] = {"value": message}
    submit(client, sender, sb.SHARE_MODAL, values)


def share_team(client, sender, node_id, scope="admin", targets=()):
    submit(
        client,
        sender,
        sb.SHARE_TEAM_MODAL,
        {
            sb.NODE_BLOCK: {"selected_option": {"value": node_id}},
            sb.SCOPE_BLOCK: {"selected_option": {"value": scope}},
            sb.TARGETS_BLOCK: {"selected_users": list(targets)},
        },
    )


def invite_card(slack, recipient):
    card = [m for m in dms_to(slack, recipient) if m["blocks"] and m["blocks"][-1]["type"] == "actions"][-1]
    return card, card["blocks"][-1]["elements"][0]["value"]


def fetch(db, sql, **params):
    with db.connect() as conn:
        return conn.execute(text(sql), params).all()


def notifications(db, user_id):
    return [(r.type, r.message) for r in fetch(db, "SELECT type, message FROM notifications WHERE user_id = :u ORDER BY created_at", u=user_id)]


def graph(client, path, user_id):
    client.app.dependency_overrides[get_current_uid] = lambda: user_id
    try:
        return client.get(path).json()
    finally:
        client.app.dependency_overrides.clear()


def pairs(g):
    return [tuple(sorted((c["source_id"], c["target_id"]))) for c in g["connections"]]


# ---------------------------------------------------------------- team

def test_team_create_invite_and_list(client, people):
    db, slack = people["db"], people["slack"]

    assert command(client, A, "team invite")["text"] == "⏳ Opening..."
    assert "not in a team yet" in last_reply(slack, A)["text"]

    command(client, A, "team create Platform")
    assert "Created team *Platform*" in last_reply(slack, A)["text"]
    command(client, A, "team create Other")
    assert "already in team *Platform*" in last_reply(slack, A)["text"]

    command(client, A, "team invite", trigger="T9")
    view = slack.of("open_view")[-1]
    assert view["trigger_id"] == "T9" and view["view"]["callback_id"] == sb.TEAM_INVITE_MODAL
    assert view["view"]["blocks"][1]["element"]["type"] == "users_select"

    submit(client, A, sb.TEAM_INVITE_MODAL, {sb.MEMBER_BLOCK: {"selected_user": B}})
    assert dms_to(slack, A)[-1]["text"] == "✅ Added <@UB> to *Platform*."
    assert "added you to team *Platform*" in dms_to(slack, B)[-1]["text"]

    # C has never signed in to the web app: the inviter is told exactly that.
    submit(client, A, sb.TEAM_INVITE_MODAL, {sb.MEMBER_BLOCK: {"selected_user": C}})
    assert "never signed in to the Neurix web app with c@example.com" in dms_to(slack, A)[-1]["text"]

    submit(client, A, sb.TEAM_INVITE_MODAL, {sb.MEMBER_BLOCK: {"selected_user": B}})
    assert dms_to(slack, A)[-1]["text"] == "<@UB> is already in your team."

    # Members can't add people.
    command(client, B, "team invite")
    assert last_reply(slack, B)["text"] == "Only a team admin can add members."

    command(client, B, "team list")
    listing = last_reply(slack, B)["text"]
    assert "*Team Platform* — 2 member(s)" in listing
    assert "Alice (a@example.com) — admin" in listing and "Bob (b@example.com)" in listing
    rows = fetch(db, "SELECT uid, role FROM org_members ORDER BY role")
    assert [(str(r.uid), r.role) for r in rows] == [(people[A], "admin"), (people[B], "member")]


# ---------------------------------------------------------------- person share

def test_person_share_accept_end_to_end(client, people):
    db, slack, gemini = people["db"], people["slack"], people["gemini"]
    x = add_node(db, people[A], "FastAPI tips", ["python", "fastapi"], raw_path="raw/a/x.txt")
    y = add_node(db, people[B], "Python style", ["python", "fastapi"])
    add_node(db, people[B], "Sourdough", ["baking"])

    command(client, A, "share", trigger="T2")
    view = slack.of("open_view")[-1]["view"]
    assert view["callback_id"] == sb.SHARE_MODAL
    assert [o["value"] for o in view["blocks"][0]["element"]["options"]] == [x]

    share_to(client, A, x, B, message="thought you'd like this")
    share = fetch(db, "SELECT id, status, is_active, permission, shared_user_id, org_id, metadata FROM share_node")[0]
    assert (share.status, share.is_active, share.permission, str(share.shared_user_id), share.org_id) == (
        "pending", False, "view", people[B], None
    )
    card, share_id = invite_card(slack, B)
    assert card["blocks"][0]["text"]["text"] == "<@UA> wants to share a note with you: *FastAPI tips*"
    assert "thought you'd like this" in card["blocks"][1]["text"]["text"]
    assert [e["action_id"] for e in card["blocks"][-1]["elements"]] == [sb.ACCEPT_ACTION, sb.DECLINE_ACTION]
    assert share.metadata["invite_dm"] == {"channel": "DUB", "ts": card["ts"]}
    assert dms_to(slack, A)[-1]["blocks"][-1]["elements"][0]["action_id"] == sb.CANCEL_ACTION

    # Pending: invisible to B's graph and to retrieval, no connections yet.
    assert x not in {n["id"] for n in graph(client, "/graph", people[B])["nodes"]}
    assert fetch(db, "SELECT count(*) FROM connection WHERE is_share")[0][0] == 0

    # Same note to the same person again is refused while pending.
    share_to(client, A, x, B)
    assert "still waiting for an answer" in dms_to(slack, A)[-1]["text"]

    # Only the recipient can answer.
    click(client, sb.ACCEPT_ACTION, share_id, "DUB", card["ts"], user=A)
    assert slack.of("post_ephemeral")[-1]["text"] == "This invite isn't addressed to you."

    click(client, sb.ACCEPT_ACTION, share_id, "DUB", card["ts"], user=B)
    assert slack.of("update_message")[-1]["text"] == "Joined your network."
    done = slack.of("update_message")[-1]["blocks"][0]["text"]["text"]
    assert done == "✅ *FastAPI tips* joined your network — 1 new connection with *Python style*"
    assert fetch(db, "SELECT status, is_active FROM share_node")[0] == ("active", True)

    # Persisted rows: one per (target node, key of the shared node), in B's scope.
    rows = fetch(db, "SELECT source_node, target_node, score, is_share, share_node_id, user_id, org_id FROM connection WHERE is_share")
    assert sorted((str(r.source_node), str(r.target_node), r.score) for r in rows) == [(x, y, 1.0), (x, y, 1.0)]
    assert {(str(r.share_node_id), str(r.user_id), r.org_id) for r in rows} == {(share_id, people[B], None)}

    # B's graph: the shared node, and its edge to B's note exactly once.
    g = graph(client, "/graph", people[B])
    assert {n["id"]: n["shared"] for n in g["nodes"]}[x] is True
    assert pairs(g).count(tuple(sorted((x, y)))) == 1
    edge = next(c for c in g["connections"] if {c["source_id"], c["target_id"]} == {x, y})
    assert (edge["key_count"], edge["is_share"], edge["cross_member"]) == (2, True, True)
    # A's graph is unchanged: the edge lives in B's network.
    assert graph(client, "/graph", people[A])["connections"] == []

    # Cross-member DM to the sender — a count, never B's private titles.
    assert dms_to(slack, A)[-1]["text"] == "🔗 Your note *FastAPI tips* connected to 1 note in <@UB>'s network."
    assert notifications(db, people[A]) == [("share_accepted", "Bob accepted your share of 'FastAPI tips'")]
    assert notifications(db, people[B]) == [("share_connections", "'FastAPI tips' joined your network — 1 new connection")]

    # Accepted is permanent: no second answer, no cancel.
    click(client, sb.DECLINE_ACTION, share_id, "DUB", card["ts"], user=B)
    assert slack.of("post_ephemeral")[-1]["text"] == "You already answered this invite."
    click(client, sb.CANCEL_ACTION, share_id, "DUA", "1.1", user=A)
    assert "an accepted share can't be undone" in slack.of("post_ephemeral")[-1]["text"]
    assert fetch(db, "SELECT status, is_active FROM share_node")[0] == ("active", True)

    # B can now ask about A's note.
    gemini.answer = "Use dependency injection [1]."
    command(client, B, "ask fastapi tips python")
    assert "_Sources:_ [1] FastAPI tips" in last_reply(slack, B)["text"]


def test_decline_and_cancel(client, people):
    db, slack = people["db"], people["slack"]
    x = add_node(db, people[A], "FastAPI tips", ["python"])
    add_node(db, people[B], "Python style", ["python"])

    share_to(client, A, x, B)
    card, share_id = invite_card(slack, B)
    click(client, sb.DECLINE_ACTION, share_id, "DUB", card["ts"], user=B)
    assert slack.of("update_message")[-1]["text"] == "Declined."
    assert fetch(db, "SELECT status, is_active FROM share_node")[0] == ("declined", False)
    assert fetch(db, "SELECT count(*) FROM connection")[0][0] == 0
    assert notifications(db, people[A]) == [("share_declined", "Bob declined your share of 'FastAPI tips'")]
    assert notifications(db, people[B]) == []
    click(client, sb.ACCEPT_ACTION, share_id, "DUB", card["ts"], user=B)
    assert fetch(db, "SELECT status FROM share_node")[0] == ("declined",)

    # A declined invite doesn't block a new one; a pending one can be cancelled.
    share_to(client, A, x, B)
    card2, share2 = invite_card(slack, B)
    assert share2 != share_id
    sender_card = dms_to(slack, A)[-1]
    click(client, sb.CANCEL_ACTION, share2, "DUB", card2["ts"], user=B)
    assert slack.of("post_ephemeral")[-1]["text"] == "Only the sender can cancel this invite."
    click(client, sb.CANCEL_ACTION, share2, "DUA", sender_card["ts"], user=A)
    updates = slack.of("update_message")[-2:]
    assert updates[0]["ts"] == sender_card["ts"] and updates[0]["text"] == "Invite cancelled."
    assert updates[1]["ts"] == card2["ts"] and "was cancelled" in updates[1]["blocks"][0]["text"]["text"]
    assert [r.status for r in fetch(db, "SELECT status FROM share_node")] == ["declined"]
    click(client, sb.ACCEPT_ACTION, share2, "DUB", card2["ts"], user=B)
    assert "no longer exists" in slack.of("post_ephemeral")[-1]["text"]


def test_share_to_self_unlinked_or_foreign_node_is_refused(client, people):
    db, slack = people["db"], people["slack"]
    x = add_node(db, people[A], "FastAPI tips", ["python"])
    theirs = add_node(db, people[B], "Bob's", ["python"])

    share_to(client, A, x, A)
    assert "can't share a note with yourself" in dms_to(slack, A)[-1]["text"]
    share_to(client, A, x, C)
    assert "<@UC> hasn't signed in" in dms_to(slack, A)[-1]["text"]
    share_to(client, A, theirs, B)
    assert "only share your own notes" in dms_to(slack, A)[-1]["text"]
    assert fetch(db, "SELECT count(*) FROM share_node")[0][0] == 0


# ---------------------------------------------------------------- team share

def test_team_share_cross_member_dm_notify_scope_and_org_ask(client, people):
    db, slack, gemini = people["db"], people["slack"], people["gemini"]
    make_team(client, people, B)
    org_id = str(fetch(db, "SELECT id FROM orgs")[0][0])
    y = add_node(db, people[B], "Python style", ["python", "typing"])
    x = add_node(db, people[A], "FastAPI tips", ["python", "fastapi"])
    private = add_node(db, people[A], "Private python diary", ["python"])

    command(client, B, "share-team", trigger="T3")
    assert slack.of("open_view")[-1]["view"]["callback_id"] == sb.SHARE_TEAM_MODAL

    # B shares first (default scope: admin only -> A is notified).
    share_team(client, B, y)
    assert "0 connection(s)" in dms_to(slack, B)[-1]["text"]
    assert notifications(db, people[A]) == [("team_node_added", "Bob added 'Python style' to the team space")]
    share = fetch(db, "SELECT org_id, shared_user_id, status, is_active, metadata FROM share_node")[0]
    assert (str(share.org_id), share.shared_user_id, share.status, share.is_active) == (org_id, None, "active", True)
    assert share.metadata == {"notify_scope": "admin", "notify_targets": []}

    # A shares a related note, notifying "specific" people (B).
    n_before = len(slack.of("post_message"))
    share_team(client, A, x, scope="specific", targets=[B])
    rows = fetch(db, "SELECT source_node, target_node, user_id, org_id, is_share FROM connection WHERE is_share")
    assert {(str(r.source_node), str(r.target_node), r.user_id, str(r.org_id)) for r in rows} == {(x, y, None, org_id)}
    assert len(rows) == 1  # only 'python' is shared — and never to A's private notes
    assert private not in {str(r.target_node) for r in rows}

    new = slack.of("post_message")[n_before:]
    texts = {m["channel"]: m["text"] for m in new if m["text"].startswith("🔗")}
    assert texts == {
        "DUA": "🔗 New cross-team connection: your *FastAPI tips* links to *Python style*",
        "DUB": "🔗 New cross-team connection: <@UA>'s *FastAPI tips* links to your *Python style*",
    }
    assert any("1 connection(s), 1 with other members' notes" in m["text"] for m in new if m["channel"] == "DUA")
    assert ("team_node_added", "Alice added 'FastAPI tips' to the team space") in notifications(db, people[B])

    share_team(client, A, x)
    assert "already in the team space" in dms_to(slack, A)[-1]["text"]

    # Team graph: both notes, colored by original owner, one cross-member edge.
    g = graph(client, "/team/graph", people[B])
    assert g["org"]["name"] == "Platform"
    assert {n["id"]: n["owner_id"] for n in g["nodes"]} == {x: people[A], y: people[B]}
    assert pairs(g) == [tuple(sorted((x, y)))]
    assert g["connections"][0]["cross_member"] is True
    assert {m["name"] for m in g["members"]} == {"Alice", "Bob"}
    # The team edge is org-scoped: it is not in either personal graph.
    assert graph(client, "/graph", people[B])["connections"] == []

    # Org-scope ask: B finds A's team note; the log records the org.
    gemini.answer = "Use FastAPI [1]."
    command(client, B, "ask fastapi tips python")
    assert "FastAPI tips" in last_reply(slack, B)["text"]
    log = fetch(db, "SELECT org_id, used_node_ids FROM retrieval_log")[-1]
    assert str(log.org_id) == org_id and [str(i) for i in log.used_node_ids] == [x]

    # Non-members can't use the team space.
    command(client, C, "share-team")
    assert "Sign in to the Neurix web app" in last_reply(slack, C)["text"]
    assert graph(client, "/team/graph", str(uuid.uuid4())) == {"org": None, "members": [], "nodes": [], "connections": []}


def test_shared_node_connections_appear_once_in_graph(client, people):
    """Regression for the double-count risk: share edges come only from
    persisted rows, one per node pair — even with several matching keys, both
    share modes on the same node, and the job re-run."""
    db, slack = people["db"], people["slack"]
    make_team(client, people, B)
    x = add_node(db, people[A], "FastAPI tips", ["python", "fastapi", "api"])
    y = add_node(db, people[B], "Python style", ["python", "fastapi", "api"])

    share_to(client, A, x, B)
    card, share_id = invite_card(slack, B)
    click(client, sb.ACCEPT_ACTION, share_id, "DUB", card["ts"], user=B)
    share_team(client, B, y)
    share_team(client, A, x)
    team_share = str(fetch(db, "SELECT id FROM share_node WHERE org_id IS NOT NULL AND node_id = :n", n=x)[0][0])
    for sid in (share_id, share_id, team_share):  # re-runs replace, never add
        share_service.persist_share_connections(sid)

    assert fetch(db, "SELECT count(*) FROM connection WHERE share_node_id = :s", s=share_id)[0][0] == 3
    assert fetch(db, "SELECT count(*) FROM connection WHERE share_node_id = :s", s=team_share)[0][0] == 3
    for path in ("/graph", "/team/graph"):
        g = graph(client, path, people[B])
        assert pairs(g) == [tuple(sorted((x, y)))], path
        assert g["connections"][0]["key_count"] == 3
        assert len({n["id"] for n in g["nodes"]}) == len(g["nodes"])


def test_semantic_key_match_connects_across_owners(people):
    """Different key text, near-identical key vectors: connected with the
    similarity as the score, like ingest's key merge."""
    db = people["db"]
    x = add_node(db, people[A], "K8s", ["kubernetes"])
    y = add_node(db, people[B], "Cluster ops", ["k8s"])
    with db.begin() as conn:  # make 'k8s' embed like 'kubernetes'
        conn.execute(
            text("UPDATE key_pool SET key_vec = CAST(:v AS vector) WHERE key_text = 'k8s'"),
            {"v": to_pgvector(fake_vector("kubernetes"))},
        )
        share_id = str(conn.execute(
            text("INSERT INTO share_node (owner_user_id, shared_user_id, node_id, status, is_active) "
                 "VALUES (:a, :b, :x, 'active', true) RETURNING id"),
            {"a": people[A], "b": people[B], "x": x},
        ).scalar_one())
    result = share_service.persist_share_connections(share_id)
    assert [n["id"] for n in result.connected] == [y]
    assert fetch(db, "SELECT round(score::numeric, 3) FROM connection")[0][0] == 1


# ---------------------------------------------------------------- stats

def test_stats_digest_and_insights(client, people):
    db, slack = people["db"], people["slack"]
    make_team(client, people, B)
    a1 = add_node(db, people[A], "K8s basics", ["kubernetes", "python"])
    a2 = add_node(db, people[A], "K8s upgrades", ["kubernetes"])
    b1 = add_node(db, people[B], "Python style", ["python"])
    add_node(db, people[B], "Unconnected", ["baking"])
    share_team(client, A, a1)
    share_team(client, A, a2)
    share_team(client, B, b1, scope="all")
    with db.begin() as conn:
        conn.execute(
            text("INSERT INTO retrieval_log (user_id, org_id, question, used_node_ids) VALUES (:u, :o, 'q', CAST(:ids AS uuid[]))"),
            {"u": people[A], "o": str(fetch(db, "SELECT id FROM orgs")[0][0]), "ids": [b1, a1]},
        )

    command(client, B, "stats")
    reply = last_reply(slack, B)
    body = "\n".join(b["text"]["text"] for b in reply["blocks"] if b["type"] == "section")
    # Digest: A's two team notes (scope admin -> B isn't admin, so none), plus
    # nothing else; B's own share notified A. So B's digest is empty...
    assert "*🔔 Since last time*\nNothing new." in body
    assert "• *Volume:* 2 notes, 2 keys, 0 connections" in body
    assert "• *Shared:* 0 with people, 1 to your team · 0 received" in body
    assert "• *Quality:* 50% unconnected notes, 1.0 keys per note" in body
    assert "• *Bridges:* 1 connection between your notes and other people's" in body
    assert "• *Impact:* your notes were used 1 time in answers" in body
    assert "*👥 Team Platform*" in body
    assert "2 members, 3 shared notes from 2 contributors, 2 keys, 2 connections" in body
    assert "• *Cross-contribution:* 1 bridge between different members' notes" in body
    assert "• *Impact:* team notes were used 2 times in team answers" in body
    assert "*⚠️ Bus factor*\n• *kubernetes* (2 notes) — only Alice contributes here" in body
    assert "Alice — kubernetes" in body

    # A's digest has the notifications, once.
    command(client, A, "stats")
    body = last_reply(slack, A)["blocks"][0]["text"]["text"]
    assert body == "*🔔 Since last time*\n• Bob added 'Python style' to the team space"
    command(client, A, "stats")
    assert last_reply(slack, A)["blocks"][0]["text"]["text"] == "*🔔 Since last time*\nNothing new."


# ---------------------------------------------------------------- raw files

def test_raw_access_toggles_file_access(client, people, raw_store):
    db, slack = people["db"], people["slack"]
    make_team(client, people, B)
    x = add_node(db, people[A], "FastAPI tips", ["python"], raw_path="raw/a/20260101T000000-tips.pdf")
    t = add_node(db, people[A], "Team doc", ["python"], raw_path="raw/a/20260101T000000-team.pdf")

    command(client, A, "file fastapi")
    assert "raw/a/20260101T000000-tips.pdf?ttl=600" in last_reply(slack, A)["text"]

    command(client, A, "raw-access FastAPI tips <@UB|bob> on")
    assert "has no active share with that person" in last_reply(slack, A)["text"]

    share_to(client, A, x, B)
    card, share_id = invite_card(slack, B)
    command(client, A, "raw-access FastAPI tips <@UB|bob> on")
    assert "has no active share" in last_reply(slack, A)["text"]  # still pending
    click(client, sb.ACCEPT_ACTION, share_id, "DUB", card["ts"], user=B)

    command(client, B, "file FastAPI tips")
    assert last_reply(slack, B)["text"] == "🔒 You don't have access to the original file of *FastAPI tips*."

    command(client, A, "raw-access FastAPI tips <@UB|bob> on")
    assert last_reply(slack, A)["text"] == "🔓 Original file of *FastAPI tips* is now visible to <@UB>."
    command(client, B, "file FastAPI tips")
    assert "tips.pdf?ttl=600|download>" in last_reply(slack, B)["text"]

    command(client, A, "raw-access FastAPI tips <@UB> off")
    command(client, B, "file FastAPI tips")
    assert last_reply(slack, B)["text"].startswith("🔒")
    # Revoking raw access never touches the share itself.
    assert fetch(db, "SELECT status, is_active, can_view_raw FROM share_node")[0] == ("active", True, False)
    assert [n[0] for n in notifications(db, people[B]) if n[0].startswith("raw_")] == [
        "raw_access_granted", "raw_access_revoked"
    ]

    # Team scope: `team` flips the org share's flag for every member.
    share_team(client, A, t)
    command(client, B, "file team doc")
    assert last_reply(slack, B)["text"].startswith("🔒")
    command(client, A, "raw-access team doc team on")
    command(client, B, "file team doc")
    assert "team.pdf" in last_reply(slack, B)["text"]

    command(client, B, "raw-access FastAPI tips <@UA> on")  # not B's note
    assert "only your own notes" in last_reply(slack, B)["text"]
    command(client, A, "raw-access FastAPI tips")
    assert last_reply(slack, A)["text"].startswith("Usage:")


def test_ingest_keeps_original_in_raw_storage(client, people, raw_store):
    slack, gemini, db = people["slack"], people["gemini"], people["db"]
    gemini.structured = {
        "TitleSummaryResult": {"title": "Note", "summary": "a note", "generated_keys": ["note"]},
        "KeysResult": {"all_keys": ["note"]},
    }
    command(client, A, "save remember the milk")
    card = slack.of("post_message")[-1]
    draft_id = card["blocks"][-1]["elements"][0]["value"]
    [(path, (data, content_type))] = raw_store.objects.items()
    assert path.startswith(f"raw/{people[A]}/") and path.endswith("-note.txt")
    assert (data, content_type) == (b"remember the milk", "text/plain; charset=utf-8")

    click(client, "neurix_confirm", draft_id, "DUA", card["ts"], user=A)
    meta = fetch(db, "SELECT metadata FROM node_pool WHERE title = 'Note'")[0][0]
    assert meta["raw_storage_path"] == path

    command(client, A, "save throw this away")
    card = slack.of("post_message")[-1]
    [path2] = [p for p in raw_store.objects if p != path]
    click(client, "neurix_discard", card["blocks"][-1]["elements"][0]["value"], "DUA", card["ts"], user=A)
    assert raw_store.deleted == [path2]


# ---------------------------------------------------------------- units

def test_parse_raw_access():
    assert parse_raw_access("My note title <@U1|bob> on") == ("My note title", "U1", True)
    assert parse_raw_access("Doc team OFF") == ("Doc", "team", False)
    assert parse_raw_access("Doc @bob on") is None  # unescaped mention
    assert parse_raw_access("Doc <@U1> maybe") is None
    assert parse_raw_access("<@U1> on") is None


def test_match_node():
    nodes = [{"id": "1", "title": "FastAPI tips"}, {"id": "2", "title": "FastAPI"}, {"id": "3", "title": "Python"}]
    assert match_node(nodes, "fastapi")[0]["id"] == "2"  # exact title wins
    assert match_node(nodes, "tips")[0]["id"] == "1"
    assert match_node(nodes, "3")[0]["id"] == "3"
    assert "matches several" in match_node(nodes, "fast")[1]
    assert "No note matching" in match_node(nodes, "rust")[1]

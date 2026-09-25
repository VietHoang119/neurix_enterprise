"""Web Team page (create, invite by email, remove, read-only for members)
and original-file links on the web node lists."""

from contextlib import contextmanager

from core.auth import get_current_uid
from services import team as team_service
from services.slack import share_blocks as sb
from test_team_share import (  # noqa: F401  (people is a fixture)
    A,
    B,
    add_node,
    click,
    command,
    invite_card,
    last_reply,
    make_team,
    people,
    share_team,
    share_to,
    submit_raw_access,
)


@contextmanager
def as_user(client, user_id):
    client.app.dependency_overrides[get_current_uid] = lambda: user_id
    try:
        yield client
    finally:
        client.app.dependency_overrides.clear()


def members(view):
    return [(m["email"], m["role"], m["shared_notes"], m["is_me"]) for m in view["members"]]


def test_web_team_create_invite_remove(client, people):
    a, b = people[A], people[B]
    with as_user(client, a):
        assert client.get("/team").json() == {"org": None, "members": []}
        res = client.post("/team", json={"name": "  Neurix Demo Team "})
        assert res.status_code == 201
        view = res.json()
        assert view["org"]["name"] == "Neurix Demo Team" and view["org"]["role"] == "admin"
        assert members(view) == [("a@example.com", "admin", 0, True)]
        assert client.post("/team", json={"name": "Second"}).status_code == 409

        res = client.post("/team/members", json={"email": "c@example.com"})  # never signed in
        assert (res.status_code, res.json()["detail"]) == (404, team_service.NOT_SIGNED_IN)
        res = client.post("/team/members", json={"email": " B@Example.com "})
        assert res.status_code == 201
        assert members(res.json())[1] == ("b@example.com", "member", 0, False)
        res = client.post("/team/members", json={"email": "b@example.com"})
        assert (res.status_code, res.json()["detail"]) == (409, "This person is already in your team.")

    # The web team is the same team Slack sees.
    command(client, A, "team list")
    assert "*Team Neurix Demo Team* — 2 member(s)" in last_reply(people["slack"], A)["text"]

    x = add_node(people["db"], a, "FastAPI tips", ["python"])
    share_team(client, A, x)

    with as_user(client, b):  # a member: read-only
        view = client.get("/team").json()
        assert view["org"]["role"] == "member"
        assert members(view) == [("a@example.com", "admin", 1, False), ("b@example.com", "member", 0, True)]
        assert client.post("/team/members", json={"email": "a@example.com"}).status_code == 403
        assert client.delete(f"/team/members/{a}").status_code == 403

    with as_user(client, a):
        res = client.delete(f"/team/members/{a}")
        assert (res.status_code, res.json()["detail"]) == (400, "You can't remove the team's only admin.")
        assert client.delete("/team/members/not-a-member").status_code == 400
        res = client.delete(f"/team/members/{b}")
        assert res.status_code == 200 and [m["email"] for m in res.json()["members"]] == ["a@example.com"]
    with as_user(client, b):
        assert client.get("/team").json()["org"] is None


def test_web_file_links_follow_view_raw(client, people, raw_store):
    db, slack, a, b = people["db"], people["slack"], people[A], people[B]
    make_team(client, people, B)
    x = add_node(db, a, "FastAPI tips", ["python"], raw_path="raw/a/tips.pdf")
    t = add_node(db, a, "Team doc", ["python"], raw_path="raw/a/team.pdf")
    y = add_node(db, a, "No original", ["python"])
    share_to(client, A, x, B)
    card, share_id = invite_card(slack, B)
    click(client, sb.ACCEPT_ACTION, share_id, "DUB", card["ts"], user=B)
    share_team(client, A, t)

    with as_user(client, a):  # the owner can always open their own originals
        own = {n["id"]: n["can_open_file"] for n in client.get("/nodes").json()["nodes"]}
        assert own == {x: True, t: True, y: False}
        assert client.get(f"/nodes/{x}/file").json()["url"] == "https://storage.example/raw/a/tips.pdf?ttl=600"
        assert client.get(f"/nodes/{y}/file").status_code == 404

    def b_view():
        with as_user(client, b):
            listed = client.get("/nodes").json()
            team = client.get("/team/graph").json()
        shared = {n["id"]: n["can_open_file"] for n in listed["shared_with_me"]}
        return shared, {n["id"]: n["can_open_file"] for n in team["nodes"]}

    assert b_view() == ({x: False}, {t: False})
    with as_user(client, b):
        assert client.get(f"/nodes/{x}/file").status_code == 404
        assert client.get("/nodes").json()["shared_with_me"][0]["owner_email"] == "a@example.com"

    submit_raw_access(client, A, x, b, True)
    submit_raw_access(client, A, t, "team", True)
    assert b_view() == ({x: True}, {t: True})
    with as_user(client, b):
        assert client.get(f"/nodes/{t}/file").json() == {
            "url": "https://storage.example/raw/a/team.pdf?ttl=600", "expires_in": 600, "title": "Team doc"
        }

    submit_raw_access(client, A, x, b, False)
    assert b_view() == ({x: False}, {t: True})


def test_no_file_links_without_raw_storage(client, people):
    a = people[A]
    x = add_node(people["db"], a, "FastAPI tips", ["python"], raw_path="raw/a/tips.pdf")
    with as_user(client, a):
        assert client.get("/nodes").json()["nodes"][0]["can_open_file"] is False
        assert client.get(f"/nodes/{x}/file").status_code == 404

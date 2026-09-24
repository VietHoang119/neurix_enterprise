import uuid

from sqlalchemy import text

from core.auth import get_current_uid


def test_nodes_and_graph_are_personal_and_deduplicated(client, db):
    me, other = str(uuid.uuid4()), str(uuid.uuid4())
    with db.begin() as conn:
        def node(user, title, status):
            return str(conn.execute(
                text("INSERT INTO node_pool (user_id, title, status) VALUES (:u, :t, :s) RETURNING id"),
                {"u": user, "t": title, "s": status},
            ).scalar_one())

        a, b = node(me, "A", "READY"), node(me, "B", "PROCESSING")
        failed = node(me, "Broken", "FAILED")
        theirs = node(other, "Not mine", "READY")
        k1 = conn.execute(text("INSERT INTO key_pool (user_id, key_text) VALUES (:u, 'k1') RETURNING id"), {"u": me}).scalar_one()
        k2 = conn.execute(text("INSERT INTO key_pool (user_id, key_text) VALUES (:u, 'k2') RETURNING id"), {"u": me}).scalar_one()
        for src, tgt, score, key in [(b, a, 1.0, k1), (a, b, 0.92, k2), (b, failed, 1.0, k1)]:
            conn.execute(
                text("INSERT INTO connection (user_id, source_node, target_node, score, via_key_id) VALUES (:u, :s, :t, :sc, :k)"),
                {"u": me, "s": src, "t": tgt, "sc": score, "k": key},
            )

    client.app.dependency_overrides[get_current_uid] = lambda: me
    try:
        nodes = client.get("/nodes").json()["nodes"]
        graph = client.get("/graph").json()
    finally:
        client.app.dependency_overrides.clear()

    assert {(n["title"], n["status"]) for n in nodes} == {("A", "READY"), ("B", "PROCESSING"), ("Broken", "FAILED")}
    assert theirs not in {n["id"] for n in nodes}
    assert {n["id"] for n in graph["nodes"]} == {a, b}
    assert graph["connections"] == [
        {"source_id": min(a, b), "target_id": max(a, b), "score": 1.0, "key_count": 2}
    ]

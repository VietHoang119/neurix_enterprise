"""`/neurix stats` insights — plain SQL, no model calls.

Personal numbers cover the caller's own nodes. Org numbers cover the org's
team space only (nodes actively shared to the org): members' private notes
never feed org-level insights, not even as key names.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

# "Domain" for the bus-factor check: a key shared by at least this many team
# nodes — a small key cluster rather than a one-off tag.
DOMAIN_MIN_NODES = 2


def _scalar(conn: Connection, sql: str, params: dict[str, Any]) -> Any:
    return conn.execute(text(sql), params).scalar_one()


def personal(conn: Connection, user_id: str) -> dict[str, Any]:
    p = {"uid": user_id}
    own = "SELECT id FROM node_pool WHERE user_id = :uid AND status <> 'FAILED'"
    nodes = _scalar(conn, f"SELECT count(*) FROM ({own}) n", p)
    return {
        "nodes": nodes,
        "keys": _scalar(conn, "SELECT count(*) FROM key_pool WHERE user_id = :uid", p),
        "connections": _scalar(
            conn,
            "SELECT count(DISTINCT (least(source_node, target_node), greatest(source_node, target_node))) "
            "FROM connection WHERE user_id = :uid AND source_node <> target_node",
            p,
        ),
        "shared_to_people": _scalar(
            conn,
            "SELECT count(DISTINCT node_id) FROM share_node WHERE owner_user_id = :uid "
            "AND shared_user_id IS NOT NULL AND status = 'active'",
            p,
        ),
        "pending_invites": _scalar(
            conn,
            "SELECT count(*) FROM share_node WHERE owner_user_id = :uid AND shared_user_id IS NOT NULL "
            "AND status = 'pending'",
            p,
        ),
        "shared_to_team": _scalar(
            conn, "SELECT count(DISTINCT node_id) FROM share_node WHERE owner_user_id = :uid AND org_id IS NOT NULL", p
        ),
        "received": _scalar(
            conn, "SELECT count(DISTINCT node_id) FROM share_node WHERE shared_user_id = :uid AND status = 'active'", p
        ),
        "orphan_pct": _pct(
            _scalar(
                conn,
                f"SELECT count(*) FROM ({own}) n WHERE NOT EXISTS ("
                "SELECT 1 FROM connection c WHERE (c.source_node = n.id OR c.target_node = n.id) "
                "AND c.source_node <> c.target_node)",
                p,
            ),
            nodes,
        ),
        "avg_keys": _avg_keys(conn, own, p),
        # Bridges: share connections between one of my nodes and someone
        # else's, whichever graph (mine, a recipient's, the team's) holds them.
        "bridges": _scalar(
            conn,
            "SELECT count(DISTINCT (least(c.source_node, c.target_node), greatest(c.source_node, c.target_node))) "
            "FROM connection c JOIN node_pool s ON s.id = c.source_node JOIN node_pool t ON t.id = c.target_node "
            "WHERE c.is_share = true AND s.user_id <> t.user_id AND (s.user_id = :uid OR t.user_id = :uid)",
            p,
        ),
        "impact": _scalar(
            conn,
            "SELECT count(*) FROM retrieval_log rl CROSS JOIN LATERAL unnest(rl.used_node_ids) AS u(node_id) "
            "JOIN node_pool np ON np.id = u.node_id WHERE np.user_id = :uid",
            p,
        ),
    }


def org(conn: Connection, org_id: str) -> dict[str, Any]:
    p = {"org_id": org_id}
    team = (
        "SELECT DISTINCT np.id, np.user_id FROM node_pool np JOIN share_node sn ON sn.node_id = np.id "
        "WHERE sn.org_id = :org_id AND sn.status = 'active' AND sn.is_active = true AND np.status <> 'FAILED'"
    )
    nodes = _scalar(conn, f"SELECT count(*) FROM ({team}) n", p)
    names = {
        str(r.uid): r.name
        for r in conn.execute(
            text(
                "SELECT m.uid, coalesce(m.display_name, u.email, 'someone') AS name FROM org_members m "
                "LEFT JOIN auth.users u ON u.id = m.uid WHERE m.org_id = :org_id"
            ),
            p,
        ).all()
    }
    # key_text -> owners and node count, over team nodes only.
    key_owners = conn.execute(
        text(
            f"SELECT kp.key_text, array_agg(DISTINCT n.user_id) AS owners, count(DISTINCT n.id) AS node_count "
            f"FROM ({team}) n JOIN node_keys nk ON nk.node_id = n.id JOIN key_pool kp ON kp.id = nk.key_id "
            "GROUP BY kp.key_text"
        ),
        p,
    ).all()
    contributors = _scalar(conn, f"SELECT count(DISTINCT user_id) FROM ({team}) n", p)

    expertise: dict[str, list[tuple[str, int]]] = {}
    bus_factor: list[dict[str, Any]] = []
    for row in key_owners:
        owners = [str(o) for o in row.owners]
        if len(owners) != 1:
            continue
        owner = names.get(owners[0], "someone")
        expertise.setdefault(owner, []).append((row.key_text, int(row.node_count)))
        if contributors >= 2 and row.node_count >= DOMAIN_MIN_NODES:
            bus_factor.append({"key": row.key_text, "owner": owner, "nodes": int(row.node_count)})
    for keys in expertise.values():
        keys.sort(key=lambda k: (-k[1], k[0]))
    bus_factor.sort(key=lambda d: (-d["nodes"], d["key"]))

    return {
        "members": len(names),
        "nodes": nodes,
        "contributors": contributors,
        "keys": len(key_owners),
        "connections": _scalar(
            conn,
            "SELECT count(DISTINCT (least(source_node, target_node), greatest(source_node, target_node))) "
            "FROM connection WHERE org_id = :org_id AND source_node <> target_node",
            p,
        ),
        "bridges": _scalar(
            conn,
            "SELECT count(DISTINCT (least(c.source_node, c.target_node), greatest(c.source_node, c.target_node))) "
            "FROM connection c JOIN node_pool s ON s.id = c.source_node JOIN node_pool t ON t.id = c.target_node "
            "WHERE c.org_id = :org_id AND c.is_share = true AND s.user_id <> t.user_id",
            p,
        ),
        "orphan_pct": _pct(
            _scalar(
                conn,
                f"SELECT count(*) FROM ({team}) n WHERE NOT EXISTS ("
                "SELECT 1 FROM connection c WHERE c.org_id = :org_id AND c.source_node <> c.target_node "
                "AND (c.source_node = n.id OR c.target_node = n.id))",
                p,
            ),
            nodes,
        ),
        "avg_keys": _avg_keys(conn, f"SELECT id FROM ({team}) t", p),
        "impact": _scalar(
            conn,
            f"SELECT count(*) FROM retrieval_log rl CROSS JOIN LATERAL unnest(rl.used_node_ids) AS u(node_id) "
            f"WHERE rl.org_id = :org_id AND u.node_id IN (SELECT id FROM ({team}) n)",
            p,
        ),
        "expertise": {owner: [k for k, _ in keys[:3]] for owner, keys in sorted(expertise.items())},
        "bus_factor": bus_factor[:5],
    }


def _avg_keys(conn: Connection, node_ids_sql: str, params: dict[str, Any]) -> float:
    value = _scalar(
        conn,
        f"SELECT avg(k.cnt) FROM (SELECT n.id, count(nk.key_id) AS cnt FROM ({node_ids_sql}) n "
        "LEFT JOIN node_keys nk ON nk.node_id = n.id GROUP BY n.id) k",
        params,
    )
    return round(float(value), 1) if value is not None else 0.0


def _pct(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0

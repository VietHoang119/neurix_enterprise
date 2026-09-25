"""connection + retrieval_log access.

Two kinds of connection rows are written here:
- personal (insert_connections): user_id set, org_id NULL, is_share=false —
  from the post-confirm ingest job.
- share (insert_share_connections): is_share=true, share_node_id set, and
  either user_id (an accepted person share, in the recipient's graph) or
  org_id (a team share, in the org's graph) — the 0002 XOR constraint.

Graphs are read from these rows only. neurix computes share edges live at
request time (GraphService._build_incoming_share_connections); that is not
ported, so a persisted share edge can never be drawn twice.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def insert_connections(conn: Connection, *, user_id: str, rows: list[dict[str, Any]]) -> int:
    for row in rows:
        conn.execute(
            text(
                "INSERT INTO connection (user_id, source_node, target_node, score, via_key_id, is_share) "
                "VALUES (:user_id, :source_node, :target_node, :score, :via_key_id, false)"
            ),
            {"user_id": user_id, **row},
        )
    return len(rows)


def share_connection_candidates(
    conn: Connection,
    *,
    node_id: str,
    threshold: float,
    recipient_id: str | None = None,
    org_id: str | None = None,
) -> list[dict[str, Any]]:
    """Connections a newly shared node forms with the recipient's own nodes
    (person scope) or with the other nodes in the org's team space (org
    scope).

    Same rule as the ingest job (services/ingest.process_node, itself a port
    of GraphService.insert): one row per (other node, key of the shared
    node), score 1.0 for the same key and the key-vector cosine similarity
    for a key at or above KEYS_SIMILARITY_SCORE (what ingest would have
    merged). key_pool is per user, so across owners "the same key" means the
    same key_text rather than the same key_id. via_key_id is the shared
    node's key, as in neurix's _build_incoming_share_connections.
    """
    rows = conn.execute(
        text(
            "WITH src AS ("
            "  SELECT kp.id AS key_id, kp.key_text, kp.key_vec FROM node_keys nk "
            "  JOIN key_pool kp ON kp.id = nk.key_id WHERE nk.node_id = :node_id"
            "), targets AS ("
            "  SELECT np.id FROM node_pool np WHERE np.id <> :node_id AND np.status <> 'FAILED' AND ("
            "    (CAST(:recipient AS uuid) IS NOT NULL AND np.user_id = CAST(:recipient AS uuid)) "
            "    OR (CAST(:org_id AS uuid) IS NOT NULL AND np.id IN ("
            "      SELECT sn.node_id FROM share_node sn WHERE sn.org_id = CAST(:org_id AS uuid) "
            "      AND sn.status = 'active' AND sn.is_active = true)))"
            "), target_keys AS ("
            "  SELECT nk.node_id, kp.key_text, kp.key_vec FROM node_keys nk "
            "  JOIN key_pool kp ON kp.id = nk.key_id WHERE nk.node_id IN (SELECT id FROM targets)"
            "), matches AS ("
            "  SELECT tk.node_id AS target, src.key_id AS via_key_id, "
            "    CASE WHEN tk.key_text = src.key_text THEN 1.0 "
            "         ELSE 1 - (tk.key_vec <=> src.key_vec) END AS score "
            "  FROM src JOIN target_keys tk ON tk.key_text = src.key_text "
            "    OR (tk.key_vec IS NOT NULL AND src.key_vec IS NOT NULL "
            "        AND 1 - (tk.key_vec <=> src.key_vec) >= :threshold)"
            ") "
            "SELECT DISTINCT ON (target, via_key_id) target, via_key_id, score FROM matches "
            "ORDER BY target, via_key_id, score DESC"
        ),
        {"node_id": node_id, "recipient": recipient_id, "org_id": org_id, "threshold": threshold},
    ).all()
    return [
        {"source_node": node_id, "target_node": str(r.target), "score": float(r.score), "via_key_id": str(r.via_key_id)}
        for r in rows
    ]


def insert_share_connections(
    conn: Connection,
    *,
    share_node_id: str,
    rows: list[dict[str, Any]],
    user_id: str | None = None,
    org_id: str | None = None,
) -> int:
    """Replace this share's persisted connections (so re-running the job
    never duplicates them)."""
    conn.execute(text("DELETE FROM connection WHERE share_node_id = :sid"), {"sid": share_node_id})
    for row in rows:
        conn.execute(
            text(
                "INSERT INTO connection (user_id, org_id, source_node, target_node, score, via_key_id, "
                "is_share, share_node_id) "
                "VALUES (:user_id, :org_id, :source_node, :target_node, :score, :via_key_id, true, :sid)"
            ),
            {"user_id": user_id, "org_id": org_id, "sid": share_node_id, **row},
        )
    return len(rows)


# One edge per node pair (strongest score kept) — connection has a row per
# (pair, key), and may hold both directions of a pair.
_EDGES = (
    "SELECT least(c.source_node, c.target_node) AS a, greatest(c.source_node, c.target_node) AS b, "
    "max(c.score) AS score, count(DISTINCT c.via_key_id) AS key_count, bool_or(c.is_share) AS is_share, "
    "bool_or(s.user_id <> t.user_id) AS cross_member "
    "FROM connection c "
    "JOIN node_pool s ON s.id = c.source_node "
    "JOIN node_pool t ON t.id = c.target_node "
    "WHERE c.source_node <> c.target_node "
    "AND c.source_node = ANY(CAST(:ids AS uuid[])) AND c.target_node = ANY(CAST(:ids AS uuid[])) "
)


def _edges(rows) -> list[dict[str, Any]]:
    return [
        {
            "source_id": str(e.a),
            "target_id": str(e.b),
            "score": float(e.score),
            "key_count": int(e.key_count),
            "is_share": bool(e.is_share),
            "cross_member": bool(e.cross_member),
        }
        for e in rows
    ]


def _keys_by_node(conn: Connection, ids: list[str]) -> dict[str, list[str]]:
    """node_id -> its extracted key texts (node_keys -> key_pool), for the
    web graphs' "Show keys" view. Grouped by text on the client, so a key
    two owners both extracted is one key there, as in share scoring."""
    if not ids:
        return {}
    rows = conn.execute(
        text(
            "SELECT nk.node_id, kp.key_text FROM node_keys nk JOIN key_pool kp ON kp.id = nk.key_id "
            "WHERE nk.node_id = ANY(CAST(:ids AS uuid[])) ORDER BY nk.node_id, kp.key_text"
        ),
        {"ids": ids},
    ).all()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(str(r.node_id), []).append(r.key_text)
    return result


def get_graph(conn: Connection, user_id: str) -> dict[str, list[dict[str, Any]]]:
    """The user's personal graph: their own nodes plus nodes from accepted
    person shares, and the connection rows scoped to them (their own
    ingest connections and the persisted share connections)."""
    nodes = conn.execute(
        text(
            "SELECT np.id, np.title, np.ai_title, np.status, np.user_id FROM node_pool np "
            "WHERE np.status <> 'FAILED' AND (np.user_id = :user_id OR np.id IN ("
            "  SELECT sn.node_id FROM share_node sn WHERE sn.shared_user_id = :user_id "
            "  AND sn.status = 'active' AND sn.is_active = true "
            "  AND (sn.expires_at IS NULL OR sn.expires_at > now()))) "
            "ORDER BY np.created_at"
        ),
        {"user_id": user_id},
    ).all()
    ids = [str(n.id) for n in nodes]
    edges = conn.execute(text(_EDGES + "AND c.user_id = :user_id GROUP BY 1, 2"), {"user_id": user_id, "ids": ids}).all()
    keys = _keys_by_node(conn, ids)
    return {
        "nodes": [
            {
                "id": str(n.id),
                "title": n.title or n.ai_title or "Untitled",
                "status": n.status,
                "owner_id": str(n.user_id),
                "shared": str(n.user_id) != user_id,
                "keys": keys.get(str(n.id), []),
            }
            for n in nodes
        ],
        "connections": _edges(edges),
    }


def get_team_graph(conn: Connection, org_id: str) -> dict[str, list[dict[str, Any]]]:
    """The org's team space: every node actively shared to the org (a
    reference to the original, never a copy), tagged with its original
    owner, and the org-scoped connection rows between them."""
    nodes = conn.execute(
        text(
            "SELECT DISTINCT np.id, np.title, np.ai_title, np.status, np.user_id, np.created_at FROM node_pool np "
            "JOIN share_node sn ON sn.node_id = np.id AND sn.org_id = :org_id "
            "AND sn.status = 'active' AND sn.is_active = true "
            "WHERE np.status <> 'FAILED' ORDER BY np.created_at"
        ),
        {"org_id": org_id},
    ).all()
    ids = [str(n.id) for n in nodes]
    edges = conn.execute(text(_EDGES + "AND c.org_id = :org_id GROUP BY 1, 2"), {"org_id": org_id, "ids": ids}).all()
    keys = _keys_by_node(conn, ids)
    return {
        "nodes": [
            {
                "id": str(n.id),
                "title": n.title or n.ai_title or "Untitled",
                "status": n.status,
                "owner_id": str(n.user_id),
                "shared": True,
                "keys": keys.get(str(n.id), []),
            }
            for n in nodes
        ],
        "connections": _edges(edges),
    }


def log_retrieval(
    conn: Connection, *, user_id: str, question: str, used_node_ids: list[str], org_id: str | None = None
) -> None:
    conn.execute(
        text(
            "INSERT INTO retrieval_log (user_id, org_id, question, used_node_ids) "
            "VALUES (:user_id, :org_id, :question, CAST(:ids AS uuid[]))"
        ),
        {"user_id": user_id, "org_id": org_id, "question": question, "ids": list(used_node_ids)},
    )

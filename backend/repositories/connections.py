"""connection + retrieval_log access. Personal scope only: every row written
here has user_id set, org_id NULL, is_share=false. Share-derived connections
are Week 3."""

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


def get_graph(conn: Connection, user_id: str) -> dict[str, list[dict[str, Any]]]:
    """The user's own nodes + personal connections, one edge per node pair
    (strongest score kept) — connection has a row per (pair, shared key)."""
    nodes = conn.execute(
        text(
            "SELECT id, title, ai_title, status FROM node_pool "
            "WHERE user_id = :user_id AND status <> 'FAILED' ORDER BY created_at"
        ),
        {"user_id": user_id},
    ).all()
    edges = conn.execute(
        text(
            "SELECT least(c.source_node, c.target_node) AS a, greatest(c.source_node, c.target_node) AS b, "
            "max(c.score) AS score, count(*) AS key_count "
            "FROM connection c "
            "JOIN node_pool s ON s.id = c.source_node AND s.user_id = :user_id AND s.status <> 'FAILED' "
            "JOIN node_pool t ON t.id = c.target_node AND t.user_id = :user_id AND t.status <> 'FAILED' "
            "WHERE c.user_id = :user_id AND c.is_share = false AND c.source_node <> c.target_node "
            "GROUP BY 1, 2"
        ),
        {"user_id": user_id},
    ).all()
    return {
        "nodes": [
            {"id": str(n.id), "title": n.title or n.ai_title or "Untitled", "status": n.status} for n in nodes
        ],
        "connections": [
            {"source_id": str(e.a), "target_id": str(e.b), "score": float(e.score), "key_count": int(e.key_count)}
            for e in edges
        ],
    }


def log_retrieval(conn: Connection, *, user_id: str, question: str, used_node_ids: list[str]) -> None:
    conn.execute(
        text(
            "INSERT INTO retrieval_log (user_id, question, used_node_ids) "
            "VALUES (:user_id, :question, CAST(:ids AS uuid[]))"
        ),
        {"user_id": user_id, "question": question, "ids": list(used_node_ids)},
    )

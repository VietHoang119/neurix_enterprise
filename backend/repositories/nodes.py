"""node_pool access. Processing state lives in the existing
node_pool.status column (DRAFT / PROCESSING / READY / FAILED) — never a new
column. A failure note goes into node_pool.metadata->'error' for the same
reason."""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from db.vector import from_pgvector, to_pgvector


def insert_processing_node(conn: Connection, *, user_id: str, client_node_id: str, node: dict[str, Any]) -> str:
    row = conn.execute(
        text(
            "INSERT INTO node_pool (user_id, client_node_id, title, source, origin, size_tokens, "
            "summary, summary_vec, metadata, fingerprint, raw_content, status) "
            "VALUES (:user_id, :client_node_id, :title, :source, :origin, :size_tokens, "
            ":summary, CAST(:summary_vec AS vector), CAST(:metadata AS jsonb), :fingerprint, "
            ":raw_content, 'PROCESSING') RETURNING id"
        ),
        {
            "user_id": user_id,
            "client_node_id": client_node_id,
            "title": node.get("title"),
            "source": node.get("source"),
            "origin": node.get("origin"),
            "size_tokens": node.get("size_tokens"),
            "summary": node.get("summary"),
            "summary_vec": to_pgvector(node.get("summary_vec")),
            "metadata": json.dumps(node.get("metadata") or {}),
            "fingerprint": node.get("fingerprint"),
            "raw_content": node.get("raw_content"),
        },
    ).first()
    return str(row[0])


def get_node(conn: Connection, node_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        text("SELECT id, user_id, title, summary, summary_vec, status FROM node_pool WHERE id = :id"),
        {"id": node_id},
    ).first()
    if row is None:
        return None
    data = dict(row._mapping)
    data["id"] = str(data["id"])
    data["user_id"] = str(data["user_id"])
    data["summary_vec"] = from_pgvector(data["summary_vec"]) or None
    return data


def set_summary_vec(conn: Connection, node_id: str, vec: list[float]) -> None:
    conn.execute(
        text("UPDATE node_pool SET summary_vec = CAST(:vec AS vector), updated_at = now() WHERE id = :id"),
        {"id": node_id, "vec": to_pgvector(vec)},
    )


def set_status(conn: Connection, node_id: str, status: str, error: str | None = None) -> None:
    if error is None:
        conn.execute(
            text("UPDATE node_pool SET status = :status, updated_at = now() WHERE id = :id"),
            {"id": node_id, "status": status},
        )
        return
    conn.execute(
        text(
            "UPDATE node_pool SET status = :status, updated_at = now(), "
            "metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object('error', CAST(:error AS text)) "
            "WHERE id = :id"
        ),
        {"id": node_id, "status": status, "error": error},
    )


def list_nodes(conn: Connection, user_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT id, title, ai_title, status, source, origin, created_at FROM node_pool "
            "WHERE user_id = :user_id ORDER BY created_at DESC"
        ),
        {"user_id": user_id},
    ).all()
    return [
        {
            "id": str(r.id),
            "title": r.title or r.ai_title or "Untitled",
            "status": r.status,
            "source": r.source,
            "origin": r.origin,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def recent_ready_nodes(conn: Connection, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """The user's own READY nodes, newest first — the share pickers' options
    (Slack static_select allows at most 100)."""
    rows = conn.execute(
        text(
            "SELECT id, coalesce(title, ai_title, 'Untitled') AS title FROM node_pool "
            "WHERE user_id = :user_id AND status = 'READY' ORDER BY created_at DESC LIMIT :limit"
        ),
        {"user_id": user_id, "limit": limit},
    ).all()
    return [{"id": str(r.id), "title": r.title} for r in rows]


def search_similar_nodes(
    conn: Connection, *, user_id: str, query_vector: list[float], similarity_threshold: float, limit: int
) -> list[dict[str, Any]]:
    """The user's nodes closest to `query_vector` by cosine similarity —
    stands in for the `match_nodes_euclidean` Supabase RPC that
    KeysExtractorAgent.key_similarity_search calls (that RPC is not in
    either repo's SQL, only in the old Supabase project)."""
    rows = conn.execute(
        text(
            "SELECT id, 1 - (summary_vec <=> CAST(:vec AS vector)) AS similarity FROM node_pool "
            "WHERE user_id = :user_id AND summary_vec IS NOT NULL AND status <> 'FAILED' "
            "AND 1 - (summary_vec <=> CAST(:vec AS vector)) >= :threshold "
            "ORDER BY summary_vec <=> CAST(:vec AS vector) LIMIT :limit"
        ),
        {"user_id": user_id, "vec": to_pgvector(query_vector), "threshold": similarity_threshold, "limit": limit},
    ).all()
    return [{"id": str(r.id), "similarity": float(r.similarity)} for r in rows]


def retrieve_candidate_nodes(
    conn: Connection,
    *,
    user_id: str,
    query_text: str,
    query_embedding: list[float],
    match_count: int = 30,
    org_id: str | None = None,
) -> list[dict[str, Any]]:
    """Candidate generation via retrieve_candidate_nodes: the user's own
    nodes, nodes from accepted person shares, and — with `org_id` — every
    node in that org's team space.

    org_id is always passed (NULL for personal scope): 0003 added a 7-arg
    overload with `org_id DEFAULT NULL` next to 0001's 6-arg version, so a
    6-arg call is rejected by Postgres as "function ... is not unique". With
    org_id NULL the 7-arg body is identical to the 6-arg one (its
    shared_nodes CTE reduces to the shared_user_id match only).
    """
    rows = conn.execute(
        text(
            "SELECT id, user_id, title, ai_title, summary, summary_vec, metadata, created_at, "
            "source_scope, candidate_type, similarity, lexical_rank "
            "FROM public.retrieve_candidate_nodes(CAST(:user_id AS uuid), CAST(:query_text AS text), "
            "CAST(:vec AS vector), 'merged'::text, CAST(:match_count AS integer), 'en'::text, "
            "CAST(:org_id AS uuid))"
        ),
        {
            "user_id": user_id,
            "query_text": query_text,
            "vec": to_pgvector(query_embedding),
            "match_count": match_count,
            "org_id": org_id,
        },
    ).all()
    results = []
    for r in rows:
        data = dict(r._mapping)
        data["id"] = str(data["id"])
        data["user_id"] = str(data["user_id"])
        data["summary_vec"] = from_pgvector(data["summary_vec"])
        data["metadata"] = data["metadata"] or {}
        if isinstance(data["metadata"], str):
            data["metadata"] = json.loads(data["metadata"])
        results.append(data)
    return results

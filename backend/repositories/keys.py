"""key_pool + node_keys access (personal scope: key_pool.user_id set,
org_id NULL)."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from db.vector import from_pgvector, to_pgvector


def get_or_create_key(conn: Connection, *, user_id: str, key_text: str) -> tuple[str, bool]:
    """Return (key_id, created). Relies on the existing
    key_pool_user_id_key_text_key unique index, like
    KeyRepository.batch_create_keys' duplicate handling in neurix."""
    row = conn.execute(
        text(
            "INSERT INTO key_pool (user_id, key_text) VALUES (:user_id, :key_text) "
            "ON CONFLICT (user_id, key_text) DO NOTHING RETURNING id"
        ),
        {"user_id": user_id, "key_text": key_text},
    ).first()
    if row is not None:
        return str(row[0]), True
    row = conn.execute(
        text("SELECT id FROM key_pool WHERE user_id = :user_id AND key_text = :key_text"),
        {"user_id": user_id, "key_text": key_text},
    ).first()
    return str(row[0]), False


def existing_key_texts(conn: Connection, *, user_id: str, key_texts: list[str]) -> set[str]:
    if not key_texts:
        return set()
    rows = conn.execute(
        text("SELECT key_text FROM key_pool WHERE user_id = :user_id AND key_text = ANY(CAST(:texts AS text[]))"),
        {"user_id": user_id, "texts": list(key_texts)},
    ).all()
    return {r.key_text for r in rows}


def link_node_key(conn: Connection, *, node_id: str, key_id: str, weight: float = 1.0) -> None:
    conn.execute(
        text(
            "INSERT INTO node_keys (node_id, key_id, weight) VALUES (:node_id, :key_id, :weight) "
            "ON CONFLICT (node_id, key_id) DO NOTHING"
        ),
        {"node_id": node_id, "key_id": key_id, "weight": weight},
    )


def get_keys_for_node(conn: Connection, node_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT kp.id, kp.key_text, kp.key_vec IS NOT NULL AS has_vec FROM node_keys nk "
            "JOIN key_pool kp ON kp.id = nk.key_id WHERE nk.node_id = :node_id ORDER BY kp.key_text"
        ),
        {"node_id": node_id},
    ).all()
    return [{"id": str(r.id), "key_text": r.key_text, "has_vec": r.has_vec} for r in rows]


def get_keys_for_nodes(conn: Connection, node_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """node_id -> [{id, key_text, key_vec}] (same shape the ranker consumes
    from NodeKeysRepository.get_keys_for_nodes in neurix)."""
    if not node_ids:
        return {}
    rows = conn.execute(
        text(
            "SELECT nk.node_id, kp.id, kp.key_text, kp.key_vec FROM node_keys nk "
            "JOIN key_pool kp ON kp.id = nk.key_id WHERE nk.node_id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": list(node_ids)},
    ).all()
    result: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        result.setdefault(str(r.node_id), []).append(
            {"id": str(r.id), "key_text": r.key_text, "key_vec": from_pgvector(r.key_vec)}
        )
    return result


def set_key_vec(conn: Connection, key_id: str, vec: list[float]) -> None:
    conn.execute(
        text("UPDATE key_pool SET key_vec = CAST(:vec AS vector) WHERE id = :id"),
        {"id": key_id, "vec": to_pgvector(vec)},
    )


def search_similar_key(
    conn: Connection, *, user_id: str, query_vector: list[float], threshold: float, exclude_key_id: str
) -> dict[str, Any] | None:
    """Best existing key of this user with cosine similarity >= threshold —
    the lookup KeyRepository.search_keys_by_vector does (limit 1) in
    GraphService.insert."""
    row = conn.execute(
        text(
            "SELECT id, key_text, 1 - (key_vec <=> CAST(:vec AS vector)) AS similarity FROM key_pool "
            "WHERE user_id = :user_id AND key_vec IS NOT NULL AND id <> :exclude "
            "AND 1 - (key_vec <=> CAST(:vec AS vector)) >= :threshold "
            "ORDER BY key_vec <=> CAST(:vec AS vector) LIMIT 1"
        ),
        {"user_id": user_id, "vec": to_pgvector(query_vector), "threshold": threshold, "exclude": exclude_key_id},
    ).first()
    if row is None:
        return None
    return {"id": str(row.id), "key_text": row.key_text, "similarity": float(row.similarity)}


def merge_key_into(conn: Connection, *, node_id: str, from_key_id: str, into_key_id: str) -> None:
    """Point this node at `into_key_id` instead of `from_key_id`, then drop
    `from_key_id` if nothing else references it."""
    conn.execute(
        text("DELETE FROM node_keys WHERE node_id = :node_id AND key_id = :from_id"),
        {"node_id": node_id, "from_id": from_key_id},
    )
    link_node_key(conn, node_id=node_id, key_id=into_key_id)
    conn.execute(
        text(
            "DELETE FROM key_pool WHERE id = :from_id "
            "AND NOT EXISTS (SELECT 1 FROM node_keys WHERE key_id = :from_id) "
            "AND NOT EXISTS (SELECT 1 FROM connection WHERE via_key_id = :from_id)"
        ),
        {"from_id": from_key_id},
    )


def nodes_sharing_key(conn: Connection, *, user_id: str, key_id: str, exclude_node_id: str) -> list[str]:
    """Other (non-failed) nodes of this user tagged with `key_id` — the
    NodeKeysRepository.get_nodes_by_keytexts lookup in GraphService.insert."""
    rows = conn.execute(
        text(
            "SELECT DISTINCT nk.node_id FROM node_keys nk JOIN node_pool np ON np.id = nk.node_id "
            "WHERE nk.key_id = :key_id AND np.user_id = :user_id AND np.id <> :exclude "
            "AND np.status <> 'FAILED'"
        ),
        {"key_id": key_id, "user_id": user_id, "exclude": exclude_node_id},
    ).all()
    return [str(r.node_id) for r in rows]

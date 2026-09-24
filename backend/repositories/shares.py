"""share_node access — reference model, never a node_pool copy (same as
neurix's ShareNodeRepository.upsert_share).

Unlike neurix, where a share is live the moment it is written
(is_active=true, no pending state) and either side can flip is_active off
later, Enterprise shares follow 0002's workflow:

- person share: 'pending' (is_active=false) -> 'active' (is_active=true) on
  Accept, or 'declined' (is_active=false, forever) on Decline. Only a pending
  invite can be cancelled; nothing here ever turns an active share off.
- team share: written 'active' directly, no accept step.

can_view_raw is the one flag that is independently revocable.
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

_COLUMNS = (
    "sn.id, sn.owner_user_id, sn.shared_user_id, sn.org_id, sn.node_id, sn.status, sn.is_active, "
    "sn.can_view_raw, sn.permission, sn.message, sn.metadata, np.title AS node_title"
)
_FROM = "FROM share_node sn JOIN node_pool np ON np.id = sn.node_id"


def _row(row) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row._mapping)
    for key in ("id", "owner_user_id", "shared_user_id", "org_id", "node_id"):
        data[key] = str(data[key]) if data[key] is not None else None
    if isinstance(data.get("metadata"), str):
        data["metadata"] = json.loads(data["metadata"])
    data["metadata"] = data.get("metadata") or {}
    data["node_title"] = data.get("node_title") or "Untitled"
    return data


def get_share(conn: Connection, share_id: str) -> dict[str, Any] | None:
    return _row(conn.execute(text(f"SELECT {_COLUMNS} {_FROM} WHERE sn.id = :id"), {"id": share_id}).first())


def _returning(conn: Connection, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Run a write that RETURNs share ids, then re-read with the node title."""
    share_id = conn.execute(text(sql), params).scalar_one_or_none()
    return get_share(conn, str(share_id)) if share_id is not None else None


# ---------------------------------------------------------------- person

def find_open_person_share(conn: Connection, *, node_id: str, recipient_id: str) -> dict[str, Any] | None:
    return _row(
        conn.execute(
            text(
                f"SELECT {_COLUMNS} {_FROM} WHERE sn.node_id = :node_id AND sn.shared_user_id = :recipient "
                "AND sn.status IN ('pending', 'active')"
            ),
            {"node_id": node_id, "recipient": recipient_id},
        ).first()
    )


def create_person_invite(
    conn: Connection, *, owner_id: str, recipient_id: str, node_id: str, message: str | None
) -> dict[str, Any]:
    return _returning(
        conn,
        "INSERT INTO share_node (owner_user_id, shared_user_id, node_id, message, permission, status, is_active) "
        "VALUES (:owner, :recipient, :node_id, :message, 'view', 'pending', false) RETURNING id",
        {"owner": owner_id, "recipient": recipient_id, "node_id": node_id, "message": message},
    )


def accept_invite(conn: Connection, share_id: str) -> dict[str, Any] | None:
    """pending -> active. None if the invite is no longer pending (already
    answered, cancelled, or a double click)."""
    return _returning(
        conn,
        "UPDATE share_node SET status = 'active', is_active = true, updated_at = now() "
        "WHERE id = :id AND status = 'pending' AND shared_user_id IS NOT NULL RETURNING id",
        {"id": share_id},
    )


def decline_invite(conn: Connection, share_id: str) -> dict[str, Any] | None:
    return _returning(
        conn,
        "UPDATE share_node SET status = 'declined', is_active = false, updated_at = now() "
        "WHERE id = :id AND status = 'pending' AND shared_user_id IS NOT NULL RETURNING id",
        {"id": share_id},
    )


def cancel_invite(conn: Connection, *, share_id: str, owner_id: str) -> dict[str, Any] | None:
    """Delete a still-pending invite (nothing references it yet: no
    connections are persisted before Accept). None if it was already
    answered — an active share can never be cancelled."""
    share = get_share(conn, share_id)
    deleted = conn.execute(
        text(
            "DELETE FROM share_node WHERE id = :id AND owner_user_id = :owner AND status = 'pending' RETURNING id"
        ),
        {"id": share_id, "owner": owner_id},
    ).first()
    return share if deleted else None


def merge_metadata(conn: Connection, share_id: str, patch: dict[str, Any]) -> None:
    conn.execute(
        text(
            "UPDATE share_node SET metadata = coalesce(metadata, '{}'::jsonb) || CAST(:patch AS jsonb), "
            "updated_at = now() WHERE id = :id"
        ),
        {"id": share_id, "patch": json.dumps(patch)},
    )


# ---------------------------------------------------------------- team

def find_team_share(conn: Connection, *, node_id: str, org_id: str) -> dict[str, Any] | None:
    return _row(
        conn.execute(
            text(f"SELECT {_COLUMNS} {_FROM} WHERE sn.node_id = :node_id AND sn.org_id = :org_id"),
            {"node_id": node_id, "org_id": org_id},
        ).first()
    )


def create_team_share(
    conn: Connection, *, owner_id: str, org_id: str, node_id: str, metadata: dict[str, Any]
) -> dict[str, Any]:
    return _returning(
        conn,
        "INSERT INTO share_node (owner_user_id, org_id, node_id, permission, status, is_active, metadata) "
        "VALUES (:owner, :org_id, :node_id, 'view', 'active', true, CAST(:metadata AS jsonb)) RETURNING id",
        {"owner": owner_id, "org_id": org_id, "node_id": node_id, "metadata": json.dumps(metadata)},
    )


# ---------------------------------------------------------------- raw access

def find_active_share(
    conn: Connection, *, node_id: str, recipient_id: str | None = None, org_id: str | None = None
) -> dict[str, Any] | None:
    """The active share of `node_id` to one person, or to one org."""
    return _row(
        conn.execute(
            text(
                f"SELECT {_COLUMNS} {_FROM} WHERE sn.node_id = :node_id AND sn.status = 'active' "
                "AND sn.is_active = true AND (sn.expires_at IS NULL OR sn.expires_at > now()) "
                "AND ((CAST(:recipient AS uuid) IS NOT NULL AND sn.shared_user_id = CAST(:recipient AS uuid)) "
                "OR (CAST(:org_id AS uuid) IS NOT NULL AND sn.org_id = CAST(:org_id AS uuid)))"
            ),
            {"node_id": node_id, "recipient": recipient_id, "org_id": org_id},
        ).first()
    )


def set_can_view_raw(conn: Connection, share_id: str, value: bool) -> None:
    conn.execute(
        text("UPDATE share_node SET can_view_raw = :value, updated_at = now() WHERE id = :id"),
        {"id": share_id, "value": value},
    )


def visible_nodes(conn: Connection, *, user_id: str, org_id: str | None) -> list[dict[str, Any]]:
    """Every non-failed node the user can see — owned, or reached through an
    active share (to them, or to their org) — with whether any of those
    grants carries can_view_raw. One row per node."""
    rows = conn.execute(
        text(
            "SELECT np.id, coalesce(np.title, np.ai_title, 'Untitled') AS title, np.user_id, np.metadata, "
            "np.user_id = :uid AS owned, "
            "coalesce(bool_or(sn.can_view_raw), false) AS can_view_raw "
            "FROM node_pool np "
            "LEFT JOIN share_node sn ON sn.node_id = np.id AND sn.status = 'active' AND sn.is_active = true "
            "  AND (sn.expires_at IS NULL OR sn.expires_at > now()) "
            "  AND (sn.shared_user_id = :uid OR (CAST(:org_id AS uuid) IS NOT NULL AND sn.org_id = CAST(:org_id AS uuid))) "
            "WHERE np.status <> 'FAILED' AND (np.user_id = :uid OR sn.id IS NOT NULL) "
            "GROUP BY np.id ORDER BY np.created_at DESC"
        ),
        {"uid": user_id, "org_id": org_id},
    ).all()
    result = []
    for r in rows:
        metadata = r.metadata or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        result.append(
            {
                "id": str(r.id),
                "title": r.title,
                "owner_id": str(r.user_id),
                "owned": bool(r.owned),
                "can_view_raw": bool(r.can_view_raw),
                "metadata": metadata,
            }
        )
    return result

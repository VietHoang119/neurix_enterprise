"""notifications access (the 0001 table + 0002's org_id/payload columns).

Rows here are the digest ones — shown and marked read by `/neurix stats`.
Immediate events (share invites, cross-member connections) go out as Slack
DMs instead and are not stored.
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

# notifications.type values written by this backend.
SHARE_ACCEPTED = "share_accepted"
SHARE_DECLINED = "share_declined"
SHARE_CONNECTIONS = "share_connections"
TEAM_NODE_ADDED = "team_node_added"
RAW_ACCESS_GRANTED = "raw_access_granted"
RAW_ACCESS_REVOKED = "raw_access_revoked"


def create(
    conn: Connection,
    *,
    user_id: str,
    type: str,
    message: str,
    share_node_id: str | None = None,
    org_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    # notifications.id has no DEFAULT in the ported schema.
    conn.execute(
        text(
            "INSERT INTO notifications (id, user_id, share_node_id, message, type, org_id, payload) "
            "VALUES (gen_random_uuid(), :user_id, :share_node_id, :message, :type, :org_id, CAST(:payload AS jsonb))"
        ),
        {
            "user_id": user_id,
            "share_node_id": share_node_id,
            "message": message,
            "type": type,
            "org_id": org_id,
            "payload": json.dumps(payload or {}),
        },
    )


def create_many(conn: Connection, *, user_ids: list[str], **kwargs: Any) -> int:
    for user_id in dict.fromkeys(user_ids):
        create(conn, user_id=user_id, **kwargs)
    return len(set(user_ids))


def take_unread(conn: Connection, user_id: str, limit: int = 20) -> tuple[list[dict[str, Any]], int]:
    """Return (the newest `limit` unread notifications, total unread) for
    the user — personal and org-scoped alike — and mark every unread one read."""
    rows = conn.execute(
        text(
            "UPDATE notifications SET is_read = true, read_at = now() "
            "WHERE user_id = :user_id AND is_read = false "
            "RETURNING type, message, org_id, created_at"
        ),
        {"user_id": user_id},
    ).all()
    ordered = sorted(rows, key=lambda r: r.created_at, reverse=True)
    items = [
        {"type": r.type, "message": r.message, "org_id": str(r.org_id) if r.org_id else None}
        for r in ordered[:limit]
    ]
    return items, len(rows)

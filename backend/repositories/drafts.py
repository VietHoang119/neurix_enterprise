"""node_drafts access — the preview -> edit -> confirm staging area.

The whole preview (title/summary/keys, node_payload, keys_payload — the same
shape neurix's NodeDraftService._build_preview produces) plus the Slack
message coordinates live in node_drafts.preview (jsonb). No new columns.
"""

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

_COLUMNS = "id, user_id, client_node_id, raw_content, source, preview, status, created_at"


def _row(row) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row._mapping)
    data["id"] = str(data["id"])
    data["user_id"] = str(data["user_id"])
    data["client_node_id"] = str(data["client_node_id"])
    if isinstance(data.get("preview"), str):
        data["preview"] = json.loads(data["preview"])
    return data


def create_draft(
    conn: Connection, *, user_id: str, raw_content: str, source: str | None, preview: dict[str, Any]
) -> dict[str, Any]:
    row = conn.execute(
        text(
            "INSERT INTO node_drafts (user_id, client_node_id, raw_content, source, preview, status) "
            "VALUES (:user_id, :client_node_id, :raw_content, :source, CAST(:preview AS jsonb), 'READY') "
            f"RETURNING {_COLUMNS}"
        ),
        {
            "user_id": user_id,
            "client_node_id": str(uuid.uuid4()),
            "raw_content": raw_content,
            "source": source,
            "preview": json.dumps(preview),
        },
    ).first()
    return _row(row)


def get_draft(conn: Connection, draft_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        text(f"SELECT {_COLUMNS} FROM node_drafts WHERE id = :id"), {"id": draft_id}
    ).first()
    return _row(row)


def find_draft_by_slack_message(conn: Connection, *, channel: str, ts: str) -> dict[str, Any] | None:
    row = conn.execute(
        text(
            f"SELECT {_COLUMNS} FROM node_drafts "
            "WHERE preview->'slack'->>'channel' = :channel AND preview->'slack'->>'ts' = :ts"
        ),
        {"channel": channel, "ts": ts},
    ).first()
    return _row(row)


def update_preview(conn: Connection, draft_id: str, preview: dict[str, Any]) -> None:
    conn.execute(
        text("UPDATE node_drafts SET preview = CAST(:preview AS jsonb), updated_at = now() WHERE id = :id"),
        {"id": draft_id, "preview": json.dumps(preview)},
    )


def claim_draft(conn: Connection, draft_id: str) -> dict[str, Any] | None:
    """Atomically delete and return a draft. Used by both Confirm and
    Discard, so a double-click (or Confirm racing Discard) can only ever be
    acted on once — the loser gets None."""
    row = conn.execute(
        text(f"DELETE FROM node_drafts WHERE id = :id RETURNING {_COLUMNS}"), {"id": draft_id}
    ).first()
    return _row(row)

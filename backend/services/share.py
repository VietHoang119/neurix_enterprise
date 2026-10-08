"""Sharing: the persist-connections job and the share rules around it.

Both share modes end in the same job, `persist_share_connections`:
- person share, on Accept: connections between the shared node and the
  recipient's own nodes, written with user_id = recipient;
- team share, on submit: connections between the shared node and the other
  nodes in the org's team space, written with org_id = the org.
Either way the rows are real `connection` rows (is_share=true,
share_node_id set), replacing neurix's live-at-query-time computation.

Everything here is synchronous DB work; Slack handlers run it in a thread.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from core.config import settings
from db.session import get_engine
from repositories import connections as connections_repo
from repositories import orgs as orgs_repo
from repositories import shares as shares_repo

logger = logging.getLogger(__name__)

NOTIFY_SCOPES = ("admin", "all", "specific")


class ShareError(ValueError):
    """A user-facing reason a share can't be made."""


@dataclass
class PersistResult:
    share: dict[str, Any]
    rows: int
    # Distinct nodes the shared node is now connected to: [{id, title, owner_id}]
    connected: list[dict[str, Any]] = field(default_factory=list)

    @property
    def cross_member(self) -> list[dict[str, Any]]:
        """Connected nodes owned by someone other than the shared node's owner."""
        return [n for n in self.connected if n["owner_id"] != self.share["owner_user_id"]]


def persist_share_connections(share_id: str) -> PersistResult:
    """Compute and write the share's connections (idempotent: a re-run
    replaces this share's rows rather than adding to them)."""
    with get_engine().begin() as conn:
        share = shares_repo.get_share(conn, share_id)
        if share is None or share["status"] != "active" or not share["is_active"]:
            raise ShareError("Only an active share forms connections.")
        person = share["shared_user_id"] is not None
        rows = connections_repo.share_connection_candidates(
            conn,
            node_id=share["node_id"],
            threshold=settings.KEYS_SIMILARITY_SCORE,
            recipient_id=share["shared_user_id"] if person else None,
            org_id=None if person else share["org_id"],
        )
        connections_repo.insert_share_connections(
            conn,
            share_node_id=share_id,
            rows=rows,
            user_id=share["shared_user_id"] if person else None,
            org_id=None if person else share["org_id"],
        )
        target_ids = list(dict.fromkeys(r["target_node"] for r in rows))
        connected = [
            {"id": str(r.id), "title": r.title, "owner_id": str(r.user_id)}
            for r in conn.execute(
                text(
                    "SELECT id, coalesce(title, ai_title, 'Untitled') AS title, user_id FROM node_pool "
                    "WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY title"
                ),
                {"ids": target_ids},
            ).all()
        ]
    logger.info(
        "SHARE_CONNECTIONS_PERSISTED | share_id=%s | scope=%s | rows=%d | connected_nodes=%d",
        share_id, "person" if person else "org", len(rows), len(connected),
    )
    return PersistResult(share=share, rows=len(rows), connected=connected)


def check_shareable_node(conn, *, user_id: str, node_id: str) -> dict[str, Any]:
    row = conn.execute(
        text("SELECT id, user_id, status, coalesce(title, ai_title, 'Untitled') AS title FROM node_pool WHERE id = :id"),
        {"id": node_id},
    ).first()
    if row is None or str(row.user_id) != user_id:
        raise ShareError("You can only share your own nodes.")
    if row.status != "READY":
        raise ShareError(f"'{row.title}' is still processing — try again once it's ready.")
    return {"id": str(row.id), "title": row.title}


def team_notify_recipients(conn, share: dict[str, Any]) -> list[str]:
    """Who gets the "node added to team space" notification, per the
    share's stored notify_scope — never the sharer themselves."""
    meta = share.get("metadata") or {}
    scope = meta.get("notify_scope") or "admin"
    members = orgs_repo.member_ids(conn, share["org_id"])
    if scope == "all":
        targets = members
    elif scope == "specific":
        wanted = set(meta.get("notify_targets") or [])
        targets = [m for m in members if m in wanted]
    else:
        targets = orgs_repo.member_ids(conn, share["org_id"], role="admin")
    return [t for t in targets if t != share["owner_user_id"]]

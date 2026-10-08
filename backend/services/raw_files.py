"""Who may open a note's original file, and its signed download link —
shared by Slack's `/neurix file` and the web node lists.

A note's original is openable when a file is stored for it (raw storage is
configured and metadata.raw_storage_path is set) and the user owns the note
or reaches it through a live share (to them, or to their team) carrying
can_view_raw.
"""

from typing import Any

from sqlalchemy.engine import Connection

from core.config import settings
from repositories import orgs as orgs_repo
from repositories import shares as shares_repo
from services import raw_storage


def fileable_nodes(conn: Connection, user_id: str) -> list[dict[str, Any]]:
    """Every note whose original the user can get a link for, newest first."""
    if raw_storage.get_raw_storage() is None:
        return []
    membership = orgs_repo.get_membership(conn, user_id)
    visible = shares_repo.visible_nodes(conn, user_id=user_id, org_id=membership["org_id"] if membership else None)
    return [n for n in visible if (n["owned"] or n["can_view_raw"]) and n["metadata"].get("raw_storage_path")]


def fileable_ids(conn: Connection, user_id: str) -> set[str]:
    return {n["id"] for n in fileable_nodes(conn, user_id)}


def find_fileable(conn: Connection, *, user_id: str, node_id: str) -> dict[str, Any] | None:
    """The note if the user can open its original now, else None."""
    return next((n for n in fileable_nodes(conn, user_id) if n["id"] == node_id), None)


def signed_link(node: dict[str, Any]) -> tuple[str, int]:
    """-> (signed URL, ttl seconds) for a node from `find_fileable`. Signing
    may call IAM, so run it outside the DB transaction."""
    ttl = settings.RAW_URL_TTL_SECONDS
    return raw_storage.get_raw_storage().signed_url(node["metadata"]["raw_storage_path"], ttl), ttl

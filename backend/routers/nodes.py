"""Web shell data: the signed-in user's node list, personal graph, and
original-file links."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from core.auth import get_current_uid
from db.session import get_engine
from repositories import connections as connections_repo
from repositories import nodes as nodes_repo
from repositories import shares as shares_repo
from repositories import users as users_repo
from services import raw_files

router = APIRouter()


def _list_nodes(user_id: str):
    """Own notes, plus notes shared with the caller by a person (accepted),
    each with whether its original file can be opened."""
    with get_engine().connect() as conn:
        openable = raw_files.fileable_ids(conn, user_id)
        own = nodes_repo.list_nodes(conn, user_id)
        shared = [n for n in shares_repo.visible_nodes(conn, user_id=user_id, org_id=None) if not n["owned"]]
        emails = users_repo.get_emails(conn, list({n["owner_id"] for n in shared}))
    return {
        "nodes": [{**n, "can_open_file": n["id"] in openable} for n in own],
        "shared_with_me": [
            {
                "id": n["id"],
                "title": n["title"],
                "status": n["status"],
                "owner_email": emails.get(n["owner_id"]),
                "can_open_file": n["id"] in openable,
            }
            for n in shared
        ],
    }


def _get_graph(user_id: str):
    with get_engine().connect() as conn:
        return connections_repo.get_graph(conn, user_id)


def _find_fileable(user_id: str, node_id: str):
    with get_engine().connect() as conn:
        return raw_files.find_fileable(conn, user_id=user_id, node_id=node_id)


@router.get("/nodes")
async def list_nodes(user_id: str = Depends(get_current_uid)):
    return await asyncio.to_thread(_list_nodes, user_id)


@router.get("/nodes/{node_id}/file")
async def get_file_link(node_id: str, user_id: str = Depends(get_current_uid)):
    """A short-lived signed download URL for the note's original file — the
    same access rule and link as Slack's `/neurix file`."""
    node = await asyncio.to_thread(_find_fileable, user_id, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="You don't have access to this node's original file.")
    url, ttl = await asyncio.to_thread(raw_files.signed_link, node)
    return {"url": url, "expires_in": ttl, "title": node["title"]}


@router.get("/graph")
async def get_graph(user_id: str = Depends(get_current_uid)):
    return await asyncio.to_thread(_get_graph, user_id)

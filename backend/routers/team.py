"""Web shell data for the signed-in user's team: the team graph."""

import asyncio

from fastapi import APIRouter, Depends

from core.auth import get_current_uid
from db.session import get_engine
from repositories import connections as connections_repo
from repositories import orgs as orgs_repo

router = APIRouter(prefix="/team")


def _team_graph(user_id: str):
    with get_engine().connect() as conn:
        membership = orgs_repo.get_membership(conn, user_id)
        if membership is None:
            return {"org": None, "members": [], "nodes": [], "connections": []}
        members = orgs_repo.list_members(conn, membership["org_id"])
        graph = connections_repo.get_team_graph(conn, membership["org_id"])
    return {
        "org": {"id": membership["org_id"], "name": membership["name"]},
        "members": [
            {"user_id": m["user_id"], "name": m["display_name"] or m["email"] or "Member", "is_me": m["user_id"] == user_id}
            for m in members
        ],
        **graph,
    }


@router.get("/graph")
async def team_graph(user_id: str = Depends(get_current_uid)):
    """Team space nodes (references to the originals, colored by original
    owner on the client) and the org-scoped connection rows between them,
    one edge per node pair."""
    return await asyncio.to_thread(_team_graph, user_id)

"""Web shell data for the signed-in user's team: the Team page (view,
create, invite by email, remove) and the team graph. Membership rules live
in services/team.py, shared with the Slack `/neurix team` commands."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from core.auth import get_current_uid
from db.session import get_engine
from repositories import connections as connections_repo
from repositories import orgs as orgs_repo
from services import raw_files
from services import team as team_service
from services.team import TeamError

router = APIRouter(prefix="/team")


class CreateTeamBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class InviteBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)


def _in_tx(fn, *args, **kwargs):
    def run():
        with get_engine().begin() as conn:
            return fn(conn, *args, **kwargs)

    return asyncio.to_thread(run)


def _team_error(exc: TeamError) -> HTTPException:
    code = status.HTTP_403_FORBIDDEN if str(exc) == team_service.NOT_ADMIN else status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(exc))


def _team_view(conn, user_id: str):
    membership, members = team_service.list_team(conn, user_id)
    if membership is None:
        return {"org": None, "members": []}
    return {
        "org": {"id": membership["org_id"], "name": membership["name"], "role": membership["role"]},
        "members": [
            {
                "user_id": m["user_id"],
                "name": m["display_name"] or m["email"] or "Member",
                "email": m["email"],
                "role": m["role"],
                "shared_notes": m["shared_notes"],
                "is_me": m["user_id"] == user_id,
            }
            for m in members
        ],
    }


@router.get("")
async def get_team(user_id: str = Depends(get_current_uid)):
    """The caller's team and its members ({org: null} when not in one)."""
    return await _in_tx(_team_view, user_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_team(body: CreateTeamBody, user_id: str = Depends(get_current_uid)):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Give your team a name.")

    def create(conn):
        membership, created = team_service.create_team(conn, user_id=user_id, name=name, display_name=None)
        if not created:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"You're already in team {membership['name']} — one team per person.",
            )
        return _team_view(conn, user_id)

    return await _in_tx(create)


@router.post("/members", status_code=status.HTTP_201_CREATED)
async def invite_member(body: InviteBody, user_id: str = Depends(get_current_uid)):
    """Add a member by the email they signed in to the web app with."""

    def add(conn):
        member_id = team_service.find_invitee(conn, body.email)
        if not member_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=team_service.NOT_SIGNED_IN)
        try:
            membership, existing = team_service.add_member(conn, admin_id=user_id, member_id=member_id, display_name=None)
        except TeamError as exc:
            raise _team_error(exc) from exc
        if existing:
            where = "your team" if existing["org_id"] == membership["org_id"] else "another team"
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"This person is already in {where}.")
        return _team_view(conn, user_id)

    return await _in_tx(add)


@router.delete("/members/{member_id}")
async def remove_member(member_id: str, user_id: str = Depends(get_current_uid)):
    def remove(conn):
        try:
            team_service.remove_member(conn, admin_id=user_id, member_id=member_id)
        except TeamError as exc:
            raise _team_error(exc) from exc
        return _team_view(conn, user_id)

    return await _in_tx(remove)


def _team_graph(conn, user_id: str):
    membership = orgs_repo.get_membership(conn, user_id)
    if membership is None:
        return {"org": None, "members": [], "nodes": [], "connections": []}
    members = orgs_repo.list_members(conn, membership["org_id"])
    graph = connections_repo.get_team_graph(conn, membership["org_id"])
    openable = raw_files.fileable_ids(conn, user_id)
    return {
        "org": {"id": membership["org_id"], "name": membership["name"]},
        "members": [
            {"user_id": m["user_id"], "name": m["display_name"] or m["email"] or "Member", "is_me": m["user_id"] == user_id}
            for m in members
        ],
        "nodes": [{**n, "can_open_file": n["id"] in openable} for n in graph["nodes"]],
        "connections": graph["connections"],
    }


@router.get("/graph")
async def team_graph(user_id: str = Depends(get_current_uid)):
    """Team space nodes (references to the originals, colored by original
    owner on the client) and the org-scoped connection rows between them,
    one edge per node pair."""
    return await _in_tx(_team_graph, user_id)

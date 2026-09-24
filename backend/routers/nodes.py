"""Web shell data: the signed-in user's node list and personal graph."""

import asyncio

from fastapi import APIRouter, Depends

from core.auth import get_current_uid
from db.session import get_engine
from repositories import connections as connections_repo
from repositories import nodes as nodes_repo

router = APIRouter()


def _list_nodes(user_id: str):
    with get_engine().connect() as conn:
        return nodes_repo.list_nodes(conn, user_id)


def _get_graph(user_id: str):
    with get_engine().connect() as conn:
        return connections_repo.get_graph(conn, user_id)


@router.get("/nodes")
async def list_nodes(user_id: str = Depends(get_current_uid)):
    return {"nodes": await asyncio.to_thread(_list_nodes, user_id)}


@router.get("/graph")
async def get_graph(user_id: str = Depends(get_current_uid)):
    return await asyncio.to_thread(_get_graph, user_id)

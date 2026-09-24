"""/neurix ask — retrieval (personal, or org scope for team members) +
grounded Gemini answer.

Retrieval mirrors neurix's ChatAgent `retrieve_user_data` tool
(app/agents/chat_agent.py): embed the question, candidate generation via
retrieve_candidate_nodes, rank_candidates + select_top_nodes(3, 0.8), then
the CHAT_RELEVANCE_THRESHOLD floor. The ReAct agent + Brave web fallback is
intentionally not ported: answers here must come only from the user's
nodes, so the retrieved nodes are passed straight into one grounded call.
"""

import asyncio
import logging
import re
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from core import llm
from core.config import settings
from db.session import get_engine
from repositories import connections as connections_repo
from repositories import keys as keys_repo
from repositories import nodes as nodes_repo
from services.retrieval_ranker import rank_candidates, select_top_nodes

logger = logging.getLogger(__name__)

# Adapted from neurix's ChatAgent prompt (RULE 1 "user data is the primary
# source of truth", RULE 3 "be explicit about sources"), minus the web
# fallback and with mandatory per-claim citations.
ANSWER_PROMPT = """You are Neurix, an assistant that answers questions strictly from the user's saved notes (their own, and notes shared with them or their team).

Rules:
- Answer ONLY from the numbered SOURCES below. Do not use outside knowledge.
- Cite the source for every claim with its number in square brackets, e.g. [1] or [1][3].
- If the sources do not contain the answer, say that the user's notes don't cover it — do not guess.
- Be concise. Respond in the language of the question.

SOURCES:
{sources}"""

NO_RESULTS_ANSWER = "I couldn't find anything in your saved notes that answers this."


@dataclass
class AskResult:
    answer: str
    sources: list[dict]  # [{n, id, title}] for each [n] the answer cites


def _format_sources(selected, node_keys_map) -> str:
    lines = []
    for i, ranked in enumerate(selected, start=1):
        node = ranked.node
        lines.append(f"[{i}] Title: {node.get('title') or node.get('ai_title') or 'Untitled'}")
        lines.append(f"Summary: {node.get('summary') or ''}")
        keys = ", ".join(k["key_text"] for k in node_keys_map.get(node["id"], []) if k.get("key_text"))
        if keys:
            lines.append(f"Keys: {keys}")
        lines.append("")
    return "\n".join(lines)


def cited_indices(answer: str, count: int) -> list[int]:
    """1-based source numbers cited as [n] in the answer, in first-cited order."""
    seen: list[int] = []
    for match in re.finditer(r"\[(\d+)\]", answer):
        n = int(match.group(1))
        if 1 <= n <= count and n not in seen:
            seen.append(n)
    return seen


def _retrieve(user_id: str, question: str, query_vector: list[float], org_id: str | None):
    with get_engine().connect() as conn:
        candidates = nodes_repo.retrieve_candidate_nodes(
            conn, user_id=user_id, query_text=question, query_embedding=query_vector, org_id=org_id
        )
        node_keys_map = keys_repo.get_keys_for_nodes(conn, [c["id"] for c in candidates])
    return candidates, node_keys_map


def _log(user_id: str, question: str, used_node_ids: list[str], org_id: str | None) -> None:
    with get_engine().begin() as conn:
        connections_repo.log_retrieval(
            conn, user_id=user_id, question=question, used_node_ids=used_node_ids, org_id=org_id
        )


async def answer_question(user_id: str, question: str, org_id: str | None = None) -> AskResult:
    query_vector = await llm.embed_query(question)
    candidates, node_keys_map = await asyncio.to_thread(_retrieve, user_id, question, query_vector, org_id)

    ranked = rank_candidates(question, query_vector, candidates, node_keys_map)
    selected = select_top_nodes(ranked, max_nodes=3, gap_ratio=0.8)
    selected = [r for r in selected if r.score >= settings.CHAT_RELEVANCE_THRESHOLD]
    logger.info(
        "ASK_RETRIEVAL | user_id=%s | org_id=%s | candidates=%d | selected=%s",
        user_id, org_id, len(candidates), [(r.node["id"], round(r.score, 3)) for r in selected],
    )

    if not selected:
        await asyncio.to_thread(_log, user_id, question, [], org_id)
        return AskResult(answer=NO_RESULTS_ANSWER, sources=[])

    response = await llm.get_chat_model().ainvoke(
        [
            SystemMessage(content=ANSWER_PROMPT.format(sources=_format_sources(selected, node_keys_map))),
            HumanMessage(content=question),
        ]
    )
    answer = response.content if isinstance(response.content, str) else str(response.content)

    sources = [
        {
            "n": n,
            "id": selected[n - 1].node["id"],
            "title": selected[n - 1].node.get("title") or selected[n - 1].node.get("ai_title") or "Untitled",
        }
        for n in cited_indices(answer, len(selected))
    ]
    await asyncio.to_thread(_log, user_id, question, [s["id"] for s in sources], org_id)
    return AskResult(answer=answer.strip(), sources=sources)

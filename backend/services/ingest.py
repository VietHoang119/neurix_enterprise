"""Ingest: preview -> confirm -> background processing.

- build_preview: port of neurix's NodeDraftService._build_preview (same
  KeysExtractorAgent call, same preview/node_payload/keys_payload shape).
- confirm_draft: inserts node_pool (status='PROCESSING') + node_keys.
- process_node: the post-confirm job — summary_vec (if edited), Stage 2 key
  dedup, connection generation, then status='READY' (or 'FAILED').
  Ports the key/connection logic of neurix's GraphService.insert, with one
  change the handoff asks for: a new key that matches an existing key at
  KEYS_SIMILARITY_SCORE is *merged* into it instead of being created
  alongside it.
"""

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig

from core import llm
from core.config import settings
from db.session import get_engine
from repositories import connections as connections_repo
from repositories import keys as keys_repo
from repositories import nodes as nodes_repo
from services.keys_extractor_agent import get_keys_extractor

logger = logging.getLogger(__name__)


def _estimate_token_count(text: str) -> int:
    # NodeDraftService._estimate_token_count
    return len(re.findall(r"\b\w+\b", text or ""))


def _fingerprint(text: str) -> str:
    # app/utils/content_fingerprint.py: sha256 of normalized text
    value = re.sub(r"[^\w\s]", " ", (text or "").lower().strip())
    value = re.sub(r"\s+", " ", value).strip()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def build_preview(*, user_id: str, raw_content: str, source: str | None) -> dict[str, Any]:
    result = await get_keys_extractor().ainvoke(
        input={"input_text": raw_content},
        config=RunnableConfig(configurable={"thread_id": str(uuid.uuid4()), "user_id": user_id}),
    )
    title = result.get("generated_title") or ""
    summary = result.get("generated_summary") or ""
    all_keys = result.get("all_keys") or []
    suggested = set(result.get("existing_keys") or [])
    existing_keys = [k for k in all_keys if k in suggested]

    return {
        "preview": {"title": title, "summary": summary, "keys": all_keys},
        "node_payload": {
            "title": title,
            "source": source,
            "size_tokens": _estimate_token_count(raw_content),
            "summary": summary,
            "summary_vec": result.get("summary_embedding") or [],
            "metadata": {},
        },
        "keys_payload": {
            "existing_keys": existing_keys,
            "new_keys": [k for k in all_keys if k not in suggested],
        },
        "summary_edited": False,
    }


@dataclass
class ConfirmedNode:
    node_id: str
    user_id: str
    title: str
    summary_edited: bool
    # key_id -> key_text for keys that went into key_pool fresh on confirm
    # (neither a reused suggestion nor an exact existing key): dedup targets.
    new_key_ids: dict[str, str] = field(default_factory=dict)


def confirm_draft(draft: dict[str, Any]) -> ConfirmedNode:
    """Insert the (possibly edited) draft as a PROCESSING node. The caller
    must already have claimed the draft (drafts_repo.claim_draft)."""
    payload = draft["preview"]
    node_payload = dict(payload["node_payload"])
    keys_payload = payload["keys_payload"]
    summary_edited = bool(payload.get("summary_edited"))
    user_id = draft["user_id"]

    title = node_payload.get("title") or ""
    summary = node_payload.get("summary") or ""
    node_payload.update(
        {
            "origin": "slack",
            "raw_content": draft["raw_content"],
            "fingerprint": _fingerprint(f"{title}\n{summary}"),
            # An edited summary's old vector no longer describes it; the
            # background job recomputes it.
            "summary_vec": None if summary_edited else (node_payload.get("summary_vec") or None),
        }
    )

    suggested = set(keys_payload.get("existing_keys") or [])
    final_keys = list(dict.fromkeys((payload["preview"].get("keys") or [])))
    new_key_ids: dict[str, str] = {}
    with get_engine().begin() as conn:
        node_id = nodes_repo.insert_processing_node(
            conn, user_id=user_id, client_node_id=draft["client_node_id"], node=node_payload
        )
        for key_text in final_keys:
            key_id, created = keys_repo.get_or_create_key(conn, user_id=user_id, key_text=key_text)
            keys_repo.link_node_key(conn, node_id=node_id, key_id=key_id)
            if created and key_text not in suggested:
                new_key_ids[key_id] = key_text

    return ConfirmedNode(
        node_id=node_id, user_id=user_id, title=title, summary_edited=summary_edited, new_key_ids=new_key_ids
    )


@dataclass
class ProcessResult:
    connected_nodes: int
    merges: list[tuple[str, str]]  # (typed key, existing key it was merged into)


async def process_node(confirmed: ConfirmedNode) -> ProcessResult:
    """The post-confirm background job. Sets READY on success; on any
    error sets FAILED (with metadata.error) and re-raises."""
    node_id, user_id = confirmed.node_id, confirmed.user_id
    try:
        # --- network work first (embeddings), no transaction held open ---
        with get_engine().connect() as conn:
            node = nodes_repo.get_node(conn, node_id)
            node_keys = keys_repo.get_keys_for_node(conn, node_id)
        summary_vec = None
        if confirmed.summary_edited or not node["summary_vec"]:
            summary_vec = await llm.embed_query(node["summary"] or node["title"] or "")

        # New keys, plus any pre-existing key that somehow never got a vector.
        to_embed = [k for k in node_keys if k["id"] in confirmed.new_key_ids or not k["has_vec"]]
        vectors = await llm.embed_documents([k["key_text"] for k in to_embed])

        # --- all writes in one transaction ---
        merges: list[tuple[str, str]] = []
        merged_score: dict[str, float] = {}  # final key_id -> similarity, for merged keys
        with get_engine().begin() as conn:
            if summary_vec is not None:
                nodes_repo.set_summary_vec(conn, node_id, summary_vec)

            for key, vec in zip(to_embed, vectors):
                if key["id"] in confirmed.new_key_ids:
                    match = keys_repo.search_similar_key(
                        conn,
                        user_id=user_id,
                        query_vector=vec,
                        threshold=settings.KEYS_SIMILARITY_SCORE,
                        exclude_key_id=key["id"],
                    )
                    if match:
                        keys_repo.merge_key_into(conn, node_id=node_id, from_key_id=key["id"], into_key_id=match["id"])
                        merges.append((key["key_text"], match["key_text"]))
                        merged_score[match["id"]] = match["similarity"]
                        continue
                keys_repo.set_key_vec(conn, key["id"], vec)

            # Connections: one row per (other node, shared key), like
            # GraphService.insert — score 1 for a shared key, the key
            # similarity for a key reached through a merge.
            rows: list[dict[str, Any]] = []
            connected: set[str] = set()
            for key in keys_repo.get_keys_for_node(conn, node_id):
                score = merged_score.get(key["id"], 1.0)
                for other in keys_repo.nodes_sharing_key(
                    conn, user_id=user_id, key_id=key["id"], exclude_node_id=node_id
                ):
                    rows.append({"source_node": node_id, "target_node": other, "score": score, "via_key_id": key["id"]})
                    connected.add(other)
            connections_repo.insert_connections(conn, user_id=user_id, rows=rows)
            nodes_repo.set_status(conn, node_id, "READY")

        logger.info(
            "NODE_PROCESSED | node_id=%s | connections=%d | connected_nodes=%d | merges=%s",
            node_id, len(rows), len(connected), merges,
        )
        return ProcessResult(connected_nodes=len(connected), merges=merges)
    except Exception as exc:
        logger.error("NODE_PROCESSING_FAILED | node_id=%s | error=%s", node_id, exc, exc_info=True)
        with get_engine().begin() as conn:
            nodes_repo.set_status(conn, node_id, "FAILED", error=str(exc)[:500])
        raise


def done_message(result: ProcessResult) -> str:
    n = result.connected_nodes
    parts = [f"✅ Saved — {n} new connection{'s' if n != 1 else ''}"]
    parts += [f"key '{typed}' merged into '{existing}'" for typed, existing in result.merges]
    return ", ".join(parts)

"""Verbatim port of neurix's app/agents/retrieval_ranker.py (rank_candidates,
select_top_nodes) — used by /neurix ask to rank retrieve_candidate_nodes
output exactly as neurix's ChatAgent retrieve_user_data tool does."""


from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence


def _sanitize_vector(vec: Sequence[Any]) -> list[float]:
    """Return a list of floats, skipping any non-numeric entries."""

    if not vec:
        return []

    cleaned: list[float] = []
    for value in vec:
        try:
            cleaned.append(float(value))
        except (TypeError, ValueError):
            continue
    return cleaned


def _cosine_similarity(vec1: Sequence[Any], vec2: Sequence[Any]) -> float:
    """Compute cosine similarity between two vectors.

    If either vector is empty or zero-valued, returns 0.0.
    Non-numeric values are ignored.
    """

    v1 = _sanitize_vector(vec1)
    v2 = _sanitize_vector(vec2)

    if not v1 or not v2:
        return 0.0

    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def _normalize_cosine(value: float) -> float:
    """Normalize cosine similarity into [0, 1]."""

    if -1.0 <= value <= 1.0:
        return (value + 1.0) / 2.0
    return min(max(value, 0.0), 1.0)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _lexical_overlap_score(query: str, candidate_text: str) -> float:
    """Simple token overlap ratio for lexical similarity."""

    query_tokens = _tokenize(query)
    candidate_tokens = _tokenize(candidate_text)
    if not query_tokens or not candidate_tokens:
        return 0.0
    query_set = set(query_tokens)
    candidate_set = set(candidate_tokens)
    overlap = len(query_set & candidate_set)
    return overlap / len(query_set)


def _safe_parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            cleaned = value.replace("Z", "+00:00")
            return datetime.fromisoformat(cleaned)
    except Exception:
        return None
    return None


def _compute_recency_score(
    created_at: Any,
    click_count: float,
    max_click_count: float,
    tau_days: float = 30.0,
    alpha: float = 0.7,
) -> float:
    """Combine recency decay with interaction count into a [0,1] score."""

    now = datetime.now(timezone.utc)
    created_dt = _safe_parse_datetime(created_at)
    if created_dt is None:
        created_dt = now

    age_days = max((now - created_dt).total_seconds() / 86400.0, 0.0)
    s_time = math.exp(-age_days / tau_days)

    if max_click_count > 0:
        s_click = math.log1p(click_count) / math.log1p(max_click_count)
    else:
        s_click = 0.0

    return alpha * s_time + (1 - alpha) * s_click


@dataclass
class RankedNode:
    node: Dict[str, Any]
    s_node: float
    s_key: float
    s_lex: float
    s_recency: float
    score: float


def rank_candidates(
    query: str,
    query_embedding: Sequence[float],
    candidates: Iterable[Dict[str, Any]],
    node_keys_map: Dict[str, List[Dict[str, Any]]] | None = None,
    weights: tuple[float, float, float, float] = (0.45, 0.30, 0.20, 0.05),
) -> list[RankedNode]:
    """Rank candidate nodes using semantic, lexical, and recency signals."""

    node_keys_map = node_keys_map or {}
    w1, w2, w3, w4 = weights
    candidates_list = list(candidates)

    max_click_count = max(
        [
            node.get("click_count")
            or node.get("metadata", {}).get("click_count")
            or 0
            for node in candidates_list
        ]
        or [0]
    )

    ranked: list[RankedNode] = []
    for node in candidates_list:
        node_emb = node.get("summary_vec") or node.get("embedding") or []
        s_node = _normalize_cosine(_cosine_similarity(query_embedding, node_emb))

        keys = node_keys_map.get(node.get("id"), [])
        key_scores = [
            _cosine_similarity(query_embedding, key.get("key_vec") or []) for key in keys
        ]
        s_key = _normalize_cosine(max(key_scores)) if key_scores else 0.0

        key_texts = [key.get("key_text", "") for key in keys]
        text_blob = " ".join(
            filter(
                None,
                [
                    node.get("title", ""),
                    node.get("ai_title", ""),
                    " ".join(key_texts),
                ],
            )
        )
        s_lex = _lexical_overlap_score(query, text_blob)

        click_count = node.get("click_count") or node.get("metadata", {}).get("click_count") or 0
        s_recency = _compute_recency_score(node.get("created_at"), click_count, max_click_count)

        score = w1 * s_node + w2 * s_key + w3 * s_lex + w4 * s_recency

        ranked.append(
            RankedNode(
                node=node,
                s_node=s_node,
                s_key=s_key,
                s_lex=s_lex,
                s_recency=s_recency,
                score=score,
            )
        )

    ranked.sort(key=lambda x: x.score, reverse=True)
    return ranked


def select_top_nodes(
    ranked_nodes: Sequence[RankedNode],
    max_nodes: int = 3,
    gap_ratio: float = 0.8,
) -> list[RankedNode]:
    """Select top nodes while ensuring diminishing returns are filtered out."""

    if not ranked_nodes:
        return []

    selected: list[RankedNode] = [ranked_nodes[0]]
    for node in ranked_nodes[1:max_nodes]:
        if node.score >= gap_ratio * selected[-1].score:
            selected.append(node)
        else:
            break
    return selected

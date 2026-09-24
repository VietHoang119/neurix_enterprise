"""Gemini model access, ported from neurix's app/core/llm.py (get_model) and
app/utils/gemini_embedding.py (GeminiEmbedding), trimmed to the two Google
models this backend actually uses.

Chat calls go through FallbackChatModel: GEMINI_MODEL first, then
GEMINI_MODEL_FALLBACKS in order, moving on only when a model raises 404
NotFound (Google retired it). Every other error propagates unchanged, after
langchain-google-genai's own retry/backoff.
"""

import logging
from collections.abc import Callable
from functools import cache
from typing import Any

from google.api_core.exceptions import NotFound
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

from core.config import settings

logger = logging.getLogger(__name__)

# Models that have answered NotFound in this process. They are skipped on
# later calls so each request doesn't pay the library's retry backoff on a
# retired model again; cleared by a restart/redeploy.
_unavailable_models: set[str] = set()


def chat_model_chain() -> list[str]:
    """GEMINI_MODEL, then GEMINI_MODEL_FALLBACKS, blanks and duplicates removed."""
    names = [settings.GEMINI_MODEL, *settings.GEMINI_MODEL_FALLBACKS.split(",")]
    return list(dict.fromkeys(n.strip() for n in names if n and n.strip()))


@cache
def _chat(model: str) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=settings.GOOGLE_API_KEY,
        temperature=0.5,
    )


class FallbackChatModel:
    """The subset of the chat model interface the services use (ainvoke,
    with_structured_output), run against the model chain."""

    def __init__(self, build: Callable[[ChatGoogleGenerativeAI], Runnable] = lambda m: m):
        self._build = build

    def with_structured_output(self, schema: Any) -> "FallbackChatModel":
        return FallbackChatModel(lambda m: self._build(m).with_structured_output(schema))

    async def ainvoke(self, messages: Any, config: Any = None) -> Any:
        chain = chat_model_chain()
        candidates = [m for m in chain if m not in _unavailable_models] or chain
        if candidates[0] != chain[0]:
            logger.warning(
                "GEMINI_MODEL_FALLBACK | primary=%s previously returned NotFound, using=%s "
                "| update GEMINI_MODEL",
                chain[0], candidates[0],
            )
        for i, name in enumerate(candidates):
            try:
                return await self._build(_chat(name)).ainvoke(messages, config)
            except NotFound as exc:
                _unavailable_models.add(name)
                if i + 1 == len(candidates):
                    logger.error("GEMINI_MODEL_FALLBACK | model=%s NotFound, no fallbacks left", name)
                    raise
                logger.warning(
                    "GEMINI_MODEL_FALLBACK | model=%s NotFound (%s), falling back to=%s "
                    "| update GEMINI_MODEL",
                    name, exc, candidates[i + 1],
                )
        raise AssertionError("unreachable")


@cache
def get_chat_model() -> FallbackChatModel:
    return FallbackChatModel()


@cache
def get_embedding_model() -> GoogleGenerativeAIEmbeddings:
    return GoogleGenerativeAIEmbeddings(
        model=settings.EMBEDDING_MODEL,
        google_api_key=settings.GOOGLE_API_KEY,
    )


def _check_dim(vec: list[float]) -> list[float]:
    if not vec or len(vec) != settings.GEMINI_EMBEDDING_DIM:
        raise ValueError("Embedding value error: None or incorrect dimension")
    return vec


async def embed_query(text: str) -> list[float]:
    """Same call as GeminiEmbedding.aembed_query / KeysExtractorAgent's
    summary embedding (task_type RETRIEVAL_QUERY, 768 dims)."""
    vec = await get_embedding_model().aembed_query(
        text=text,
        task_type="RETRIEVAL_QUERY",
        output_dimensionality=settings.GEMINI_EMBEDDING_DIM,
    )
    return _check_dim(vec)


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """Same call as GeminiEmbedding.aembed_docs (used for key_vec in
    GraphService.insert)."""
    if not texts:
        return []
    vecs = await get_embedding_model().aembed_documents(
        texts=texts,
        task_type="RETRIEVAL_DOCUMENT",
        output_dimensionality=settings.GEMINI_EMBEDDING_DIM,
    )
    return [_check_dim(v) for v in vecs]

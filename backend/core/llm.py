"""Gemini model access, ported from neurix's app/core/llm.py (get_model) and
app/utils/gemini_embedding.py (GeminiEmbedding), trimmed to the two Google
models this backend actually uses.
"""

from functools import cache

from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

from core.config import settings


@cache
def get_chat_model() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=settings.DEFAULT_MODEL,
        google_api_key=settings.GOOGLE_API_KEY,
        temperature=0.5,
    )


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

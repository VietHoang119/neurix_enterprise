"""Port of neurix's app/agents/keys_extractor_agent.py (KeysExtractorAgent).

Same four-step LangGraph pipeline, same prompts, same structured-output
schemas: generate_node_data -> generate_summary_embedding ->
get_similar_keys -> generate_keywords. Only the data access changed:
neurix's NodeRepository / NodeKeysRepository (Supabase) are replaced by the
SQL repositories in this backend.

One deliberate difference: neurix's NodeRepository.search_nodes_by_vector
ignores its own `similarity_threshold`/`limit` arguments and hardcodes
match_threshold=0.0, match_count=1 in the RPC call. Here the documented
arguments (SIMILARITY_THRESHOLD, TOP_K_NODE) are honored.
"""

import asyncio
import re
from typing import Optional, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from core import llm
from core.config import settings
from db.session import get_engine
from repositories import keys as keys_repo
from repositories import nodes as nodes_repo


class KeysExtractorState(TypedDict, total=False):
    input_text: str
    generated_title: Optional[str]
    generated_summary: Optional[str]
    generated_keys: Optional[list[str]]
    summary_embedding: Optional[list[float]]
    existing_keys: Optional[list[str]]
    all_keys: Optional[list[str]]
    new_keys: Optional[list[str]]


class TitleSummaryResult(BaseModel):
    """Result from title and summary generation."""
    title: str = Field(..., description="Generated title for the text")
    summary: str = Field(..., description="Generated summary of the text")
    generated_keys: list[str] = Field(..., description="Extracted keywords from the text")


class KeysResult(BaseModel):
    """Result from keywords generation."""
    all_keys: list[str] = Field(..., description="Final list of standardized keywords")


keyword_extraction_prompt = """
You are a Keyword Discovery Agent. Extract keywords that best represent the given summary content.

Summary: {summary}
Existing Keywords: {database_keywords}

<task>
1. Identify key concepts, topics, and terms from the summary
2. For each concept:
   - Use existing database keyword if it represents the same concept
   - Use novel keyword from summary if no database match exists
3. Select the most content-relevant keywords that capture the summary's essence

<guidelines>
- Prefer existing keywords for consistency when meaning aligns
- Include novel keywords when they add unique value
- Return {max_keys} keywords maximum
- Sort by relevance to summary content (descending)

<output_format>
["most_relevant_keyword", "second_most_relevant", "third_most_relevant"]
"""


class KeysExtractorAgent:
    @staticmethod
    def _number_of_keys(summary: str) -> int:
        words = re.findall(r"\b\w+\b", summary)
        return max(settings.MIN_KEYS, min(settings.MAX_KEYS, int(len(words) / 100)))

    @staticmethod
    def key_similarity_search(
        summary_embedding: list[float],
        user_id: str,
        similarity_threshold: float,
        nodes_limit: int,
    ) -> list[str]:
        """Keys of the user's nodes most similar to the summary (normalized,
        deduplicated) — the pool the keyword step prefers to reuse from."""
        with get_engine().connect() as conn:
            similar = nodes_repo.search_similar_nodes(
                conn,
                user_id=user_id,
                query_vector=summary_embedding,
                similarity_threshold=similarity_threshold,
                limit=nodes_limit,
            )
            all_keys: set[str] = set()
            for node in similar:
                for key in keys_repo.get_keys_for_node(conn, node["id"]):
                    if key["key_text"]:
                        all_keys.add(key["key_text"].strip().lower())
        return list(all_keys)

    async def generate_node_data(self, state: KeysExtractorState, config: RunnableConfig) -> KeysExtractorState:
        """Generate title, summary, and pre-keys from input text."""
        input_text = state.get("input_text", "")
        if not input_text or input_text.strip() == "":
            raise ValueError("Input text is required for node data generation.")

        model = llm.get_chat_model().with_structured_output(TitleSummaryResult)

        prompt = f"""
        Please analyze the following text and produce:

Title — a concise, descriptive title that captures the main idea.

Summary — summarize the text while preserving the writer’s original tone, viewpoint, and intent. Keep all key informative and specific details. Do not introduce new keywords; only paraphrase or reuse wording from the original text.

Keywords — list relevant keywords drawn directly from the text.

Text to analyze:
{input_text}

Please respond with the title, summary, and keywords, and call the TitleSummaryResult tool.
        """
        try:
            response = await model.ainvoke([HumanMessage(content=prompt)])
        except Exception as e:
            raise RuntimeError("Failed to generate title and summary.") from e
        return {
            "generated_title": response.title,
            "generated_summary": response.summary,
            "generated_keys": response.generated_keys,
        }

    async def generate_summary_embedding_node(self, state: KeysExtractorState, config: RunnableConfig) -> KeysExtractorState:
        summary = state.get("generated_summary", "")
        if not summary or summary.strip() == "":
            raise ValueError("Generated summary cannot be empty for embedding generation.")
        return {"summary_embedding": await llm.embed_query(summary)}

    async def get_similar_keys_node(self, state: KeysExtractorState, config: RunnableConfig) -> KeysExtractorState:
        summary_embedding = state.get("summary_embedding") or []
        if len(summary_embedding) != settings.GEMINI_EMBEDDING_DIM:
            raise ValueError("Generated summary vector is invalid.")
        existing_keys = await asyncio.to_thread(
            self.key_similarity_search,
            summary_embedding,
            config["configurable"]["user_id"],
            settings.SIMILARITY_THRESHOLD,
            settings.TOP_K_NODE,
        )
        return {"existing_keys": existing_keys}

    async def generate_keywords_node(self, state: KeysExtractorState, config: RunnableConfig) -> KeysExtractorState:
        """Generate final keywords using pre-generated keys and associated keys."""
        summary = state.get("generated_summary", "")
        existing_keys = state.get("existing_keys", [])
        num_keys = self._number_of_keys(summary)

        model = llm.get_chat_model().with_structured_output(KeysResult)
        prompt = keyword_extraction_prompt.format(
            summary=summary,
            database_keywords=existing_keys,
            max_keys=num_keys,
        )
        system_msg = SystemMessage(content=prompt)
        human_msg = HumanMessage(content="Extract keywords that best represent the core concepts and main topics in this text.")
        try:
            response = await model.ainvoke([system_msg, human_msg])
        except Exception as e:
            raise RuntimeError("Failed to generate final keywords.") from e
        all_keys = response.all_keys
        if not all_keys:
            raise RuntimeError("No keywords generated by the model.")

        # Deduplicate and normalize (order-preserving, unlike neurix's set())
        all_keys = list(dict.fromkeys(k.strip().lower() for k in all_keys[:num_keys] if k.strip()))
        new_keys = [key for key in all_keys if key not in existing_keys]
        return {"new_keys": new_keys, "all_keys": all_keys}

    def build_graph(self) -> CompiledStateGraph:
        graph = StateGraph(KeysExtractorState)
        graph.add_node("generate_node_data", self.generate_node_data)
        graph.add_node("generate_summary_embedding", self.generate_summary_embedding_node)
        graph.add_node("get_similar_keys", self.get_similar_keys_node)
        graph.add_node("generate_keywords", self.generate_keywords_node)
        graph.add_edge(START, "generate_node_data")
        graph.add_edge("generate_node_data", "generate_summary_embedding")
        graph.add_edge("generate_summary_embedding", "get_similar_keys")
        graph.add_edge("get_similar_keys", "generate_keywords")
        graph.add_edge("generate_keywords", END)
        return graph.compile()


_agent: CompiledStateGraph | None = None


def get_keys_extractor() -> CompiledStateGraph:
    global _agent
    if _agent is None:
        _agent = KeysExtractorAgent().build_graph()
    return _agent

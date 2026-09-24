"""GEMINI_MODEL / GEMINI_MODEL_FALLBACKS: 404 NotFound moves to the next
model; every other error propagates untouched."""

import asyncio
import logging

import pytest
from google.api_core.exceptions import NotFound, ResourceExhausted
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from core import llm
from core.config import settings


class Answer(BaseModel):
    text: str


class FakeModel:
    def __init__(self, name: str, calls: list[str], error: Exception | None = None):
        self.name, self.calls, self.error = name, calls, error
        self.schema = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    async def ainvoke(self, messages, config=None):
        self.calls.append(self.name)
        if self.error:
            raise self.error
        if self.schema:
            return self.schema(text=self.name)
        return AIMessage(content=self.name)


@pytest.fixture
def models(monkeypatch):
    """Map model name -> error it raises (None = works); records call order."""
    calls: list[str] = []
    errors: dict[str, Exception | None] = {}
    monkeypatch.setattr(llm, "_chat", lambda name: FakeModel(name, calls, errors.get(name)))
    monkeypatch.setattr(settings, "GEMINI_MODEL", "primary")
    monkeypatch.setattr(settings, "GEMINI_MODEL_FALLBACKS", "primary, second ,third")
    llm._unavailable_models.clear()
    yield errors, calls
    llm._unavailable_models.clear()


def _invoke(model, text: str = "hi"):
    return asyncio.run(model.ainvoke([HumanMessage(content=text)]))


def _retired(name: str) -> NotFound:
    return NotFound(f"This model models/{name} is no longer available to new users.")


def test_chain_reads_settings_and_dedupes(models):
    assert llm.chat_model_chain() == ["primary", "second", "third"]


def test_defaults_use_current_model():
    assert settings.GEMINI_MODEL == "gemini-3.6-flash"


def test_primary_used_when_available(models):
    errors, calls = models
    result = _invoke(llm.FallbackChatModel(), "hi")
    assert result.content == "primary"
    assert calls == ["primary"]


def test_not_found_falls_through_to_next_model(models, caplog):
    errors, calls = models
    errors["primary"] = _retired("primary")
    errors["second"] = _retired("second")

    with caplog.at_level(logging.WARNING, logger="core.llm"):
        result = _invoke(llm.FallbackChatModel(), "hi")

    assert result.content == "third"
    assert calls == ["primary", "second", "third"]
    assert "model=primary NotFound" in caplog.text
    assert "falling back to=second" in caplog.text


def test_structured_output_falls_back(models):
    errors, calls = models
    errors["primary"] = _retired("primary")
    result = _invoke(llm.FallbackChatModel().with_structured_output(Answer), "hi")
    assert result == Answer(text="second")


def test_retired_model_skipped_on_later_calls(models, caplog):
    errors, calls = models
    errors["primary"] = _retired("primary")
    _invoke(llm.FallbackChatModel(), "a")
    calls.clear()

    with caplog.at_level(logging.WARNING, logger="core.llm"):
        result = _invoke(llm.FallbackChatModel(), "b")
    assert result.content == "second"
    assert calls == ["second"]
    assert "primary=primary previously returned NotFound, using=second" in caplog.text


def test_all_models_not_found_raises(models):
    errors, calls = models
    for name in ("primary", "second", "third"):
        errors[name] = _retired(name)
    with pytest.raises(NotFound):
        _invoke(llm.FallbackChatModel(), "hi")
    assert calls == ["primary", "second", "third"]


@pytest.mark.parametrize("error", [ResourceExhausted("quota"), TimeoutError("slow"), RuntimeError("boom")])
def test_other_errors_do_not_fall_back(models, error):
    errors, calls = models
    errors["primary"] = error
    with pytest.raises(type(error)):
        _invoke(llm.FallbackChatModel(), "hi")
    assert calls == ["primary"]
    assert not llm._unavailable_models

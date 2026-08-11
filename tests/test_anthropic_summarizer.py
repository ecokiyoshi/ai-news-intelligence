from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.anthropic_summarizer import SUMMARY_INSTRUCTIONS, AnthropicSummarizer
from app.database import create_db_engine, init_db
from app.models import NewsArticle
from app.summarization import (
    EmptySummaryResultError,
    Summarizer,
    SummaryResult,
    summarize_article,
)


class FakeMessages:
    def __init__(self, text: str = "Summary") -> None:
        self.text = text
        self.calls: list[dict] = []

    def create(self, *, model: str, max_tokens: int, system: str, messages: list[dict]):
        self.calls.append(
            {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        )
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)])


class FakeClient:
    def __init__(self, messages: FakeMessages) -> None:
        self.messages = messages


def accepts_summarizer(summarizer: Summarizer) -> SummaryResult:
    return summarizer.summarize("Article text")


def test_anthropic_summarizer_implements_existing_interface() -> None:
    messages = FakeMessages("Interface-compatible summary")
    summarizer = AnthropicSummarizer(client=FakeClient(messages), model="test-model")

    result = accepts_summarizer(summarizer)

    assert result == SummaryResult(summary="Interface-compatible summary")


def test_messages_api_output_is_trimmed_and_returned() -> None:
    messages = FakeMessages("  Concise summary. \n")
    summarizer = AnthropicSummarizer(client=FakeClient(messages), model="test-model")

    result = summarizer.summarize("Full article text")

    assert result == SummaryResult(summary="Concise summary.")
    call = messages.calls[0]
    assert call["model"] == "test-model"
    assert call["system"] == SUMMARY_INSTRUCTIONS
    assert call["messages"] == [{"role": "user", "content": "Full article text"}]


def test_model_can_be_configured_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "environment-model")
    messages = FakeMessages()
    summarizer = AnthropicSummarizer(client=FakeClient(messages))

    summarizer.summarize("Article text")

    assert messages.calls[0]["model"] == "environment-model"


def test_default_client_receives_configured_timeout(monkeypatch) -> None:
    created_client = FakeClient(FakeMessages())
    received_timeouts: list[float] = []

    def create_client(timeout: float):
        received_timeouts.append(timeout)
        return created_client

    monkeypatch.setattr("app.anthropic_summarizer.build_default_client", create_client)

    summarizer = AnthropicSummarizer(model="test-model", timeout=12.5)

    assert summarizer.client is created_client
    assert received_timeouts == [12.5]


def test_empty_output_raises_existing_error() -> None:
    summarizer = AnthropicSummarizer(
        client=FakeClient(FakeMessages(" \t\n")),
        model="test-model",
    )

    with pytest.raises(EmptySummaryResultError):
        summarizer.summarize("Article text")


def test_client_exception_propagates() -> None:
    expected_error = RuntimeError("API unavailable")

    class FailingMessages:
        def create(self, **kwargs):
            raise expected_error

    client = SimpleNamespace(messages=FailingMessages())
    summarizer = AnthropicSummarizer(client=client, model="test-model")

    with pytest.raises(RuntimeError) as exc_info:
        summarizer.summarize("Article text")

    assert exc_info.value is expected_error


def test_provider_failure_rolls_back_article_and_keeps_session_usable(tmp_path) -> None:
    class FailingMessages:
        def create(self, **kwargs):
            raise RuntimeError("API unavailable")

    engine = create_db_engine(f"sqlite:///{tmp_path / 'anthropic-failure.db'}")
    init_db(engine)
    with Session(engine) as session:
        article = NewsArticle(
            title="AI news",
            url="https://example.com/ai-news",
            source="Example News",
        )
        session.add(article)
        session.commit()

        summarizer = AnthropicSummarizer(
            client=SimpleNamespace(messages=FailingMessages()),
            model="test-model",
        )
        with pytest.raises(RuntimeError, match="API unavailable"):
            summarize_article(article, "Explicit article text", summarizer, session)

        saved_article = session.scalar(
            select(NewsArticle).where(NewsArticle.id == article.id)
        )
        assert saved_article is not None
        assert saved_article.summary is None
        assert saved_article.summarized_at is None
        assert session.scalar(select(NewsArticle.id)) == article.id
    engine.dispose()

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import create_db_engine, init_db
from app.models import NewsArticle
from app.openai_summarizer import OpenAISummarizer, SUMMARY_INSTRUCTIONS
from app.summarization import (
    EmptySummaryResultError,
    Summarizer,
    SummaryResult,
    summarize_article,
)


class FakeResponses:
    def __init__(self, output_text: str = "Summary") -> None:
        self.output_text = output_text
        self.calls: list[dict[str, str]] = []

    def create(self, *, model: str, instructions: str, input: str):
        self.calls.append(
            {"model": model, "instructions": instructions, "input": input}
        )
        return SimpleNamespace(output_text=self.output_text)


class FakeClient:
    def __init__(self, responses: FakeResponses) -> None:
        self.responses = responses


def accepts_summarizer(summarizer: Summarizer) -> SummaryResult:
    return summarizer.summarize("Article text")


def test_openai_summarizer_implements_existing_interface() -> None:
    responses = FakeResponses("Interface-compatible summary")
    summarizer = OpenAISummarizer(client=FakeClient(responses), model="test-model")

    result = accepts_summarizer(summarizer)

    assert result == SummaryResult(summary="Interface-compatible summary")


def test_responses_api_output_is_trimmed_and_returned() -> None:
    responses = FakeResponses("  Concise summary. \n")
    summarizer = OpenAISummarizer(client=FakeClient(responses), model="test-model")

    result = summarizer.summarize("Full article text")

    assert result == SummaryResult(summary="Concise summary.")
    assert responses.calls == [
        {
            "model": "test-model",
            "instructions": SUMMARY_INSTRUCTIONS,
            "input": "Full article text",
        }
    ]


def test_model_can_be_configured_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "environment-model")
    responses = FakeResponses()
    summarizer = OpenAISummarizer(client=FakeClient(responses))

    summarizer.summarize("Article text")

    assert responses.calls[0]["model"] == "environment-model"


def test_default_client_receives_configured_timeout(monkeypatch) -> None:
    created_client = FakeClient(FakeResponses())
    received_timeouts: list[float] = []

    def create_client(*, timeout: float):
        received_timeouts.append(timeout)
        return created_client

    monkeypatch.setattr("app.openai_summarizer.OpenAI", create_client)

    summarizer = OpenAISummarizer(model="test-model", timeout=12.5)

    assert summarizer.client is created_client
    assert received_timeouts == [12.5]


def test_empty_output_raises_existing_error() -> None:
    summarizer = OpenAISummarizer(
        client=FakeClient(FakeResponses(" \t\n")),
        model="test-model",
    )

    with pytest.raises(EmptySummaryResultError):
        summarizer.summarize("Article text")


def test_client_exception_propagates() -> None:
    expected_error = RuntimeError("API unavailable")

    class FailingResponses:
        def create(self, **kwargs):
            raise expected_error

    client = SimpleNamespace(responses=FailingResponses())
    summarizer = OpenAISummarizer(client=client, model="test-model")

    with pytest.raises(RuntimeError) as exc_info:
        summarizer.summarize("Article text")

    assert exc_info.value is expected_error


def test_provider_failure_rolls_back_article_and_keeps_session_usable(tmp_path) -> None:
    class FailingResponses:
        def create(self, **kwargs):
            raise RuntimeError("API unavailable")

    engine = create_db_engine(f"sqlite:///{tmp_path / 'openai-failure.db'}")
    init_db(engine)
    with Session(engine) as session:
        article = NewsArticle(
            title="AI news",
            url="https://example.com/ai-news",
            source="Example News",
        )
        session.add(article)
        session.commit()

        summarizer = OpenAISummarizer(
            client=SimpleNamespace(responses=FailingResponses()),
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

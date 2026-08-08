from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import create_db_engine, init_db
from app.models import NewsArticle
from app.summarization import (
    EmptySummaryInputError,
    LocalSummarizer,
    SummaryResult,
    summarize_article,
)


def create_article(session: Session) -> NewsArticle:
    article = NewsArticle(
        title="AI news",
        url="https://example.com/ai-news",
        source="Example News",
    )
    session.add(article)
    session.commit()
    return article


def test_local_summarizer_is_deterministic() -> None:
    summarizer = LocalSummarizer(max_length=24)
    text = "A longer article text that can be summarized locally."

    first_result = summarizer.summarize(text)
    second_result = summarizer.summarize(text)

    assert first_result == second_result
    assert isinstance(first_result, SummaryResult)
    assert first_result.summary == "A longer article text th"


def test_summarize_article_persists_summary_and_utc_timestamp(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'summary.db'}")
    init_db(engine)
    with Session(engine) as session:
        article = create_article(session)

        result = summarize_article(
            article,
            "Full article text for the local summarizer.",
            LocalSummarizer(),
            session,
        )
        article_id = article.id

    with Session(engine) as session:
        saved_article = session.get(NewsArticle, article_id)
        assert saved_article is not None
        assert saved_article.summary == result.summary
        assert saved_article.summarized_at is not None
        assert saved_article.summarized_at.tzinfo is not None
        assert saved_article.summarized_at.utcoffset().total_seconds() == 0
    engine.dispose()


@pytest.mark.parametrize("text", ["", " \t\n "])
def test_empty_input_does_not_update_article(tmp_path, text: str) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    init_db(engine)
    with Session(engine) as session:
        article = create_article(session)

        with pytest.raises(EmptySummaryInputError):
            summarize_article(article, text, LocalSummarizer(), session)

        assert article.summary is None
        assert article.summarized_at is None
    engine.dispose()


def test_summarizer_failure_preserves_article_and_session(tmp_path) -> None:
    class FailingSummarizer:
        def summarize(self, text: str) -> SummaryResult:
            raise RuntimeError("provider failed")

    engine = create_db_engine(f"sqlite:///{tmp_path / 'failure.db'}")
    init_db(engine)
    previous_timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with Session(engine) as session:
        article = create_article(session)
        article.summary = "Existing summary"
        article.summarized_at = previous_timestamp
        session.commit()

        with pytest.raises(RuntimeError, match="provider failed"):
            summarize_article(article, "New article text", FailingSummarizer(), session)

        saved_article = session.scalar(
            select(NewsArticle).where(NewsArticle.id == article.id)
        )
        assert saved_article is not None
        assert saved_article.summary == "Existing summary"
        assert saved_article.summarized_at == previous_timestamp
        queried_id = session.scalar(
            select(NewsArticle.id).where(NewsArticle.id == article.id)
        )
        assert queried_id == article.id
    engine.dispose()

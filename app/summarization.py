"""Provider-independent article summarization service."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.orm import Session

from app.models import NewsArticle


@dataclass(frozen=True)
class SummaryResult:
    """Structured output returned by a summarizer."""

    summary: str


class Summarizer(Protocol):
    """Interface implemented by article summary providers."""

    def summarize(self, text: str) -> SummaryResult:
        """Summarize the supplied article text."""


class EmptySummaryInputError(ValueError):
    """Raised when summarization is requested without usable text."""


class EmptySummaryResultError(ValueError):
    """Raised when a summarizer returns no usable summary."""


class LocalSummarizer:
    """Deterministic local summarizer for development and tests."""

    def __init__(self, max_length: int = 200) -> None:
        if max_length < 1:
            raise ValueError("max_length must be positive")
        self.max_length = max_length

    def summarize(self, text: str) -> SummaryResult:
        normalized = " ".join(text.split())
        return SummaryResult(summary=normalized[: self.max_length])


def summarize_article(
    article: NewsArticle,
    text: str,
    summarizer: Summarizer,
    session: Session,
) -> SummaryResult:
    """Summarize explicit text and persist the result on an article."""

    normalized_text = text.strip()
    if not normalized_text:
        raise EmptySummaryInputError("article text must not be empty")

    try:
        result = summarizer.summarize(normalized_text)
        summary = result.summary.strip()
        if not summary:
            raise EmptySummaryResultError("summarizer returned an empty summary")

        article.summary = summary
        article.summarized_at = datetime.now(timezone.utc)
        session.add(article)
        session.commit()
        session.refresh(article)
    except Exception:
        session.rollback()
        raise

    return SummaryResult(summary=summary)

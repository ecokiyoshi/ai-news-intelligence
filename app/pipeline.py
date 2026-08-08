"""Single-run orchestration for the end-to-end news processing workflow."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import feedparser
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import NewsArticle
from app.ranking import (
    DEFAULT_IMPORTANCE_WEIGHT,
    DEFAULT_RELEVANCE_WEIGHT,
    RankingResult,
    get_rankable_articles,
    select_priority_articles,
)
from app.rss import collect_feeds
from app.scoring import Scorer, score_article
from app.summarization import Summarizer, summarize_article


@dataclass(frozen=True)
class PipelineResult:
    """Aggregate counts and selected articles produced by one pipeline run."""

    feeds_requested: int
    articles_fetched: int
    articles_stored: int
    articles_skipped: int
    articles_summarized: int
    articles_scored: int
    articles_failed: int
    priority_articles: list[RankingResult]


class ArticleTextProvider(Protocol):
    """Resolve explicit processing text for a stored article."""

    def get_text(self, article: NewsArticle) -> str:
        """Return text to use for summarization and scoring."""


class MetadataTextProvider:
    """Use stored metadata as input without fetching or scraping article bodies.

    This deterministic provider joins the title and an existing summary, when present.
    The result is metadata-derived processing input, not the full article body.
    """

    def get_text(self, article: NewsArticle) -> str:
        parts = [article.title.strip()]
        if article.summary and article.summary.strip():
            parts.append(article.summary.strip())
        return "\n\n".join(part for part in parts if part)


def _validate_feed_urls(feed_urls: Iterable[str]) -> list[str]:
    if isinstance(feed_urls, (str, bytes)):
        raise ValueError("feed_urls must be an iterable of URLs")
    try:
        urls = list(feed_urls)
    except TypeError as error:
        raise ValueError("feed_urls must be an iterable of URLs") from error
    if not urls:
        raise ValueError("at least one feed URL is required")
    if any(not isinstance(url, str) or not url.strip() for url in urls):
        raise ValueError("feed URLs must be non-empty strings")
    return [url.strip() for url in urls]


def _validate_configuration(
    relevance_target: str,
    *,
    limit: int,
    minimum_priority_score: float | None,
    max_per_source: int | None,
    importance_weight: float,
    relevance_weight: float,
    force_resummarize: bool,
    force_rescore: bool,
) -> str:
    if not isinstance(relevance_target, str) or not relevance_target.strip():
        raise ValueError("relevance_target must be a non-empty string")
    if not isinstance(force_resummarize, bool) or not isinstance(force_rescore, bool):
        raise ValueError("force flags must be booleans")

    # Reuse ranking's public validation before collection or provider work begins.
    select_priority_articles(
        [],
        limit=limit,
        minimum_priority_score=minimum_priority_score,
        max_per_source=max_per_source,
        importance_weight=importance_weight,
        relevance_weight=relevance_weight,
    )
    return relevance_target.strip()


def run_pipeline(
    feed_urls: Iterable[str],
    relevance_target: str,
    summarizer: Summarizer,
    scorer: Scorer,
    text_provider: ArticleTextProvider,
    session: Session,
    *,
    limit: int = 10,
    minimum_priority_score: float | None = None,
    max_per_source: int | None = None,
    importance_weight: float = DEFAULT_IMPORTANCE_WEIGHT,
    relevance_weight: float = DEFAULT_RELEVANCE_WEIGHT,
    force_resummarize: bool = False,
    force_rescore: bool = False,
    feed_parser: Callable[[str], Any] = feedparser.parse,
) -> PipelineResult:
    """Collect, process, rank, and select news articles in one reusable run."""

    urls = _validate_feed_urls(feed_urls)
    target = _validate_configuration(
        relevance_target,
        limit=limit,
        minimum_priority_score=minimum_priority_score,
        max_per_source=max_per_source,
        importance_weight=importance_weight,
        relevance_weight=relevance_weight,
        force_resummarize=force_resummarize,
        force_rescore=force_rescore,
    )

    collection_sessions = sessionmaker(
        bind=session.get_bind(), autoflush=False, expire_on_commit=False
    )
    collection = collect_feeds(
        urls,
        session_factory=collection_sessions,
        parser=feed_parser,
    )

    articles = list(session.scalars(select(NewsArticle).order_by(NewsArticle.id)))
    summarized = 0
    scored = 0
    failed = 0
    for article in articles:
        needs_summary = force_resummarize or not (article.summary and article.summary.strip())
        needs_score = force_rescore or (
            article.importance_score is None or article.relevance_score is None
        )
        if not needs_summary and not needs_score:
            continue

        try:
            text = text_provider.get_text(article)
        except Exception:
            session.rollback()
            failed += 1
            continue

        if needs_summary:
            try:
                summarize_article(article, text, summarizer, session)
            except Exception:
                failed += 1
                continue
            summarized += 1

        if needs_score:
            try:
                score_article(article, text, target, scorer, session)
            except Exception:
                failed += 1
                continue
            scored += 1

    priority_articles = select_priority_articles(
        get_rankable_articles(session),
        limit=limit,
        minimum_priority_score=minimum_priority_score,
        max_per_source=max_per_source,
        importance_weight=importance_weight,
        relevance_weight=relevance_weight,
    )
    return PipelineResult(
        feeds_requested=len(urls),
        articles_fetched=collection.fetched,
        articles_stored=collection.stored,
        articles_skipped=collection.skipped,
        articles_summarized=summarized,
        articles_scored=scored,
        articles_failed=failed,
        priority_articles=priority_articles,
    )

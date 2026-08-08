"""Deterministic news ranking and priority article selection."""

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NewsArticle

DEFAULT_IMPORTANCE_WEIGHT = 0.6
DEFAULT_RELEVANCE_WEIGHT = 0.4


@dataclass(frozen=True)
class RankingResult:
    """Minimal ranked representation of a scored news article."""

    article_id: int
    priority_score: float
    importance_score: int
    relevance_score: int


def _validate_score(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not 0 <= value <= 100:
        raise ValueError(f"{name} must be between 0 and 100")


def _validate_weights(importance_weight: float, relevance_weight: float) -> None:
    for name, value in (
        ("importance_weight", importance_weight),
        ("relevance_weight", relevance_weight),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number")
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be between 0 and 1")
    if not math.isclose(importance_weight + relevance_weight, 1.0):
        raise ValueError("importance_weight and relevance_weight must sum to 1.0")


def calculate_priority_score(
    importance_score: int,
    relevance_score: int,
    importance_weight: float = DEFAULT_IMPORTANCE_WEIGHT,
    relevance_weight: float = DEFAULT_RELEVANCE_WEIGHT,
) -> float:
    """Calculate a validated weighted priority score without side effects."""

    _validate_score("importance_score", importance_score)
    _validate_score("relevance_score", relevance_score)
    _validate_weights(importance_weight, relevance_weight)
    return (
        importance_score * importance_weight
        + relevance_score * relevance_weight
    )


def _timestamp(value: datetime | None) -> float:
    if value is None:
        return float("-inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).timestamp()


def rank_articles(
    articles: Iterable[NewsArticle],
    importance_weight: float = DEFAULT_IMPORTANCE_WEIGHT,
    relevance_weight: float = DEFAULT_RELEVANCE_WEIGHT,
) -> list[RankingResult]:
    """Rank fully scored articles with deterministic tie-breaking."""

    _validate_weights(importance_weight, relevance_weight)
    ranked: list[tuple[RankingResult, NewsArticle]] = []
    for article in articles:
        if article.importance_score is None or article.relevance_score is None:
            continue
        if article.id is None:
            raise ValueError("ranked articles must have an id")
        result = RankingResult(
            article_id=article.id,
            priority_score=calculate_priority_score(
                article.importance_score,
                article.relevance_score,
                importance_weight,
                relevance_weight,
            ),
            importance_score=article.importance_score,
            relevance_score=article.relevance_score,
        )
        ranked.append((result, article))

    ranked.sort(
        key=lambda item: (
            -item[0].priority_score,
            -item[0].importance_score,
            -item[0].relevance_score,
            -_timestamp(item[1].published_at),
            -_timestamp(item[1].created_at),
            item[0].article_id,
        )
    )
    return [result for result, _ in ranked]


def select_priority_articles(
    articles: Iterable[NewsArticle],
    limit: int = 10,
    minimum_priority_score: float | None = None,
    max_per_source: int | None = None,
    importance_weight: float = DEFAULT_IMPORTANCE_WEIGHT,
    relevance_weight: float = DEFAULT_RELEVANCE_WEIGHT,
) -> list[RankingResult]:
    """Select top ranked articles with optional threshold and source diversity."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    if max_per_source is not None and (
        isinstance(max_per_source, bool)
        or not isinstance(max_per_source, int)
        or max_per_source <= 0
    ):
        raise ValueError("max_per_source must be a positive integer or None")
    if minimum_priority_score is not None:
        if (
            isinstance(minimum_priority_score, bool)
            or not isinstance(minimum_priority_score, (int, float))
            or not math.isfinite(minimum_priority_score)
            or not 0 <= minimum_priority_score <= 100
        ):
            raise ValueError("minimum_priority_score must be between 0 and 100")

    article_list = list(articles)
    article_sources = {
        article.id: article.source for article in article_list if article.id is not None
    }
    ranked = rank_articles(article_list, importance_weight, relevance_weight)
    selected: list[RankingResult] = []
    source_counts: dict[str, int] = {}
    for result in ranked:
        if (
            minimum_priority_score is not None
            and result.priority_score < minimum_priority_score
        ):
            continue
        source = article_sources[result.article_id]
        if max_per_source is not None and source_counts.get(source, 0) >= max_per_source:
            continue
        selected.append(result)
        source_counts[source] = source_counts.get(source, 0) + 1
        if len(selected) == limit:
            break
    return selected


def get_rankable_articles(session: Session) -> list[NewsArticle]:
    """Load articles that have both scores; ranking remains Python-side."""

    statement = (
        select(NewsArticle)
        .where(
            NewsArticle.importance_score.is_not(None),
            NewsArticle.relevance_score.is_not(None),
        )
        .order_by(NewsArticle.id)
    )
    return list(session.scalars(statement))

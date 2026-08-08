"""Provider-independent clustering of priority articles by underlying news event."""

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.models import NewsArticle
from app.ranking import RankingResult

MAX_CLUSTER_ARTICLES = 50


def _positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _cluster_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("cluster_id must be a non-negative integer")
    return value


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _score(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not 0 <= value <= 100:
        raise ValueError(f"{name} must be between 0 and 100")
    return value


def _article_ids(values: list[int]) -> list[int]:
    if not isinstance(values, list) or not values:
        raise ValueError("article_ids must be a non-empty list")
    normalized = [_positive_int("article ID", value) for value in values]
    if len(set(normalized)) != len(normalized):
        raise ValueError("article_ids must not contain duplicates")
    return normalized


@dataclass(frozen=True)
class NewsClusterSource:
    """Compact ranked-article context supplied to a clusterer."""

    article_id: int
    title: str
    summary: str | None
    source: str
    published_at: datetime | None
    importance_score: int
    relevance_score: int
    priority_score: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "article_id", _positive_int("article_id", self.article_id))
        object.__setattr__(self, "title", _required_text("title", self.title))
        if self.summary is not None:
            if not isinstance(self.summary, str):
                raise ValueError("summary must be a string or None")
            object.__setattr__(self, "summary", self.summary.strip() or None)
        object.__setattr__(self, "source", _required_text("source", self.source))
        if self.published_at is not None and not isinstance(self.published_at, datetime):
            raise ValueError("published_at must be a datetime or None")
        object.__setattr__(
            self, "importance_score", _score("importance_score", self.importance_score)
        )
        object.__setattr__(
            self, "relevance_score", _score("relevance_score", self.relevance_score)
        )
        if (
            isinstance(self.priority_score, bool)
            or not isinstance(self.priority_score, (int, float))
            or not math.isfinite(self.priority_score)
            or not 0 <= self.priority_score <= 100
        ):
            raise ValueError("priority_score must be a finite number between 0 and 100")
        object.__setattr__(self, "priority_score", float(self.priority_score))


@dataclass(frozen=True)
class NewsClusterGrouping:
    """Provider-returned grouping before representative selection."""

    cluster_id: int
    article_ids: list[int]
    topic_title: str
    topic_summary: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "cluster_id", _cluster_id(self.cluster_id))
        object.__setattr__(self, "article_ids", _article_ids(self.article_ids))
        for name in ("topic_title", "topic_summary", "reason"):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))


@dataclass(frozen=True)
class NewsCluster:
    """Validated cluster with a core-selected representative article."""

    cluster_id: int
    article_ids: list[int]
    representative_article_id: int
    topic_title: str
    topic_summary: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "cluster_id", _cluster_id(self.cluster_id))
        article_ids = _article_ids(self.article_ids)
        object.__setattr__(self, "article_ids", article_ids)
        representative = _positive_int(
            "representative_article_id", self.representative_article_id
        )
        if representative not in article_ids:
            raise ValueError("representative_article_id must belong to article_ids")
        object.__setattr__(self, "representative_article_id", representative)
        for name in ("topic_title", "topic_summary", "reason"):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))


class NewsClusterer(Protocol):
    """Interface implemented by similar-news grouping providers."""

    def cluster(
        self, sources: list[NewsClusterSource], *, topic_focus: str
    ) -> list[NewsClusterGrouping]:
        """Group sources that describe substantially the same underlying event."""


def validate_cluster_source(source: NewsClusterSource) -> NewsClusterSource:
    if not isinstance(source, NewsClusterSource):
        raise ValueError("sources must contain NewsClusterSource values")
    return NewsClusterSource(**source.__dict__)


def validate_clustering_request(
    sources: list[NewsClusterSource], topic_focus: str, max_articles: int
) -> tuple[list[NewsClusterSource], str]:
    """Validate all configuration before provider work begins."""

    if not isinstance(sources, list) or not sources:
        raise ValueError("sources must be a non-empty list")
    if isinstance(max_articles, bool) or not isinstance(max_articles, int) or max_articles <= 0:
        raise ValueError("max_articles must be a positive integer")
    if len(sources) > max_articles:
        raise ValueError(f"source count exceeds max_articles={max_articles}")
    normalized = [validate_cluster_source(source) for source in sources]
    source_ids = [source.article_id for source in normalized]
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("source article IDs must be unique")
    focus = _required_text("topic_focus", topic_focus)
    return normalized, focus


def validate_groupings(
    groupings: list[NewsClusterGrouping], sources: list[NewsClusterSource]
) -> list[NewsClusterGrouping]:
    """Validate grouping shape and exact one-to-one source coverage."""

    if not isinstance(groupings, list) or not groupings:
        raise ValueError("clusterer must return a non-empty grouping list")
    validated: list[NewsClusterGrouping] = []
    cluster_ids: set[int] = set()
    memberships: set[int] = set()
    known_ids = {source.article_id for source in sources}
    for grouping in groupings:
        if not isinstance(grouping, NewsClusterGrouping):
            raise ValueError("clusterer must return NewsClusterGrouping values")
        grouping = NewsClusterGrouping(**grouping.__dict__)
        if grouping.cluster_id in cluster_ids:
            raise ValueError("cluster IDs must be unique")
        cluster_ids.add(grouping.cluster_id)
        article_ids = set(grouping.article_ids)
        unknown = article_ids - known_ids
        if unknown:
            raise ValueError("cluster contains an unknown article ID")
        if memberships & article_ids:
            raise ValueError("an article belongs to more than one cluster")
        memberships.update(article_ids)
        validated.append(grouping)
    if memberships != known_ids:
        raise ValueError("every source article must belong to exactly one cluster")
    return validated


def build_news_cluster_sources(
    ranking_results: list[RankingResult], articles: list[NewsArticle]
) -> list[NewsClusterSource]:
    """Match rankings to articles while copying, never recalculating, priority scores."""

    article_ids = [article.id for article in articles if article.id is not None]
    if len(set(article_ids)) != len(article_ids):
        raise ValueError("articles contain duplicate article IDs")
    articles_by_id = {
        article.id: article for article in articles if article.id is not None
    }
    seen_rankings: set[int] = set()
    sources: list[NewsClusterSource] = []
    for result in ranking_results:
        if result.article_id in seen_rankings:
            raise ValueError(f"duplicate ranking result for article {result.article_id}")
        seen_rankings.add(result.article_id)
        article = articles_by_id.get(result.article_id)
        if article is None:
            raise ValueError(f"article {result.article_id} was not found")
        sources.append(
            NewsClusterSource(
                article_id=result.article_id,
                title=article.title,
                summary=article.summary,
                source=article.source,
                published_at=article.published_at,
                importance_score=result.importance_score,
                relevance_score=result.relevance_score,
                priority_score=result.priority_score,
            )
        )
    return sources


def _normalized_title(title: str) -> str:
    without_punctuation = re.sub(r"[^\w\s]", " ", title.casefold())
    return " ".join(without_punctuation.split())


class LocalNewsClusterer:
    """Deterministic normalized-title heuristic, not semantic equivalence detection."""

    def cluster(
        self, sources: list[NewsClusterSource], *, topic_focus: str
    ) -> list[NewsClusterGrouping]:
        sources, _ = validate_clustering_request(
            sources, topic_focus, MAX_CLUSTER_ARTICLES
        )
        grouped: dict[str, list[NewsClusterSource]] = {}
        for source in sources:
            grouped.setdefault(_normalized_title(source.title), []).append(source)
        return [
            NewsClusterGrouping(
                cluster_id=index,
                article_ids=[source.article_id for source in matching_sources],
                topic_title=matching_sources[0].title,
                topic_summary=matching_sources[0].summary or matching_sources[0].title,
                reason="Titles match after deterministic local normalization.",
            )
            for index, matching_sources in enumerate(grouped.values())
        ]


def _timestamp(value: datetime | None) -> float:
    if value is None:
        return float("-inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).timestamp()


def _representative(
    article_ids: list[int], sources_by_id: dict[int, NewsClusterSource]
) -> NewsClusterSource:
    candidates = [sources_by_id[article_id] for article_id in article_ids]
    return min(
        candidates,
        key=lambda source: (
            -source.priority_score,
            -source.importance_score,
            -source.relevance_score,
            -_timestamp(source.published_at),
            source.article_id,
        ),
    )


def cluster_priority_news(
    sources: list[NewsClusterSource],
    clusterer: NewsClusterer,
    *,
    topic_focus: str,
    max_articles: int = MAX_CLUSTER_ARTICLES,
) -> list[NewsCluster]:
    """Validate groupings, select representatives, and order clusters deterministically."""

    sources, focus = validate_clustering_request(sources, topic_focus, max_articles)
    groupings = validate_groupings(
        clusterer.cluster(sources, topic_focus=focus), sources
    )
    sources_by_id = {source.article_id: source for source in sources}
    clusters_with_representatives: list[tuple[NewsCluster, NewsClusterSource]] = []
    for grouping in groupings:
        representative = _representative(grouping.article_ids, sources_by_id)
        cluster = NewsCluster(
            cluster_id=grouping.cluster_id,
            article_ids=grouping.article_ids,
            representative_article_id=representative.article_id,
            topic_title=grouping.topic_title,
            topic_summary=grouping.topic_summary,
            reason=grouping.reason,
        )
        clusters_with_representatives.append((cluster, representative))
    clusters_with_representatives.sort(
        key=lambda item: (
            -item[1].priority_score,
            -item[1].importance_score,
            -item[1].relevance_score,
            -_timestamp(item[1].published_at),
            min(item[0].article_ids),
            item[0].cluster_id,
        )
    )
    return [cluster for cluster, _ in clusters_with_representatives]

from datetime import datetime, timedelta, timezone

import pytest

from app.models import NewsArticle
from app.news_clustering import (
    LocalNewsClusterer,
    NewsCluster,
    NewsClusterGrouping,
    NewsClusterSource,
    build_news_cluster_sources,
    cluster_priority_news,
)
from app.ranking import RankingResult


def source(article_id: int = 1, **overrides) -> NewsClusterSource:
    values = {
        "article_id": article_id,
        "title": f"DJI launches Drone {article_id}",
        "summary": "Product announcement summary.",
        "source": "Example News",
        "published_at": datetime(2026, 8, 8, tzinfo=timezone.utc),
        "importance_score": 80,
        "relevance_score": 90,
        "priority_score": 84.0,
    }
    values.update(overrides)
    return NewsClusterSource(**values)


def grouping(cluster_id: int, article_ids: list[int], **overrides) -> NewsClusterGrouping:
    values = {
        "cluster_id": cluster_id,
        "article_ids": article_ids,
        "topic_title": "DJI product launch",
        "topic_summary": "DJI announced a product.",
        "reason": "The articles cover the same announcement.",
    }
    values.update(overrides)
    return NewsClusterGrouping(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"article_id": 0},
        {"article_id": True},
        {"title": "   "},
        {"source": ""},
        {"published_at": "today"},
        {"importance_score": -1},
        {"importance_score": 101},
        {"relevance_score": -1},
        {"relevance_score": 101},
        {"priority_score": -1},
        {"priority_score": 101},
        {"priority_score": True},
        {"importance_score": False},
    ],
)
def test_invalid_source_is_rejected(overrides) -> None:
    with pytest.raises(ValueError):
        source(**overrides)


def test_build_sources_copies_existing_priority_without_recalculation() -> None:
    article = NewsArticle(
        id=7,
        title="Priority story",
        url="https://example.com/story",
        source="Example News",
        summary="Stored summary",
    )
    ranking = RankingResult(7, 12.345, 90, 80)
    result = build_news_cluster_sources([ranking], [article])
    assert result[0].priority_score == 12.345
    assert result[0].article_id == 7


def test_build_sources_rejects_missing_and_duplicate_matches() -> None:
    ranking = RankingResult(1, 80.0, 80, 80)
    with pytest.raises(ValueError, match="not found"):
        build_news_cluster_sources([ranking], [])

    duplicate_articles = [
        NewsArticle(id=1, title="One", url="one", source="News"),
        NewsArticle(id=1, title="Copy", url="copy", source="News"),
    ]
    with pytest.raises(ValueError, match="duplicate"):
        build_news_cluster_sources([ranking], duplicate_articles)


def test_local_clusterer_is_deterministic_and_normalizes_titles() -> None:
    sources = [
        source(1, title="DJI Launches: Drone X!"),
        source(2, title="  dji launches drone x  "),
        source(3, title="New drone regulation announced"),
    ]
    clusterer = LocalNewsClusterer()
    first = cluster_priority_news(sources, clusterer, topic_focus="drone news")
    second = cluster_priority_news(sources, clusterer, topic_focus="drone news")
    assert first == second
    memberships = [set(cluster.article_ids) for cluster in first]
    assert {1, 2} in memberships
    assert {3} in memberships


@pytest.mark.parametrize(
    ("sources", "focus", "max_articles"),
    [
        ([], "drone news", 50),
        ([source()], "", 50),
        ([source()], "   ", 50),
        ([source()], "drone news", 0),
        ([source()], "drone news", -1),
        ([source()], "drone news", True),
        ([source(1), source(1)], "drone news", 50),
        ([source(1), source(2)], "drone news", 1),
    ],
)
def test_invalid_request_fails_before_provider(sources, focus, max_articles) -> None:
    class UnexpectedClusterer:
        def cluster(self, sources, *, topic_focus):
            raise AssertionError("clusterer must not be called")

    with pytest.raises(ValueError):
        cluster_priority_news(
            sources,
            UnexpectedClusterer(),
            topic_focus=focus,
            max_articles=max_articles,
        )


@pytest.mark.parametrize(
    "groupings",
    [
        [grouping(0, [1, 2])],
        [grouping(0, [1, 2]), grouping(1, [2, 3])],
        [grouping(0, [1, 2, 99]), grouping(1, [3])],
        [grouping(0, [1]), grouping(0, [2, 3])],
        [],
    ],
)
def test_service_rejects_invalid_coverage(groupings) -> None:
    class FixedClusterer:
        def cluster(self, sources, *, topic_focus):
            return groupings

    with pytest.raises(ValueError):
        cluster_priority_news(
            [source(1), source(2), source(3)],
            FixedClusterer(),
            topic_focus="drone news",
        )


def test_news_cluster_rejects_invalid_representative_reference() -> None:
    with pytest.raises(ValueError, match="must belong"):
        NewsCluster(0, [1, 2], 3, "Title", "Summary", "Reason")


def test_core_selects_representative_by_all_tie_breakers() -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sources = [
        source(1, priority_score=80, importance_score=100, relevance_score=100),
        source(2, priority_score=90, importance_score=80, relevance_score=100),
        source(3, priority_score=90, importance_score=90, relevance_score=80),
        source(4, priority_score=90, importance_score=90, relevance_score=90,
               published_at=base + timedelta(days=1)),
        source(5, priority_score=90, importance_score=90, relevance_score=90,
               published_at=base + timedelta(days=1)),
        source(6, priority_score=90, importance_score=90, relevance_score=90,
               published_at=None),
    ]

    class OneCluster:
        def cluster(self, sources, *, topic_focus):
            return [grouping(9, [item.article_id for item in sources])]

    clusters = cluster_priority_news(sources, OneCluster(), topic_focus="drone news")
    assert clusters[0].representative_article_id == 4


def test_cluster_order_is_deterministic_not_provider_order() -> None:
    sources = [
        source(1, priority_score=70),
        source(2, priority_score=95),
        source(3, priority_score=80),
    ]

    class ReversedClusterer:
        def cluster(self, sources, *, topic_focus):
            return [grouping(7, [1]), grouping(8, [3]), grouping(9, [2])]

    clusters = cluster_priority_news(
        sources, ReversedClusterer(), topic_focus="drone news"
    )
    assert [cluster.representative_article_id for cluster in clusters] == [2, 3, 1]


def test_every_input_article_appears_exactly_once() -> None:
    sources = [source(1, title="Same"), source(2, title="same"), source(3, title="Other")]
    clusters = cluster_priority_news(
        sources, LocalNewsClusterer(), topic_focus="drone news"
    )
    memberships = [article_id for cluster in clusters for article_id in cluster.article_ids]
    assert sorted(memberships) == [1, 2, 3]

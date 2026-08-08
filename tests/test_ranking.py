from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.database import create_db_engine, init_db
from app.models import NewsArticle
from app.ranking import (
    calculate_priority_score,
    get_rankable_articles,
    rank_articles,
    select_priority_articles,
)


def article(
    article_id: int,
    importance: int | None,
    relevance: int | None,
    *,
    source: str = "Source A",
    published_at: datetime | None = None,
    created_at: datetime | None = None,
) -> NewsArticle:
    return NewsArticle(
        id=article_id,
        title=f"Article {article_id}",
        url=f"https://example.com/{article_id}",
        source=source,
        importance_score=importance,
        relevance_score=relevance,
        published_at=published_at,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    ("importance", "relevance", "expected"),
    [(90, 80, 86.0), (100, 100, 100.0), (0, 0, 0.0)],
)
def test_calculate_priority_score(importance, relevance, expected) -> None:
    assert calculate_priority_score(importance, relevance) == expected


def test_calculate_priority_score_with_custom_weights() -> None:
    assert calculate_priority_score(100, 0, 0.8, 0.2) == 80.0


@pytest.mark.parametrize(
    ("importance_weight", "relevance_weight"),
    [(-0.1, 1.1), (1.1, -0.1), (0.5, -0.1), (-0.1, 0.5), (0.4, 0.4)],
)
def test_invalid_weights_are_rejected(importance_weight, relevance_weight) -> None:
    with pytest.raises(ValueError):
        calculate_priority_score(50, 50, importance_weight, relevance_weight)


@pytest.mark.parametrize("scores", [(-1, 50), (101, 50), (50, -1), (50, 101), (True, 50)])
def test_invalid_scores_are_rejected(scores) -> None:
    with pytest.raises(ValueError):
        calculate_priority_score(*scores)


def test_rank_articles_sorts_by_priority_and_excludes_unscored() -> None:
    results = rank_articles(
        [
            article(1, 50, 50),
            article(2, 90, 80),
            article(3, None, 100),
            article(4, 100, None),
            article(5, 70, 70),
        ]
    )
    assert [result.article_id for result in results] == [2, 5, 1]


def test_rank_articles_uses_all_deterministic_tie_breakers() -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    articles = [
        article(7, 70, 80, published_at=base, created_at=base),
        article(6, 70, 80, published_at=base, created_at=base),
        article(5, 70, 80, published_at=base, created_at=base + timedelta(days=1)),
        article(4, 70, 80, published_at=base + timedelta(days=1)),
        article(3, 80, 65),
        article(2, 90, 50),
        article(1, 50, 100),
    ]
    assert [item.article_id for item in rank_articles(articles)] == [2, 3, 4, 5, 6, 7, 1]


def test_select_priority_articles_applies_limit_and_minimum() -> None:
    articles = [article(index, index * 10, index * 10) for index in range(1, 11)]
    results = select_priority_articles(articles, limit=3, minimum_priority_score=80)
    assert [item.priority_score for item in results] == [100.0, 90.0, 80.0]


def test_select_priority_articles_enforces_source_diversity() -> None:
    articles = [
        article(1, 95, 95, source="Source A"),
        article(2, 94, 94, source="Source A"),
        article(3, 93, 93, source="Source A"),
        article(4, 90, 90, source="Source B"),
        article(5, 85, 85, source="Source C"),
    ]
    results = select_priority_articles(articles, limit=5, max_per_source=2)
    assert [item.article_id for item in results] == [1, 2, 4, 5]


@pytest.mark.parametrize("limit", [0, -1, True])
def test_invalid_limit_is_rejected(limit) -> None:
    with pytest.raises(ValueError):
        select_priority_articles([], limit=limit)


@pytest.mark.parametrize("max_per_source", [0, -1, True])
def test_invalid_max_per_source_is_rejected(max_per_source) -> None:
    with pytest.raises(ValueError):
        select_priority_articles([], max_per_source=max_per_source)


@pytest.mark.parametrize("minimum", [-1, 101, True])
def test_invalid_minimum_priority_is_rejected(minimum) -> None:
    with pytest.raises(ValueError):
        select_priority_articles([], minimum_priority_score=minimum)


def test_get_rankable_articles_filters_in_database(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'ranking.db'}")
    init_db(engine)
    with Session(engine) as session:
        session.add_all(
            [
                article(1, 80, 90),
                article(2, None, 90),
                article(3, 80, None),
            ]
        )
        session.commit()
        results = get_rankable_articles(session)
        assert [item.id for item in results] == [1]
    engine.dispose()

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import create_db_engine, init_db
from app.models import NewsArticle
from app.scoring import (
    EmptyRelevanceTargetError,
    EmptyScoreInputError,
    InvalidScoreResultError,
    LocalScorer,
    ScoreResult,
    score_article,
)


def create_article(session: Session) -> NewsArticle:
    article = NewsArticle(
        title="Scoring news",
        url="https://example.com/scoring-news",
        source="Example News",
    )
    session.add(article)
    session.commit()
    return article


class FixedScorer:
    def __init__(self, result: ScoreResult) -> None:
        self.result = result

    def score(self, text: str, relevance_target: str) -> ScoreResult:
        return self.result


def test_local_scorer_is_deterministic() -> None:
    scorer = LocalScorer()

    first = scorer.score("AI model release", "AI industry")
    second = scorer.score("AI model release", "AI industry")

    assert first == second
    assert 0 <= first.importance_score <= 100
    assert 0 <= first.relevance_score <= 100
    assert first.reason


def test_score_article_persists_result_and_utc_timestamp(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'scores.db'}")
    init_db(engine)
    with Session(engine) as session:
        article = create_article(session)
        result = score_article(
            article,
            "A major AI model was released.",
            "AI industry and model releases",
            FixedScorer(ScoreResult(80, 95, "  Major relevant release.  ")),
            session,
        )
        article_id = article.id

    with Session(engine) as session:
        saved = session.get(NewsArticle, article_id)
        assert saved is not None
        assert result == ScoreResult(80, 95, "Major relevant release.")
        assert saved.importance_score == 80
        assert saved.relevance_score == 95
        assert saved.score_reason == "Major relevant release."
        assert saved.scored_at is not None
        assert saved.scored_at.tzinfo is not None
        assert saved.scored_at.utcoffset().total_seconds() == 0
    engine.dispose()


@pytest.mark.parametrize(
    ("importance", "relevance"),
    [(0, 0), (0, 100), (100, 0), (100, 100)],
)
def test_score_boundaries_are_accepted(tmp_path, importance: int, relevance: int) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / f'{importance}-{relevance}.db'}")
    init_db(engine)
    with Session(engine) as session:
        article = create_article(session)
        score_article(
            article,
            "Article text",
            "Target",
            FixedScorer(ScoreResult(importance, relevance, "Valid boundary.")),
            session,
        )
        assert article.importance_score == importance
        assert article.relevance_score == relevance
    engine.dispose()


@pytest.mark.parametrize(
    "result",
    [
        ScoreResult(-1, 50, "Reason"),
        ScoreResult(101, 50, "Reason"),
        ScoreResult(50, -1, "Reason"),
        ScoreResult(50, 101, "Reason"),
        ScoreResult(50, 50, "   "),
    ],
)
def test_invalid_provider_result_does_not_change_database(tmp_path, result) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'invalid.db'}")
    init_db(engine)
    with Session(engine) as session:
        article = create_article(session)
        with pytest.raises(InvalidScoreResultError):
            score_article(article, "Article text", "Target", FixedScorer(result), session)
        saved = session.get(NewsArticle, article.id)
        assert saved is not None
        assert saved.importance_score is None
        assert saved.relevance_score is None
        assert saved.score_reason is None
        assert saved.scored_at is None
    engine.dispose()


@pytest.mark.parametrize("text", ["", " \n\t "])
def test_empty_article_text_is_rejected_without_changes(tmp_path, text: str) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'empty-text.db'}")
    init_db(engine)
    with Session(engine) as session:
        article = create_article(session)
        with pytest.raises(EmptyScoreInputError):
            score_article(article, text, "Target", LocalScorer(), session)
        assert article.scored_at is None
    engine.dispose()


@pytest.mark.parametrize("target", ["", " \n\t "])
def test_empty_relevance_target_is_rejected_without_changes(tmp_path, target: str) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'empty-target.db'}")
    init_db(engine)
    with Session(engine) as session:
        article = create_article(session)
        with pytest.raises(EmptyRelevanceTargetError):
            score_article(article, "Article text", target, LocalScorer(), session)
        assert article.scored_at is None
    engine.dispose()


def test_provider_failure_preserves_existing_score_and_session(tmp_path) -> None:
    class FailingScorer:
        def score(self, text: str, relevance_target: str) -> ScoreResult:
            raise RuntimeError("provider failed")

    engine = create_db_engine(f"sqlite:///{tmp_path / 'failure.db'}")
    init_db(engine)
    previous_timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with Session(engine) as session:
        article = create_article(session)
        article.importance_score = 60
        article.relevance_score = 70
        article.score_reason = "Existing reason"
        article.scored_at = previous_timestamp
        session.commit()

        with pytest.raises(RuntimeError, match="provider failed"):
            score_article(article, "New text", "Target", FailingScorer(), session)

        saved = session.get(NewsArticle, article.id)
        assert saved is not None
        assert saved.importance_score == 60
        assert saved.relevance_score == 70
        assert saved.score_reason == "Existing reason"
        assert saved.scored_at == previous_timestamp
        assert session.scalar(select(NewsArticle.id).where(NewsArticle.id == article.id)) == article.id
    engine.dispose()

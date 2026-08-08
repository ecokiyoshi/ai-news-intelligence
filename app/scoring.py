"""Provider-independent article scoring service."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.orm import Session

from app.models import NewsArticle


@dataclass(frozen=True)
class ScoreResult:
    """Structured importance and relevance scores returned by a scorer."""

    importance_score: int
    relevance_score: int
    reason: str


class Scorer(Protocol):
    """Interface implemented by article scoring providers."""

    def score(self, text: str, relevance_target: str) -> ScoreResult:
        """Score supplied article text for importance and target relevance."""


class EmptyScoreInputError(ValueError):
    """Raised when scoring is requested without usable article text."""


class EmptyRelevanceTargetError(ValueError):
    """Raised when scoring is requested without a relevance target."""


class InvalidScoreResultError(ValueError):
    """Raised when a scorer returns scores or a reason outside the contract."""


def validate_score_result(result: ScoreResult) -> ScoreResult:
    """Validate and normalize a provider-independent score result."""

    for name, value in (
        ("importance_score", result.importance_score),
        ("relevance_score", result.relevance_score),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidScoreResultError(f"{name} must be an integer")
        if not 0 <= value <= 100:
            raise InvalidScoreResultError(f"{name} must be between 0 and 100")

    reason = result.reason.strip()
    if not reason:
        raise InvalidScoreResultError("score reason must not be empty")

    return ScoreResult(
        importance_score=result.importance_score,
        relevance_score=result.relevance_score,
        reason=reason,
    )


class LocalScorer:
    """Deterministic scorer for local development and automated tests."""

    def score(self, text: str, relevance_target: str) -> ScoreResult:
        normalized_text = " ".join(text.split())
        normalized_target = " ".join(relevance_target.split())
        importance = sum(map(ord, normalized_text)) % 101
        relevance = sum(map(ord, f"{normalized_text}\0{normalized_target}")) % 101
        return ScoreResult(
            importance_score=importance,
            relevance_score=relevance,
            reason="Deterministic local score for development and testing.",
        )


def score_article(
    article: NewsArticle,
    text: str,
    relevance_target: str,
    scorer: Scorer,
    session: Session,
) -> ScoreResult:
    """Score explicit article text and persist the validated result."""

    normalized_text = text.strip()
    if not normalized_text:
        raise EmptyScoreInputError("article text must not be empty")

    normalized_target = relevance_target.strip()
    if not normalized_target:
        raise EmptyRelevanceTargetError("relevance target must not be empty")

    try:
        result = validate_score_result(
            scorer.score(normalized_text, normalized_target)
        )
        article.importance_score = result.importance_score
        article.relevance_score = result.relevance_score
        article.score_reason = result.reason
        article.scored_at = datetime.now(timezone.utc)
        session.add(article)
        session.commit()
        session.refresh(article)
    except Exception:
        session.rollback()
        raise

    return result

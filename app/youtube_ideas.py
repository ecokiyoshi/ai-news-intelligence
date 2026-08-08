"""Provider-independent YouTube idea generation from priority news."""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.models import NewsArticle
from app.ranking import RankingResult

MAX_IDEA_COUNT = 10


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _positive_integer(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _score(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not 0 <= value <= 100:
        raise ValueError(f"{name} must be between 0 and 100")
    return value


@dataclass(frozen=True)
class YouTubeIdeaSource:
    """Compact priority-news context supplied to an idea generator."""

    article_id: int
    title: str
    summary: str | None
    source: str
    published_at: datetime | None
    importance_score: int
    relevance_score: int
    priority_score: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "article_id", _positive_integer("article_id", self.article_id))
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


def _text_list(name: str, values: list[str]) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{name} must be a non-empty list")
    normalized = [_required_text(f"{name} item", value) for value in values]
    return normalized


@dataclass(frozen=True)
class YouTubeIdea:
    """Structured YouTube video concept generated from priority news."""

    source_article_ids: list[int]
    title: str
    hook: str
    angle: str
    target_audience: str
    estimated_length_minutes: int
    thumbnail_text: str
    chapters: list[str]
    seo_keywords: list[str]

    def __post_init__(self) -> None:
        if not isinstance(self.source_article_ids, list) or not self.source_article_ids:
            raise ValueError("source_article_ids must be a non-empty list")
        source_ids = [
            _positive_integer("source article ID", article_id)
            for article_id in self.source_article_ids
        ]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source_article_ids must not contain duplicates")
        object.__setattr__(self, "source_article_ids", source_ids)
        for field_name in (
            "title",
            "hook",
            "angle",
            "target_audience",
            "thumbnail_text",
        ):
            object.__setattr__(
                self, field_name, _required_text(field_name, getattr(self, field_name))
            )
        object.__setattr__(
            self,
            "estimated_length_minutes",
            _positive_integer(
                "estimated_length_minutes", self.estimated_length_minutes
            ),
        )
        object.__setattr__(self, "chapters", _text_list("chapters", self.chapters))
        object.__setattr__(
            self, "seo_keywords", _text_list("seo_keywords", self.seo_keywords)
        )


class YouTubeIdeaGenerator(Protocol):
    """Interface implemented by YouTube idea providers."""

    def generate(
        self,
        sources: list[YouTubeIdeaSource],
        *,
        channel_focus: str,
        idea_count: int,
    ) -> list[YouTubeIdea]:
        """Generate structured video ideas from priority news sources."""


def validate_youtube_idea_source(source: YouTubeIdeaSource) -> YouTubeIdeaSource:
    """Defensively validate and normalize a source, including tampered instances."""

    if not isinstance(source, YouTubeIdeaSource):
        raise ValueError("sources must contain YouTubeIdeaSource values")
    return YouTubeIdeaSource(**source.__dict__)


def validate_youtube_idea(idea: YouTubeIdea) -> YouTubeIdea:
    """Defensively validate and normalize a returned idea."""

    if not isinstance(idea, YouTubeIdea):
        raise ValueError("generator must return YouTubeIdea values")
    return YouTubeIdea(**idea.__dict__)


def validate_generation_request(
    sources: list[YouTubeIdeaSource], channel_focus: str, idea_count: int
) -> tuple[list[YouTubeIdeaSource], str, int]:
    """Validate generator inputs before provider work starts."""

    if not isinstance(sources, list) or not sources:
        raise ValueError("sources must be a non-empty list")
    normalized_sources = [validate_youtube_idea_source(source) for source in sources]
    focus = _required_text("channel_focus", channel_focus)
    count = _positive_integer("idea_count", idea_count)
    if count > MAX_IDEA_COUNT:
        raise ValueError(f"idea_count must not exceed {MAX_IDEA_COUNT}")
    return normalized_sources, focus, count


def validate_generated_ideas(
    ideas: list[YouTubeIdea],
    sources: list[YouTubeIdeaSource],
    idea_count: int,
) -> list[YouTubeIdea]:
    """Validate provider output count, fields, and source references."""

    if not isinstance(ideas, list) or len(ideas) != idea_count:
        raise ValueError("generator must return exactly the requested number of ideas")
    valid_source_ids = {source.article_id for source in sources}
    validated = [validate_youtube_idea(idea) for idea in ideas]
    for idea in validated:
        if not set(idea.source_article_ids) <= valid_source_ids:
            raise ValueError("idea references an article outside the supplied sources")
    return validated


def build_youtube_idea_sources(
    ranking_results: list[RankingResult], articles: list[NewsArticle]
) -> list[YouTubeIdeaSource]:
    """Match ranked results to articles without recalculating priority scores."""

    articles_by_id = {
        article.id: article for article in articles if article.id is not None
    }
    sources: list[YouTubeIdeaSource] = []
    seen_ids: set[int] = set()
    for result in ranking_results:
        if result.article_id in seen_ids:
            raise ValueError(f"duplicate ranking result for article {result.article_id}")
        seen_ids.add(result.article_id)
        article = articles_by_id.get(result.article_id)
        if article is None:
            raise ValueError(f"article {result.article_id} was not found")
        sources.append(
            YouTubeIdeaSource(
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


class LocalYouTubeIdeaGenerator:
    """Deterministic local generator for development and automated tests."""

    def generate(
        self,
        sources: list[YouTubeIdeaSource],
        *,
        channel_focus: str,
        idea_count: int,
    ) -> list[YouTubeIdea]:
        sources, focus, count = validate_generation_request(
            sources, channel_focus, idea_count
        )
        ideas = []
        for index in range(count):
            source = sources[index % len(sources)]
            number = index + 1
            ideas.append(
                YouTubeIdea(
                    source_article_ids=[source.article_id],
                    title=f"{source.title}: {focus} idea {number}",
                    hook=f"Why does {source.title} matter now?",
                    angle=f"Explain the news through the lens of {focus} (angle {number}).",
                    target_audience=f"Viewers interested in {focus}",
                    estimated_length_minutes=8,
                    thumbnail_text=f"WHY IT MATTERS {number}",
                    chapters=["What happened", "Why it matters", "What to watch next"],
                    seo_keywords=[focus, source.title, source.source],
                )
            )
        return ideas


def generate_youtube_ideas(
    sources: list[YouTubeIdeaSource],
    generator: YouTubeIdeaGenerator,
    *,
    channel_focus: str,
    idea_count: int = 3,
) -> list[YouTubeIdea]:
    """Validate inputs and provider output for structured idea generation."""

    normalized_sources, focus, count = validate_generation_request(
        sources, channel_focus, idea_count
    )
    ideas = generator.generate(
        normalized_sources, channel_focus=focus, idea_count=count
    )
    return validate_generated_ideas(ideas, normalized_sources, count)

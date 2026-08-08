from datetime import datetime, timezone

import pytest

from app.models import NewsArticle
from app.ranking import RankingResult
from app.youtube_ideas import (
    LocalYouTubeIdeaGenerator,
    YouTubeIdea,
    YouTubeIdeaSource,
    build_youtube_idea_sources,
    generate_youtube_ideas,
)


def source(**overrides) -> YouTubeIdeaSource:
    values = {
        "article_id": 1,
        "title": " AI model released ",
        "summary": " A concise summary. ",
        "source": " Example News ",
        "published_at": datetime(2026, 8, 8, tzinfo=timezone.utc),
        "importance_score": 90,
        "relevance_score": 80,
        "priority_score": 86.0,
    }
    values.update(overrides)
    return YouTubeIdeaSource(**values)


def idea(**overrides) -> YouTubeIdea:
    values = {
        "source_article_ids": [1],
        "title": " AI release explained ",
        "hook": " Why this release matters now. ",
        "angle": " Technical and industry impact. ",
        "target_audience": " AI industry viewers ",
        "estimated_length_minutes": 8,
        "thumbnail_text": " BIG AI UPDATE ",
        "chapters": [" What happened ", " Why it matters "],
        "seo_keywords": [" AI news ", " model release "],
    }
    values.update(overrides)
    return YouTubeIdea(**values)


def test_valid_source_is_normalized() -> None:
    value = source()
    assert value.title == "AI model released"
    assert value.summary == "A concise summary."
    assert value.source == "Example News"
    assert value.priority_score == 86.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"article_id": 0},
        {"article_id": True},
        {"title": "   "},
        {"source": ""},
        {"importance_score": -1},
        {"importance_score": 101},
        {"relevance_score": -1},
        {"relevance_score": 101},
        {"priority_score": -0.1},
        {"priority_score": 100.1},
        {"priority_score": True},
        {"importance_score": False},
    ],
)
def test_invalid_source_is_rejected(overrides) -> None:
    with pytest.raises(ValueError):
        source(**overrides)


def test_build_sources_matches_articles_without_recalculating_priority() -> None:
    article = NewsArticle(
        id=7,
        title="Priority news",
        url="https://example.com/priority",
        source="Example News",
        summary="Stored summary",
        importance_score=90,
        relevance_score=80,
    )
    ranking = RankingResult(
        article_id=7,
        priority_score=12.345,
        importance_score=90,
        relevance_score=80,
    )

    sources = build_youtube_idea_sources([ranking], [article])

    assert sources == [
        YouTubeIdeaSource(
            article_id=7,
            title="Priority news",
            summary="Stored summary",
            source="Example News",
            published_at=None,
            importance_score=90,
            relevance_score=80,
            priority_score=12.345,
        )
    ]


def test_build_sources_rejects_missing_article() -> None:
    with pytest.raises(ValueError, match="was not found"):
        build_youtube_idea_sources([RankingResult(99, 80.0, 80, 80)], [])


@pytest.mark.parametrize("idea_count", [1, 3])
def test_local_generator_is_deterministic_and_respects_count(idea_count: int) -> None:
    sources = [source()]
    first = generate_youtube_ideas(
        sources,
        LocalYouTubeIdeaGenerator(),
        channel_focus="AI industry news",
        idea_count=idea_count,
    )
    second = generate_youtube_ideas(
        sources,
        LocalYouTubeIdeaGenerator(),
        channel_focus="AI industry news",
        idea_count=idea_count,
    )
    assert first == second
    assert len(first) == idea_count
    assert all(item.source_article_ids == [1] for item in first)
    assert all("AI industry news" in item.angle for item in first)


@pytest.mark.parametrize(
    ("sources", "focus", "count"),
    [
        ([], "AI news", 3),
        ([source()], "", 3),
        ([source()], "   ", 3),
        ([source()], "AI news", 0),
        ([source()], "AI news", -1),
        ([source()], "AI news", True),
        ([source()], "AI news", 11),
    ],
)
def test_service_rejects_invalid_request_before_generator(sources, focus, count) -> None:
    class UnexpectedGenerator:
        def generate(self, sources, *, channel_focus, idea_count):
            raise AssertionError("generator must not be called")

    with pytest.raises(ValueError):
        generate_youtube_ideas(
            sources,
            UnexpectedGenerator(),
            channel_focus=focus,
            idea_count=count,
        )


def test_service_rejects_returned_count_mismatch() -> None:
    class ShortGenerator:
        def generate(self, sources, *, channel_focus, idea_count):
            return [idea(), idea()]

    with pytest.raises(ValueError, match="exactly"):
        generate_youtube_ideas(
            [source()], ShortGenerator(), channel_focus="AI news", idea_count=3
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [("title", ""), ("chapters", []), ("estimated_length_minutes", 0)],
)
def test_service_defensively_rejects_invalid_returned_idea(field, invalid_value) -> None:
    invalid = idea()
    object.__setattr__(invalid, field, invalid_value)

    class InvalidGenerator:
        def generate(self, sources, *, channel_focus, idea_count):
            return [invalid]

    with pytest.raises(ValueError):
        generate_youtube_ideas(
            [source()], InvalidGenerator(), channel_focus="AI news", idea_count=1
        )


def test_idea_rejects_duplicate_and_unknown_source_ids() -> None:
    with pytest.raises(ValueError, match="duplicates"):
        idea(source_article_ids=[1, 1])

    class UnknownSourceGenerator:
        def generate(self, sources, *, channel_focus, idea_count):
            return [idea(source_article_ids=[99])]

    with pytest.raises(ValueError, match="outside"):
        generate_youtube_ideas(
            [source()], UnknownSourceGenerator(), channel_focus="AI news", idea_count=1
        )

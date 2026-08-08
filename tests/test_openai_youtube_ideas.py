from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast

import pytest

from app.openai_youtube_ideas import (
    OpenAIYouTubeIdeaGenerator,
    OpenAIYouTubeIdeaResponse,
    OpenAIYouTubeIdeasResponse,
)
from app.youtube_ideas import YouTubeIdeaGenerator, YouTubeIdeaSource


def source() -> YouTubeIdeaSource:
    return YouTubeIdeaSource(
        article_id=5,
        title="New AI model released",
        summary="The company released a faster model.",
        source="AI News",
        published_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        importance_score=90,
        relevance_score=95,
        priority_score=92.0,
    )


def parsed_idea(**overrides) -> OpenAIYouTubeIdeaResponse:
    values = {
        "source_article_ids": [5],
        "title": "The new AI model explained",
        "hook": "A faster model just changed the competitive picture.",
        "angle": "Technical explanation and industry impact.",
        "target_audience": "AI industry viewers",
        "estimated_length_minutes": 10,
        "thumbnail_text": "NEW AI MODEL",
        "chapters": ["The release", "Technical changes", "Industry impact"],
        "seo_keywords": ["AI model", "AI news"],
    }
    values.update(overrides)
    return OpenAIYouTubeIdeaResponse(**values)


class FakeResponses:
    def __init__(self, parsed=None, error: Exception | None = None) -> None:
        self.parsed = parsed
        self.error = error
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(type="output_text", parsed=self.parsed)],
                )
            ]
        )


class FakeClient:
    def __init__(self, parsed=None, error: Exception | None = None) -> None:
        self.responses = FakeResponses(parsed, error)


def test_openai_generator_uses_typed_responses_api_and_compact_context() -> None:
    client = FakeClient(OpenAIYouTubeIdeasResponse(ideas=[parsed_idea()]))
    generator: YouTubeIdeaGenerator = cast(
        YouTubeIdeaGenerator,
        OpenAIYouTubeIdeaGenerator(client=client, model="test-model"),
    )

    ideas = generator.generate(
        [source()], channel_focus="AI industry news", idea_count=1
    )

    assert len(ideas) == 1
    assert ideas[0].source_article_ids == [5]
    assert ideas[0].title == "The new AI model explained"
    call = client.responses.calls[0]
    assert call["model"] == "test-model"
    assert call["text_format"] is OpenAIYouTubeIdeasResponse
    for expected in (
        "AI industry news",
        '"idea_count":1',
        "New AI model released",
        "The company released a faster model.",
        '"importance_score":90',
        '"relevance_score":95',
        '"priority_score":92.0',
    ):
        assert expected in call["input"]


def test_openai_generator_rejects_empty_or_short_ideas() -> None:
    empty = SimpleNamespace(ideas=[])
    with pytest.raises(ValueError, match="exactly"):
        OpenAIYouTubeIdeaGenerator(client=FakeClient(empty)).generate(
            [source()], channel_focus="AI news", idea_count=1
        )

    short = OpenAIYouTubeIdeasResponse(ideas=[parsed_idea()])
    with pytest.raises(ValueError, match="exactly"):
        OpenAIYouTubeIdeaGenerator(client=FakeClient(short)).generate(
            [source()], channel_focus="AI news", idea_count=2
        )


def test_openai_generator_rejects_invalid_source_reference() -> None:
    parsed = OpenAIYouTubeIdeasResponse(
        ideas=[parsed_idea(source_article_ids=[99])]
    )
    with pytest.raises(ValueError, match="outside"):
        OpenAIYouTubeIdeaGenerator(client=FakeClient(parsed)).generate(
            [source()], channel_focus="AI news", idea_count=1
        )


def test_openai_generator_rejects_blank_idea_fields() -> None:
    parsed = SimpleNamespace(
        ideas=[
            SimpleNamespace(
                source_article_ids=[5],
                title="   ",
                hook="Hook",
                angle="Angle",
                target_audience="Audience",
                estimated_length_minutes=8,
                thumbnail_text="TEXT",
                chapters=["Chapter"],
                seo_keywords=["keyword"],
            )
        ]
    )
    with pytest.raises(ValueError, match="title"):
        OpenAIYouTubeIdeaGenerator(client=FakeClient(parsed)).generate(
            [source()], channel_focus="AI news", idea_count=1
        )


def test_openai_generator_rejects_missing_parsed_output() -> None:
    with pytest.raises(ValueError, match="parsed YouTube ideas"):
        OpenAIYouTubeIdeaGenerator(client=FakeClient()).generate(
            [source()], channel_focus="AI news", idea_count=1
        )


def test_openai_generator_propagates_provider_exception() -> None:
    with pytest.raises(RuntimeError, match="API unavailable"):
        OpenAIYouTubeIdeaGenerator(
            client=FakeClient(error=RuntimeError("API unavailable"))
        ).generate([source()], channel_focus="AI news", idea_count=1)

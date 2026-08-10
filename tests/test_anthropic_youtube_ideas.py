from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.anthropic_youtube_ideas import (
    AnthropicYouTubeIdeaGenerator,
    AnthropicYouTubeIdeaResponse,
    AnthropicYouTubeIdeasResponse,
)
from app.youtube_ideas import YouTubeIdeaGenerator, YouTubeIdeaSource
from support_anthropic import FakeClient, NoToolCallClient, call_input_text


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


def parsed_idea(**overrides) -> AnthropicYouTubeIdeaResponse:
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
    return AnthropicYouTubeIdeaResponse(**values)


def test_anthropic_generator_uses_structured_tool_call_and_compact_context() -> None:
    client = FakeClient(AnthropicYouTubeIdeasResponse(ideas=[parsed_idea()]))
    generator: YouTubeIdeaGenerator = AnthropicYouTubeIdeaGenerator(
        client=client, model="test-model"
    )

    ideas = generator.generate(
        [source()], channel_focus="AI industry news", idea_count=1
    )

    assert len(ideas) == 1
    assert ideas[0].source_article_ids == [5]
    assert ideas[0].title == "The new AI model explained"
    call = client.messages.calls[0]
    assert call["model"] == "test-model"
    assert call["tools"][0]["input_schema"] == AnthropicYouTubeIdeasResponse.model_json_schema()
    text = call_input_text(call)
    for expected in (
        "AI industry news",
        '"idea_count":1',
        "New AI model released",
        "The company released a faster model.",
        '"importance_score":90',
        '"relevance_score":95',
        '"priority_score":92.0',
    ):
        assert expected in text


def test_anthropic_generator_rejects_an_empty_idea_list() -> None:
    # The forced tool schema requires at least one idea, so an empty list is rejected
    # by structured-output validation itself rather than reaching the app-level check.
    empty = SimpleNamespace(ideas=[])
    with pytest.raises(ValueError, match="at least 1 item"):
        AnthropicYouTubeIdeaGenerator(client=FakeClient(empty)).generate(
            [source()], channel_focus="AI news", idea_count=1
        )


def test_anthropic_generator_rejects_fewer_ideas_than_requested() -> None:
    short = AnthropicYouTubeIdeasResponse(ideas=[parsed_idea()])
    with pytest.raises(ValueError, match="exactly"):
        AnthropicYouTubeIdeaGenerator(client=FakeClient(short)).generate(
            [source()], channel_focus="AI news", idea_count=2
        )


def test_anthropic_generator_rejects_invalid_source_reference() -> None:
    parsed = AnthropicYouTubeIdeasResponse(
        ideas=[parsed_idea(source_article_ids=[99])]
    )
    with pytest.raises(ValueError, match="outside"):
        AnthropicYouTubeIdeaGenerator(client=FakeClient(parsed)).generate(
            [source()], channel_focus="AI news", idea_count=1
        )


def test_anthropic_generator_rejects_blank_idea_fields() -> None:
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
        AnthropicYouTubeIdeaGenerator(client=FakeClient(parsed)).generate(
            [source()], channel_focus="AI news", idea_count=1
        )


def test_anthropic_generator_rejects_missing_tool_call_output() -> None:
    with pytest.raises(ValueError, match="structured tool call"):
        AnthropicYouTubeIdeaGenerator(client=NoToolCallClient()).generate(
            [source()], channel_focus="AI news", idea_count=1
        )


def test_anthropic_generator_propagates_provider_exception() -> None:
    with pytest.raises(RuntimeError, match="API unavailable"):
        AnthropicYouTubeIdeaGenerator(
            client=FakeClient(error=RuntimeError("API unavailable"))
        ).generate([source()], channel_focus="AI news", idea_count=1)

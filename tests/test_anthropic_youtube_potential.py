from types import SimpleNamespace

import pytest

from app.anthropic_youtube_potential import (
    AnthropicYouTubePotentialDimension,
    AnthropicYouTubePotentialResponse,
    AnthropicYouTubePotentialScorer,
)
from app.youtube_ideas import YouTubeIdea
from app.youtube_potential import YouTubePotentialScorer
from support_anthropic import FakeClient, NoToolCallClient, call_input_text


def idea() -> YouTubeIdea:
    return YouTubeIdea(
        source_article_ids=[5],
        title="New AI model explained",
        hook="A faster model changed the competitive picture.",
        angle="Technical explanation and industry impact.",
        target_audience="AI industry viewers",
        estimated_length_minutes=10,
        thumbnail_text="NEW AI MODEL",
        chapters=["The release", "Technical changes", "Industry impact"],
        seo_keywords=["AI model", "AI news"],
    )


def evaluation(index: int = 0, **overrides) -> AnthropicYouTubePotentialDimension:
    values = {
        "idea_index": index,
        "topic_appeal_score": 90,
        "clarity_score": 85,
        "surprise_score": 80,
        "searchability_score": 75,
        "visual_explainability_score": 70,
        "reason": "Strong explanatory idea.",
    }
    values.update(overrides)
    return AnthropicYouTubePotentialDimension(**values)


def test_anthropic_scorer_uses_structured_tool_call_and_compact_context() -> None:
    client = FakeClient(AnthropicYouTubePotentialResponse(evaluations=[evaluation()]))
    scorer: YouTubePotentialScorer = AnthropicYouTubePotentialScorer(
        client=client, model="test-model"
    )

    results = scorer.score([idea()], channel_focus="AI industry news")

    assert len(results) == 1
    assert results[0].topic_appeal_score == 90
    assert results[0].reason == "Strong explanatory idea."
    call = client.messages.calls[0]
    assert call["model"] == "test-model"
    assert "youtube_potential_score" not in AnthropicYouTubePotentialDimension.model_fields
    text = call_input_text(call)
    for expected in (
        "AI industry news",
        '"idea_index":0',
        "New AI model explained",
        "A faster model changed the competitive picture.",
        "Technical explanation and industry impact.",
        "AI industry viewers",
        "NEW AI MODEL",
        "Technical changes",
        "AI news",
    ):
        assert expected in text


@pytest.mark.parametrize(
    "evaluations",
    [
        [evaluation(0), evaluation(0)],
        [evaluation(1)],
        [SimpleNamespace(
            idea_index=0,
            topic_appeal_score=80,
            clarity_score=80,
            surprise_score=80,
            searchability_score=80,
            visual_explainability_score=80,
            reason="   ",
        )],
    ],
)
def test_anthropic_scorer_rejects_invalid_provider_results(evaluations) -> None:
    parsed = SimpleNamespace(evaluations=evaluations)
    with pytest.raises(ValueError):
        AnthropicYouTubePotentialScorer(client=FakeClient(parsed)).score(
            [idea(), idea()] if len(evaluations) == 2 else [idea()],
            channel_focus="AI news",
        )


def test_anthropic_scorer_rejects_out_of_range_dimension_score() -> None:
    parsed = SimpleNamespace(evaluations=[SimpleNamespace(
        idea_index=0,
        topic_appeal_score=101,
        clarity_score=80,
        surprise_score=80,
        searchability_score=80,
        visual_explainability_score=80,
        reason="Reason",
    )])
    with pytest.raises(ValueError):
        AnthropicYouTubePotentialScorer(client=FakeClient(parsed)).score(
            [idea()], channel_focus="AI news"
        )


def test_anthropic_scorer_rejects_missing_tool_call_output() -> None:
    with pytest.raises(ValueError, match="structured tool call"):
        AnthropicYouTubePotentialScorer(client=NoToolCallClient()).score(
            [idea()], channel_focus="AI news"
        )


def test_anthropic_scorer_propagates_provider_exception() -> None:
    with pytest.raises(RuntimeError, match="API unavailable"):
        AnthropicYouTubePotentialScorer(
            client=FakeClient(error=RuntimeError("API unavailable"))
        ).score([idea()], channel_focus="AI news")

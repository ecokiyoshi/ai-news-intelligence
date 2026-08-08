from types import SimpleNamespace
from typing import cast

import pytest

from app.openai_youtube_potential import (
    OpenAIYouTubePotentialDimension,
    OpenAIYouTubePotentialResponse,
    OpenAIYouTubePotentialScorer,
)
from app.youtube_ideas import YouTubeIdea
from app.youtube_potential import YouTubePotentialScorer


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


def evaluation(index: int = 0, **overrides) -> OpenAIYouTubePotentialDimension:
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
    return OpenAIYouTubePotentialDimension(**values)


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


def test_openai_scorer_uses_typed_responses_api_and_compact_context() -> None:
    client = FakeClient(OpenAIYouTubePotentialResponse(evaluations=[evaluation()]))
    scorer: YouTubePotentialScorer = cast(
        YouTubePotentialScorer,
        OpenAIYouTubePotentialScorer(client=client, model="test-model"),
    )

    results = scorer.score([idea()], channel_focus="AI industry news")

    assert len(results) == 1
    assert results[0].topic_appeal_score == 90
    assert results[0].reason == "Strong explanatory idea."
    call = client.responses.calls[0]
    assert call["model"] == "test-model"
    assert call["text_format"] is OpenAIYouTubePotentialResponse
    assert "youtube_potential_score" not in OpenAIYouTubePotentialDimension.model_fields
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
        assert expected in call["input"]


@pytest.mark.parametrize(
    "evaluations",
    [
        [evaluation(0), evaluation(0)],
        [evaluation(1)],
        [SimpleNamespace(
            idea_index=0,
            topic_appeal_score=101,
            clarity_score=80,
            surprise_score=80,
            searchability_score=80,
            visual_explainability_score=80,
            reason="Reason",
        )],
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
def test_openai_scorer_rejects_invalid_provider_results(evaluations) -> None:
    parsed = SimpleNamespace(evaluations=evaluations)
    with pytest.raises(ValueError):
        OpenAIYouTubePotentialScorer(client=FakeClient(parsed)).score(
            [idea(), idea()] if len(evaluations) == 2 else [idea()],
            channel_focus="AI news",
        )


def test_openai_scorer_rejects_missing_parsed_output() -> None:
    with pytest.raises(ValueError, match="parsed potential"):
        OpenAIYouTubePotentialScorer(client=FakeClient()).score(
            [idea()], channel_focus="AI news"
        )


def test_openai_scorer_propagates_provider_exception() -> None:
    with pytest.raises(RuntimeError, match="API unavailable"):
        OpenAIYouTubePotentialScorer(
            client=FakeClient(error=RuntimeError("API unavailable"))
        ).score([idea()], channel_focus="AI news")

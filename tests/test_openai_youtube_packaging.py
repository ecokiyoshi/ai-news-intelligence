from types import SimpleNamespace
from typing import cast

import pytest

from app.openai_youtube_packaging import (
    OpenAIYouTubePackagingDimension,
    OpenAIYouTubePackagingDraft,
    OpenAIYouTubePackagingEvaluationResponse,
    OpenAIYouTubePackagingEvaluator,
    OpenAIYouTubePackagingGenerationResponse,
    OpenAIYouTubePackagingGenerator,
)
from app.youtube_packaging import (
    YouTubePackagingDraft,
    YouTubePackagingEvaluator,
    YouTubePackagingGenerator,
    YouTubePackagingSource,
)


def source() -> YouTubePackagingSource:
    return YouTubePackagingSource(
        idea_index=0, source_article_ids=[1],
        title="New AI model explained", hook="Why the release matters",
        angle="Technical and industry impact", target_audience="AI builders",
        estimated_length_minutes=8, current_thumbnail_text="NEW AI MODEL", chapters=["Release", "Impact"],
        seo_keywords=["AI model", "release"], youtube_potential_score=12.345,
    )


class FakeResponses:
    def __init__(self, parsed=None, error: Exception | None = None):
        self.parsed = parsed
        self.error = error
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error: raise self.error
        return SimpleNamespace(output=[SimpleNamespace(type="message", content=[SimpleNamespace(parsed=self.parsed)])])


class FakeClient:
    def __init__(self, parsed=None, error: Exception | None = None):
        self.responses = FakeResponses(parsed, error)


def generation(index=0, **overrides):
    values = dict(candidate_index=index, title=f"Title {index}", thumbnail_text=f"Thumb {index}", rationale=f"Rationale {index}")
    values.update(overrides)
    return OpenAIYouTubePackagingDraft(**values)


def draft(index=0):
    return YouTubePackagingDraft(index, f"Title {index}", f"Thumb {index}", f"Rationale {index}")


def evaluation(index=0, **overrides):
    values = dict(candidate_index=index, clarity_score=80, curiosity_score=81,
                  specificity_score=82, truthfulness_score=83,
                  thumbnail_synergy_score=84, reason="Specific and truthful")
    values.update(overrides)
    return OpenAIYouTubePackagingDimension(**values)


def test_openai_generator_is_compatible_and_uses_typed_responses_context() -> None:
    client = FakeClient(OpenAIYouTubePackagingGenerationResponse(candidates=[generation(1), generation(0)]))
    generator: YouTubePackagingGenerator = cast(YouTubePackagingGenerator, OpenAIYouTubePackagingGenerator(client=client, model="test-model"))
    drafts = generator.generate(source(), channel_focus="AI news", candidate_count=2)
    assert [x.candidate_index for x in drafts] == [0, 1]
    call = client.responses.calls[0]
    assert call["model"] == "test-model"
    assert call["text_format"] is OpenAIYouTubePackagingGenerationResponse
    for expected in ("AI news", '"candidate_count":2', '"idea_index":0', '"source_article_ids":[1]', '"estimated_length_minutes":8', "New AI model explained", "Why the release matters", "Technical and industry impact", "AI builders", "NEW AI MODEL", "Release", "AI model", "12.345"):
        assert expected in call["input"]
    assert "packaging_score" not in OpenAIYouTubePackagingDraft.model_fields


def test_openai_evaluator_is_compatible_and_returns_only_dimensions() -> None:
    client = FakeClient(OpenAIYouTubePackagingEvaluationResponse(evaluations=[evaluation(1), evaluation(0)]))
    evaluator: YouTubePackagingEvaluator = cast(YouTubePackagingEvaluator, OpenAIYouTubePackagingEvaluator(client=client, model="evaluation-model"))
    dimensions = evaluator.evaluate(source(), [draft(0), draft(1)], channel_focus="AI news")
    assert [x.candidate_index for x in dimensions] == [0, 1]
    assert dimensions[0].clarity_score == 80
    call = client.responses.calls[0]
    assert call["model"] == "evaluation-model"
    assert call["text_format"] is OpenAIYouTubePackagingEvaluationResponse
    assert "Title 0" in call["input"] and "Thumb 1" in call["input"]
    assert "packaging_score" not in OpenAIYouTubePackagingDimension.model_fields


@pytest.mark.parametrize("candidates", [
    [generation(0), generation(0)],
    [generation(1)],
    [SimpleNamespace(candidate_index=0, title=" ", thumbnail_text="Thumb", rationale="Why")],
])
def test_openai_generator_rejects_invalid_provider_output(candidates) -> None:
    with pytest.raises(ValueError):
        OpenAIYouTubePackagingGenerator(client=FakeClient(SimpleNamespace(candidates=candidates))).generate(source(), channel_focus="AI", candidate_count=2 if len(candidates) == 2 else 1)


def test_openai_generator_rejects_normalized_duplicate_copy() -> None:
    parsed = OpenAIYouTubePackagingGenerationResponse(candidates=[
        generation(0, title="BIG  NEWS", thumbnail_text="WATCH NOW"),
        generation(1, title=" big news ", thumbnail_text="watch  now"),
    ])
    with pytest.raises(ValueError, match="duplicate title"):
        OpenAIYouTubePackagingGenerator(client=FakeClient(parsed)).generate(source(), channel_focus="AI", candidate_count=2)


@pytest.mark.parametrize("evaluations", [
    [evaluation(0), evaluation(0)],
    [evaluation(1)],
    [SimpleNamespace(candidate_index=0, clarity_score=101, curiosity_score=80, specificity_score=80, truthfulness_score=80, thumbnail_synergy_score=80, reason="Reason")],
    [SimpleNamespace(candidate_index=0, clarity_score=80, curiosity_score=80, specificity_score=80, truthfulness_score=80, thumbnail_synergy_score=80, reason="  ")],
])
def test_openai_evaluator_rejects_invalid_provider_output(evaluations) -> None:
    with pytest.raises(ValueError):
        OpenAIYouTubePackagingEvaluator(client=FakeClient(SimpleNamespace(evaluations=evaluations))).evaluate(source(), [draft(0), draft(1)] if len(evaluations) == 2 else [draft(0)], channel_focus="AI")


@pytest.mark.parametrize("provider", ["generator", "evaluator"])
def test_openai_providers_reject_missing_parsed_output(provider: str) -> None:
    with pytest.raises(ValueError, match="parsed packaging"):
        if provider == "generator":
            OpenAIYouTubePackagingGenerator(client=FakeClient()).generate(source(), channel_focus="AI", candidate_count=1)
        else:
            OpenAIYouTubePackagingEvaluator(client=FakeClient()).evaluate(source(), [draft(0)], channel_focus="AI")


@pytest.mark.parametrize("provider", ["generator", "evaluator"])
def test_openai_provider_exception_propagates_without_network_or_api_key(provider: str) -> None:
    client = FakeClient(error=RuntimeError("API unavailable"))
    with pytest.raises(RuntimeError, match="API unavailable"):
        if provider == "generator":
            OpenAIYouTubePackagingGenerator(client=client).generate(source(), channel_focus="AI", candidate_count=1)
        else:
            OpenAIYouTubePackagingEvaluator(client=client).evaluate(source(), [draft(0)], channel_focus="AI")


def test_boundary_scores_zero_and_one_hundred() -> None:
    parsed = OpenAIYouTubePackagingEvaluationResponse(evaluations=[evaluation(0, clarity_score=0, curiosity_score=100, specificity_score=0, truthfulness_score=100, thumbnail_synergy_score=0)])
    result = OpenAIYouTubePackagingEvaluator(client=FakeClient(parsed)).evaluate(source(), [draft(0)], channel_focus="AI")[0]
    assert result.clarity_score == 0 and result.curiosity_score == 100

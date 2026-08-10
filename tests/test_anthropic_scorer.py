from types import SimpleNamespace

import pytest

from app.anthropic_scorer import AnthropicScoreResponse, AnthropicScorer
from app.scoring import InvalidScoreResultError, ScoreResult, Scorer
from support_anthropic import FakeClient, NoToolCallClient, call_input_text


def test_anthropic_scorer_matches_interface_and_sends_inputs() -> None:
    client = FakeClient(AnthropicScoreResponse(
        importance_score=82,
        relevance_score=91,
        reason="Significant and directly relevant.",
    ))
    scorer: Scorer = AnthropicScorer(client=client, model="test-model")

    result = scorer.score("Article body with factual details.", "AI model releases")

    assert result == ScoreResult(82, 91, "Significant and directly relevant.")
    call = client.messages.calls[0]
    assert call["model"] == "test-model"
    text = call_input_text(call)
    assert "Article body with factual details." in text
    assert "AI model releases" in text
    assert call["tools"][0]["input_schema"] == AnthropicScoreResponse.model_json_schema()


@pytest.mark.parametrize(
    ("importance", "relevance"), [(0, 0), (100, 100)]
)
def test_anthropic_scorer_accepts_score_boundaries(importance: int, relevance: int) -> None:
    client = FakeClient(AnthropicScoreResponse(
        importance_score=importance,
        relevance_score=relevance,
        reason="Boundary score.",
    ))
    result = AnthropicScorer(client=client).score("Text", "Target")
    assert result.importance_score == importance
    assert result.relevance_score == relevance


def test_anthropic_scorer_rejects_blank_reason_via_core_validation() -> None:
    # The schema's Field(min_length=1) only checks raw length, so a whitespace-only
    # reason still reaches core `validate_score_result`, which enforces the real rule.
    parsed = SimpleNamespace(importance_score=50, relevance_score=50, reason=" ")
    with pytest.raises(InvalidScoreResultError):
        AnthropicScorer(client=FakeClient(parsed)).score("Text", "Target")


def test_anthropic_scorer_rejects_out_of_range_score() -> None:
    with pytest.raises(ValueError):
        AnthropicScorer(client=FakeClient(SimpleNamespace(
            importance_score=101, relevance_score=50, reason="Invalid"
        ))).score("Text", "Target")


def test_anthropic_scorer_rejects_missing_tool_call_output() -> None:
    with pytest.raises(ValueError, match="structured tool call"):
        AnthropicScorer(client=NoToolCallClient()).score("Text", "Target")


def test_anthropic_scorer_propagates_provider_exception() -> None:
    with pytest.raises(RuntimeError, match="API unavailable"):
        AnthropicScorer(client=FakeClient(error=RuntimeError("API unavailable"))).score(
            "Text", "Target"
        )

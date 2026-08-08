from types import SimpleNamespace
from typing import cast

import pytest

from app.openai_scorer import OpenAIScoreResponse, OpenAIScorer
from app.scoring import InvalidScoreResultError, ScoreResult, Scorer


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
        self.responses = FakeResponses(parsed=parsed, error=error)


def test_openai_scorer_matches_interface_and_sends_inputs() -> None:
    client = FakeClient(OpenAIScoreResponse(
        importance_score=82,
        relevance_score=91,
        reason="Significant and directly relevant.",
    ))
    scorer: Scorer = cast(Scorer, OpenAIScorer(client=client, model="test-model"))

    result = scorer.score("Article body with factual details.", "AI model releases")

    assert result == ScoreResult(82, 91, "Significant and directly relevant.")
    call = client.responses.calls[0]
    assert call["model"] == "test-model"
    assert "Article body with factual details." in call["input"]
    assert "AI model releases" in call["input"]
    assert call["text_format"] is OpenAIScoreResponse


@pytest.mark.parametrize(
    ("importance", "relevance"), [(0, 0), (100, 100)]
)
def test_openai_scorer_accepts_score_boundaries(importance: int, relevance: int) -> None:
    client = FakeClient(OpenAIScoreResponse(
        importance_score=importance,
        relevance_score=relevance,
        reason="Boundary score.",
    ))
    result = OpenAIScorer(client=client).score("Text", "Target")
    assert result.importance_score == importance
    assert result.relevance_score == relevance


def test_openai_scorer_rejects_invalid_parsed_result() -> None:
    parsed = SimpleNamespace(importance_score=101, relevance_score=50, reason="Invalid")
    with pytest.raises(InvalidScoreResultError):
        OpenAIScorer(client=FakeClient(parsed)).score("Text", "Target")


def test_openai_scorer_rejects_missing_parsed_output() -> None:
    with pytest.raises(ValueError, match="parsed score data"):
        OpenAIScorer(client=FakeClient()).score("Text", "Target")


def test_openai_scorer_propagates_provider_exception() -> None:
    with pytest.raises(RuntimeError, match="API unavailable"):
        OpenAIScorer(client=FakeClient(error=RuntimeError("API unavailable"))).score(
            "Text", "Target"
        )

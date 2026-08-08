"""OpenAI-backed implementation of the article scorer interface."""

import os
from typing import Any, Protocol

from openai import OpenAI
from pydantic import BaseModel, Field

from app.openai_summarizer import DEFAULT_OPENAI_MODEL
from app.scoring import ScoreResult, validate_score_result

SCORING_INSTRUCTIONS = """\
Evaluate only the supplied article text and do not invent facts.
Importance is the article's general news significance from 0 to 100. Consider impact scale,
affected people or organizations, novelty, economic, social, and technical consequences, urgency,
and time sensitivity. Relevance is its relevance from 0 to 100 to the supplied target.
Give a brief reason, preserve factual names and numbers, and output only the required structured data.
"""


class OpenAIScoreResponse(BaseModel):
    """Schema supplied to the Responses API typed parsing helper."""

    importance_score: int = Field(ge=0, le=100)
    relevance_score: int = Field(ge=0, le=100)
    reason: str = Field(min_length=1)


class ResponsesParser(Protocol):
    """Minimal typed Responses API surface used by the provider."""

    def parse(
        self,
        *,
        model: str,
        instructions: str,
        input: str,
        text_format: type[OpenAIScoreResponse],
    ) -> Any:
        """Create and parse a structured model response."""


class OpenAIParsingClient(Protocol):
    """Minimal injectable OpenAI client surface used by the provider."""

    responses: ResponsesParser


class OpenAIScorer:
    """Score article text with the OpenAI Responses API."""

    def __init__(
        self,
        *,
        client: OpenAIParsingClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else OpenAI(timeout=timeout)
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL

    def score(self, text: str, relevance_target: str) -> ScoreResult:
        response = self.client.responses.parse(
            model=self.model,
            instructions=SCORING_INSTRUCTIONS,
            input=(
                f"Relevance target:\n{relevance_target}\n\n"
                f"Article text:\n{text}"
            ),
            text_format=OpenAIScoreResponse,
        )

        parsed = self._parsed_output(response)
        return validate_score_result(
            ScoreResult(
                importance_score=parsed.importance_score,
                relevance_score=parsed.relevance_score,
                reason=parsed.reason,
            )
        )

    @staticmethod
    def _parsed_output(response: Any) -> OpenAIScoreResponse:
        for output_item in response.output:
            if getattr(output_item, "type", None) != "message":
                continue
            for content_item in output_item.content:
                parsed = getattr(content_item, "parsed", None)
                if parsed is not None:
                    return parsed
        raise ValueError("OpenAI response did not contain parsed score data")

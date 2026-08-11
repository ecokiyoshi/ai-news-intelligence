"""Claude-backed implementation of the article scorer interface."""

from pydantic import BaseModel, Field

from app.anthropic_client import AnthropicClient, build_default_client, parse_structured, resolve_model
from app.scoring import ScoreResult, validate_score_result

SCORING_INSTRUCTIONS = """\
Evaluate only the supplied article text and do not invent facts.
Importance is the article's general news significance from 0 to 100. Consider impact scale,
affected people or organizations, novelty, economic, social, and technical consequences, urgency,
and time sensitivity. Relevance is its relevance from 0 to 100 to the supplied target.
Give a brief reason, preserve factual names and numbers, and output only the required structured data.
"""


class AnthropicScoreResponse(BaseModel):
    """Schema forced onto the Claude structured tool call."""

    importance_score: int = Field(ge=0, le=100)
    relevance_score: int = Field(ge=0, le=100)
    reason: str = Field(min_length=1)


class AnthropicScorer:
    """Score article text with the Anthropic Messages API."""

    def __init__(
        self,
        *,
        client: AnthropicClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else build_default_client(timeout)
        self.model = resolve_model(model)

    def score(self, text: str, relevance_target: str) -> ScoreResult:
        parsed = parse_structured(
            self.client,
            model=self.model,
            system=SCORING_INSTRUCTIONS,
            input_text=(
                f"Relevance target:\n{relevance_target}\n\n"
                f"Article text:\n{text}"
            ),
            response_model=AnthropicScoreResponse,
        )
        return validate_score_result(
            ScoreResult(
                importance_score=parsed.importance_score,
                relevance_score=parsed.relevance_score,
                reason=parsed.reason,
            )
        )

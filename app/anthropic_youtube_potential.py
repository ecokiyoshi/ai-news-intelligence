"""Claude-backed YouTube potential dimension scorer."""

import json

from pydantic import BaseModel, Field, StrictInt

from app.anthropic_client import AnthropicClient, build_default_client, parse_structured, resolve_model
from app.youtube_ideas import YouTubeIdea
from app.youtube_potential import (
    DEFAULT_YOUTUBE_POTENTIAL_WEIGHTS,
    YouTubePotentialDimensions,
    validate_dimension_results,
    validate_scoring_request,
)

YOUTUBE_POTENTIAL_INSTRUCTIONS = """\
Evaluate each supplied YouTube video idea, not the general importance of its source news.
Score topic appeal, clarity, surprise, searchability, and visual explainability from 0 to 100 using
only the supplied idea information and channel focus. Do not invent search volume, click-through
rate, audience size, trend data, or view predictions. Searchability is only a heuristic based on
the title, topic, and SEO keywords. Do not reward misleading clickbait; reward clear explanatory
value, strong truthful hooks, and ideas that naturally support visual storytelling. Return exactly
one evaluation for each idea, preserve every idea_index, and return only the structured schema.
"""


class AnthropicYouTubePotentialDimension(BaseModel):
    """Typed schema for one provider dimension evaluation."""

    idea_index: StrictInt = Field(ge=0)
    topic_appeal_score: StrictInt = Field(ge=0, le=100)
    clarity_score: StrictInt = Field(ge=0, le=100)
    surprise_score: StrictInt = Field(ge=0, le=100)
    searchability_score: StrictInt = Field(ge=0, le=100)
    visual_explainability_score: StrictInt = Field(ge=0, le=100)
    reason: str = Field(min_length=1)


class AnthropicYouTubePotentialResponse(BaseModel):
    """Typed structured-tool-call container for potential evaluations."""

    evaluations: list[AnthropicYouTubePotentialDimension] = Field(min_length=1)


class AnthropicYouTubePotentialScorer:
    """Evaluate YouTube idea dimensions with the Anthropic Messages API."""

    def __init__(
        self,
        *,
        client: AnthropicClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else build_default_client(timeout)
        self.model = resolve_model(model)

    def score(
        self, ideas: list[YouTubeIdea], *, channel_focus: str
    ) -> list[YouTubePotentialDimensions]:
        ideas, focus, _ = validate_scoring_request(
            ideas, channel_focus, DEFAULT_YOUTUBE_POTENTIAL_WEIGHTS
        )
        parsed = parse_structured(
            self.client,
            model=self.model,
            system=YOUTUBE_POTENTIAL_INSTRUCTIONS,
            input_text=self._input_payload(ideas, focus),
            response_model=AnthropicYouTubePotentialResponse,
        )
        dimensions = [
            YouTubePotentialDimensions(
                idea_index=evaluation.idea_index,
                topic_appeal_score=evaluation.topic_appeal_score,
                clarity_score=evaluation.clarity_score,
                surprise_score=evaluation.surprise_score,
                searchability_score=evaluation.searchability_score,
                visual_explainability_score=evaluation.visual_explainability_score,
                reason=evaluation.reason,
            )
            for evaluation in parsed.evaluations
        ]
        return validate_dimension_results(dimensions, len(ideas))

    @staticmethod
    def _input_payload(ideas: list[YouTubeIdea], channel_focus: str) -> str:
        context = {
            "channel_focus": channel_focus,
            "ideas": [
                {
                    "idea_index": index,
                    "source_article_ids": idea.source_article_ids,
                    "title": idea.title,
                    "hook": idea.hook,
                    "angle": idea.angle,
                    "target_audience": idea.target_audience,
                    "estimated_length_minutes": idea.estimated_length_minutes,
                    "thumbnail_text": idea.thumbnail_text,
                    "chapters": idea.chapters,
                    "seo_keywords": idea.seo_keywords,
                }
                for index, idea in enumerate(ideas)
            ],
        }
        return json.dumps(context, ensure_ascii=False, separators=(",", ":"))

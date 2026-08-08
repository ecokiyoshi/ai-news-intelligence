"""OpenAI-backed YouTube potential dimension scorer."""

import json
import os
from typing import Any, Protocol

from openai import OpenAI
from pydantic import BaseModel, Field, StrictInt

from app.openai_summarizer import DEFAULT_OPENAI_MODEL
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


class OpenAIYouTubePotentialDimension(BaseModel):
    """Typed schema for one provider dimension evaluation."""

    idea_index: StrictInt = Field(ge=0)
    topic_appeal_score: StrictInt = Field(ge=0, le=100)
    clarity_score: StrictInt = Field(ge=0, le=100)
    surprise_score: StrictInt = Field(ge=0, le=100)
    searchability_score: StrictInt = Field(ge=0, le=100)
    visual_explainability_score: StrictInt = Field(ge=0, le=100)
    reason: str = Field(min_length=1)


class OpenAIYouTubePotentialResponse(BaseModel):
    """Typed Responses API container for potential evaluations."""

    evaluations: list[OpenAIYouTubePotentialDimension] = Field(min_length=1)


class ResponsesParser(Protocol):
    """Minimal typed Responses API surface used by the provider."""

    def parse(
        self,
        *,
        model: str,
        instructions: str,
        input: str,
        text_format: type[OpenAIYouTubePotentialResponse],
    ) -> Any:
        """Create and parse a structured model response."""


class OpenAIParsingClient(Protocol):
    """Minimal injectable OpenAI client surface used by the provider."""

    responses: ResponsesParser


class OpenAIYouTubePotentialScorer:
    """Evaluate YouTube idea dimensions with the OpenAI Responses API."""

    def __init__(
        self,
        *,
        client: OpenAIParsingClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else OpenAI(timeout=timeout)
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL

    def score(
        self, ideas: list[YouTubeIdea], *, channel_focus: str
    ) -> list[YouTubePotentialDimensions]:
        ideas, focus, _ = validate_scoring_request(
            ideas, channel_focus, DEFAULT_YOUTUBE_POTENTIAL_WEIGHTS
        )
        response = self.client.responses.parse(
            model=self.model,
            instructions=YOUTUBE_POTENTIAL_INSTRUCTIONS,
            input=self._input_payload(ideas, focus),
            text_format=OpenAIYouTubePotentialResponse,
        )
        parsed = self._parsed_output(response)
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

    @staticmethod
    def _parsed_output(response: Any) -> OpenAIYouTubePotentialResponse:
        for output_item in response.output:
            if getattr(output_item, "type", None) != "message":
                continue
            for content_item in output_item.content:
                parsed = getattr(content_item, "parsed", None)
                if parsed is not None:
                    return parsed
        raise ValueError("OpenAI response did not contain parsed potential evaluations")

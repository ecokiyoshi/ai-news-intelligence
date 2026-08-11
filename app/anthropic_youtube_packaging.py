"""Claude-backed YouTube title/thumbnail generation and evaluation."""

import json

from pydantic import BaseModel, Field, StrictInt

from app.anthropic_client import AnthropicClient, build_default_client, parse_structured, resolve_model
from app.youtube_packaging import (
    DEFAULT_YOUTUBE_PACKAGING_WEIGHTS,
    YouTubePackagingDimensions,
    YouTubePackagingDraft,
    YouTubePackagingSource,
    validate_packaging_dimensions,
    validate_packaging_drafts,
    validate_packaging_request,
    validate_packaging_source,
)

YOUTUBE_PACKAGING_GENERATION_INSTRUCTIONS = """\
Generate the exact requested number of distinct YouTube title and thumbnail-copy pairs using only
the supplied video-idea context and channel focus. Keep every claim truthful and supported by the
input. Do not invent urgency, superlatives, facts, analytics, CTR, views, search volume, trends, or
audience behavior. Make titles clear, specific, and curiosity-building without misleading
clickbait. Make thumbnail copy shorter than and complementary to its title, rather than merely
repeating it. Preserve important supported names, dates, and numbers. Never add unsupported claims
such as "world first", "completely over", "absolute", or "100%". Preserve candidate_index values
0 through candidate_count - 1 exactly once and return only the structured schema.
"""

YOUTUBE_PACKAGING_EVALUATION_INSTRUCTIONS = """\
Evaluate each supplied YouTube title and thumbnail-copy pair using only the supplied video-idea
context and channel focus. Score clarity, truthful curiosity, specificity, truthfulness, and title-
thumbnail synergy from 0 to 100. Do not invent or estimate CTR, views, search volume, trends, or
audience analytics. Return only these five dimension scores and a brief reason; do not calculate
or return an overall packaging score. Preserve every candidate_index exactly once and return only
the structured schema.
"""


class AnthropicYouTubePackagingDraft(BaseModel):
    candidate_index: StrictInt = Field(ge=0)
    title: str = Field(min_length=1)
    thumbnail_text: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class AnthropicYouTubePackagingGenerationResponse(BaseModel):
    candidates: list[AnthropicYouTubePackagingDraft] = Field(min_length=1)


class AnthropicYouTubePackagingDimension(BaseModel):
    candidate_index: StrictInt = Field(ge=0)
    clarity_score: StrictInt = Field(ge=0, le=100)
    curiosity_score: StrictInt = Field(ge=0, le=100)
    specificity_score: StrictInt = Field(ge=0, le=100)
    truthfulness_score: StrictInt = Field(ge=0, le=100)
    thumbnail_synergy_score: StrictInt = Field(ge=0, le=100)
    reason: str = Field(min_length=1)


class AnthropicYouTubePackagingEvaluationResponse(BaseModel):
    evaluations: list[AnthropicYouTubePackagingDimension] = Field(min_length=1)


def _source_payload(source: YouTubePackagingSource) -> dict[str, object]:
    return {
        "idea_index": source.idea_index,
        "source_article_ids": source.source_article_ids,
        "title": source.title,
        "hook": source.hook,
        "angle": source.angle,
        "target_audience": source.target_audience,
        "estimated_length_minutes": source.estimated_length_minutes,
        "current_thumbnail_text": source.current_thumbnail_text,
        "chapters": source.chapters,
        "seo_keywords": source.seo_keywords,
        "youtube_potential_score": source.youtube_potential_score,
    }


class AnthropicYouTubePackagingGenerator:
    """Generate title/thumbnail drafts with a forced structured Claude tool call."""

    def __init__(
        self,
        *,
        client: AnthropicClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else build_default_client(timeout)
        self.model = resolve_model(model)

    def generate(
        self,
        source: YouTubePackagingSource,
        *,
        channel_focus: str,
        candidate_count: int,
    ) -> list[YouTubePackagingDraft]:
        source, focus, count, _ = validate_packaging_request(
            source,
            channel_focus,
            candidate_count,
            DEFAULT_YOUTUBE_PACKAGING_WEIGHTS,
        )
        parsed = parse_structured(
            self.client,
            model=self.model,
            system=YOUTUBE_PACKAGING_GENERATION_INSTRUCTIONS,
            input_text=json.dumps(
                {
                    "channel_focus": focus,
                    "candidate_count": count,
                    "source": _source_payload(source),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            response_model=AnthropicYouTubePackagingGenerationResponse,
        )
        drafts = [
            YouTubePackagingDraft(
                candidate_index=candidate.candidate_index,
                title=candidate.title,
                thumbnail_text=candidate.thumbnail_text,
                rationale=candidate.rationale,
            )
            for candidate in parsed.candidates
        ]
        return validate_packaging_drafts(drafts, count)


class AnthropicYouTubePackagingEvaluator:
    """Evaluate packaging dimensions with a forced structured Claude tool call."""

    def __init__(
        self,
        *,
        client: AnthropicClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else build_default_client(timeout)
        self.model = resolve_model(model)

    def evaluate(
        self,
        source: YouTubePackagingSource,
        drafts: list[YouTubePackagingDraft],
        *,
        channel_focus: str,
    ) -> list[YouTubePackagingDimensions]:
        source = validate_packaging_source(source)
        if not isinstance(channel_focus, str) or not channel_focus.strip():
            raise ValueError("channel_focus must be a non-empty string")
        focus = channel_focus.strip()
        if not isinstance(drafts, list) or not drafts:
            raise ValueError("drafts must be a non-empty list")
        drafts = validate_packaging_drafts(drafts, len(drafts))
        parsed = parse_structured(
            self.client,
            model=self.model,
            system=YOUTUBE_PACKAGING_EVALUATION_INSTRUCTIONS,
            input_text=json.dumps(
                {
                    "channel_focus": focus,
                    "candidate_count": len(drafts),
                    "source": _source_payload(source),
                    "candidates": [draft.__dict__ for draft in drafts],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            response_model=AnthropicYouTubePackagingEvaluationResponse,
        )
        dimensions = [
            YouTubePackagingDimensions(
                candidate_index=evaluation.candidate_index,
                clarity_score=evaluation.clarity_score,
                curiosity_score=evaluation.curiosity_score,
                specificity_score=evaluation.specificity_score,
                truthfulness_score=evaluation.truthfulness_score,
                thumbnail_synergy_score=evaluation.thumbnail_synergy_score,
                reason=evaluation.reason,
            )
            for evaluation in parsed.evaluations
        ]
        return validate_packaging_dimensions(dimensions, len(drafts))

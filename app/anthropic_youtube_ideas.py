"""Claude-backed YouTube idea generator."""

import json

from pydantic import BaseModel, Field, StrictInt

from app.anthropic_client import AnthropicClient, build_default_client, parse_structured, resolve_model
from app.youtube_ideas import (
    YouTubeIdea,
    YouTubeIdeaSource,
    validate_generated_ideas,
    validate_generation_request,
)

YOUTUBE_IDEA_INSTRUCTIONS = """\
Use only the supplied news context and do not invent unsupported facts.
Generate distinct, practical YouTube ideas for explanatory and news-analysis videos.
Do not optimize for misleading clickbait. Keep titles specific and understandable, create a strong
opening hook, give each idea a clear editorial angle, define its target audience, produce concise
thumbnail text, a logical chapter outline, and relevant SEO keywords. Preserve important names,
organizations, dates, and numbers. Generally maintain the language of the channel focus and source
context. Return exactly the requested number of ideas and only the structured data required by the
schema.
"""


class AnthropicYouTubeIdeaResponse(BaseModel):
    """Typed schema for one generated idea."""

    source_article_ids: list[StrictInt] = Field(min_length=1)
    title: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    angle: str = Field(min_length=1)
    target_audience: str = Field(min_length=1)
    estimated_length_minutes: StrictInt = Field(gt=0)
    thumbnail_text: str = Field(min_length=1)
    chapters: list[str] = Field(min_length=1)
    seo_keywords: list[str] = Field(min_length=1)


class AnthropicYouTubeIdeasResponse(BaseModel):
    """Typed structured-tool-call container for generated ideas."""

    ideas: list[AnthropicYouTubeIdeaResponse] = Field(min_length=1)


class AnthropicYouTubeIdeaGenerator:
    """Generate structured YouTube ideas with the Anthropic Messages API."""

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
        sources: list[YouTubeIdeaSource],
        *,
        channel_focus: str,
        idea_count: int,
    ) -> list[YouTubeIdea]:
        sources, focus, count = validate_generation_request(
            sources, channel_focus, idea_count
        )
        parsed = parse_structured(
            self.client,
            model=self.model,
            system=YOUTUBE_IDEA_INSTRUCTIONS,
            input_text=self._input_payload(sources, focus, count),
            response_model=AnthropicYouTubeIdeasResponse,
        )
        ideas = [
            YouTubeIdea(
                source_article_ids=idea.source_article_ids,
                title=idea.title,
                hook=idea.hook,
                angle=idea.angle,
                target_audience=idea.target_audience,
                estimated_length_minutes=idea.estimated_length_minutes,
                thumbnail_text=idea.thumbnail_text,
                chapters=idea.chapters,
                seo_keywords=idea.seo_keywords,
            )
            for idea in parsed.ideas
        ]
        return validate_generated_ideas(ideas, sources, count)

    @staticmethod
    def _input_payload(
        sources: list[YouTubeIdeaSource], channel_focus: str, idea_count: int
    ) -> str:
        context = {
            "channel_focus": channel_focus,
            "idea_count": idea_count,
            "priority_news": [
                {
                    "article_id": source.article_id,
                    "title": source.title,
                    "summary": source.summary,
                    "source": source.source,
                    "published_at": (
                        source.published_at.isoformat()
                        if source.published_at is not None
                        else None
                    ),
                    "importance_score": source.importance_score,
                    "relevance_score": source.relevance_score,
                    "priority_score": source.priority_score,
                }
                for source in sources
            ],
        }
        return json.dumps(context, ensure_ascii=False, separators=(",", ":"))

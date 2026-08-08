"""OpenAI-backed YouTube outline and full narration providers."""

import json
import os
from typing import Any, Protocol

from openai import OpenAI
from pydantic import BaseModel, Field, StrictInt

from app.openai_summarizer import DEFAULT_OPENAI_MODEL
from app.youtube_script import (
    YouTubeNarrationSection,
    YouTubeScript,
    YouTubeScriptChapter,
    YouTubeScriptSource,
    validate_outline,
    validate_script,
    validate_target_minutes,
    validate_youtube_script_source,
)

YOUTUBE_OUTLINE_INSTRUCTIONS = """\
Create a coherent long-form explanatory/news-analysis outline using only the supplied context.
Target approximately the requested duration and allocate chapter time realistically. Start with a
strong factual hook, make chapter indexes sequential from zero, explain technical concepts clearly,
and avoid repetitive filler. Preserve supported names, organizations, dates, and numbers. Do not
invent unsupported facts, outcomes, quotes, sources, laws, or statistics. Clearly separate supplied
facts from interpretation, avoid misleading sensationalism, and return structured output only.
"""

YOUTUBE_SCRIPT_INSTRUCTIONS = """\
Write a complete generic narration script that follows every validated outline chapter. Use clear
transitions and an engaging explanatory tone. Use only supplied context, preserve supported facts
and uncertainty, and explain technical terms accessibly. Do not invent quotes, sources, statistics,
dates, numbers, companies, laws, or outcomes. Do not fabricate urgency or sensational claims and do
not repeat points merely to increase length. Include a concise final takeaway and optionally a light
neutral call to action. Target approximately the requested duration and return structured output
only. Do not write character dialogue or speaker labels.
"""


class OpenAIYouTubeScriptChapter(BaseModel):
    chapter_index: StrictInt = Field(ge=0)
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    estimated_seconds: StrictInt = Field(gt=0)
    key_points: list[str] = Field(min_length=1)


class OpenAIYouTubeOutlineResponse(BaseModel):
    chapters: list[OpenAIYouTubeScriptChapter] = Field(min_length=1)


class OpenAIYouTubeNarrationSection(BaseModel):
    chapter_index: StrictInt = Field(ge=0)
    narration: str = Field(min_length=1)


class OpenAIYouTubeScriptResponse(BaseModel):
    opening_hook: str = Field(min_length=1)
    narration_sections: list[OpenAIYouTubeNarrationSection] = Field(min_length=1)
    closing: str = Field(min_length=1)


class ResponsesParser(Protocol):
    def parse(
        self,
        *,
        model: str,
        instructions: str,
        input: str,
        text_format: type[BaseModel],
    ) -> Any: ...


class OpenAIParsingClient(Protocol):
    responses: ResponsesParser


def _source_payload(source: YouTubeScriptSource) -> dict[str, object]:
    return {
        "idea_index": source.idea_index,
        "source_article_ids": source.source_article_ids,
        "selected_title": source.selected_title,
        "selected_thumbnail_text": source.selected_thumbnail_text,
        "hook": source.hook,
        "angle": source.angle,
        "target_audience": source.target_audience,
        "estimated_length_minutes": source.estimated_length_minutes,
        "original_chapters": source.original_chapters,
        "seo_keywords": source.seo_keywords,
        "youtube_potential_score": source.youtube_potential_score,
        "packaging_score": source.packaging_score,
    }


def _parsed_output(response: Any, message: str) -> Any:
    for output_item in response.output:
        if getattr(output_item, "type", None) != "message":
            continue
        for content_item in output_item.content:
            parsed = getattr(content_item, "parsed", None)
            if parsed is not None:
                return parsed
    raise ValueError(message)


class OpenAIYouTubeOutlineGenerator:
    """Generate a validated chapter outline through typed Responses API output."""

    def __init__(
        self,
        *,
        client: OpenAIParsingClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else OpenAI(timeout=timeout)
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL

    def generate_outline(
        self,
        source: YouTubeScriptSource,
        *,
        channel_focus: str,
        target_minutes: int,
    ) -> list[YouTubeScriptChapter]:
        source = validate_youtube_script_source(source)
        if not isinstance(channel_focus, str) or not channel_focus.strip():
            raise ValueError("channel_focus must be a non-empty string")
        focus = channel_focus.strip()
        target = validate_target_minutes(target_minutes)
        response = self.client.responses.parse(
            model=self.model,
            instructions=YOUTUBE_OUTLINE_INSTRUCTIONS,
            input=json.dumps(
                {
                    "channel_focus": focus,
                    "target_minutes": target,
                    "source": _source_payload(source),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            text_format=OpenAIYouTubeOutlineResponse,
        )
        parsed = _parsed_output(response, "OpenAI response did not contain a parsed outline")
        chapters = [
            YouTubeScriptChapter(
                chapter_index=chapter.chapter_index,
                title=chapter.title,
                objective=chapter.objective,
                estimated_seconds=chapter.estimated_seconds,
                key_points=chapter.key_points,
            )
            for chapter in parsed.chapters
        ]
        return validate_outline(chapters, target)


class OpenAIYouTubeScriptGenerator:
    """Generate complete generic narration through typed Responses API output."""

    def __init__(
        self,
        *,
        client: OpenAIParsingClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else OpenAI(timeout=timeout)
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL

    def generate_script(
        self,
        source: YouTubeScriptSource,
        chapters: list[YouTubeScriptChapter],
        *,
        channel_focus: str,
        target_minutes: int,
    ) -> YouTubeScript:
        source = validate_youtube_script_source(source)
        if not isinstance(channel_focus, str) or not channel_focus.strip():
            raise ValueError("channel_focus must be a non-empty string")
        focus = channel_focus.strip()
        target = validate_target_minutes(target_minutes)
        chapters = validate_outline(chapters, target)
        response = self.client.responses.parse(
            model=self.model,
            instructions=YOUTUBE_SCRIPT_INSTRUCTIONS,
            input=json.dumps(
                {
                    "channel_focus": focus,
                    "target_minutes": target,
                    "source": _source_payload(source),
                    "chapters": [chapter.__dict__ for chapter in chapters],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            text_format=OpenAIYouTubeScriptResponse,
        )
        parsed = _parsed_output(response, "OpenAI response did not contain a parsed script")
        script = YouTubeScript(
            title=source.selected_title,
            thumbnail_text=source.selected_thumbnail_text,
            target_minutes=target,
            opening_hook=parsed.opening_hook,
            chapters=chapters,
            narration_sections=[
                YouTubeNarrationSection(
                    chapter_index=section.chapter_index,
                    narration=section.narration,
                )
                for section in parsed.narration_sections
            ],
            closing=parsed.closing,
            seo_keywords=source.seo_keywords,
        )
        return validate_script(script)

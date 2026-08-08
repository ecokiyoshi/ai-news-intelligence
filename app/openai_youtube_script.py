"""OpenAI-backed YouTube outline and full narration providers."""

import json
import math
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
only. Keep the complete script, including opening and closing, within the supplied English-word or
Japanese-character range for the language you use. Treat the supplied per-chapter word and character
targets as concrete length requirements. Do not write character dialogue or speaker labels.
"""

YOUTUBE_SCRIPT_SUPPLEMENT_INSTRUCTIONS = """\
Generate narration only for the explicitly requested missing outline chapters. Return exactly one
narration section for each requested chapter index and no other indexes. Do not regenerate or repeat
the opening hook, closing, or existing narration. Match the established tone and transitions, use
only the supplied context, and obey each missing chapter's concrete word or character target. Do not
invent unsupported facts, quotes, sources, statistics, dates, numbers, companies, laws, or outcomes.
Return structured output only without character dialogue or speaker labels.
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


class OpenAIYouTubeNarrationSupplementResponse(BaseModel):
    narration_sections: list[OpenAIYouTubeNarrationSection] = Field(min_length=1)


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


def _normalize_outline_duration(
    chapters: list[YouTubeScriptChapter], target_minutes: int
) -> list[YouTubeScriptChapter]:
    """Scale provider duration estimates to the exact requested runtime."""

    indexes = [chapter.chapter_index for chapter in chapters]
    if len(set(indexes)) != len(indexes):
        raise ValueError("outline contains a duplicate chapter_index")
    if set(indexes) != set(range(len(chapters))):
        raise ValueError("chapter indexes must be sequential from zero")
    ordered = sorted(chapters, key=lambda chapter: chapter.chapter_index)
    target_seconds = target_minutes * 60
    if len(ordered) > target_seconds:
        raise ValueError("outline contains too many chapters for the target duration")

    total = sum(chapter.estimated_seconds for chapter in ordered)
    exact = [chapter.estimated_seconds * target_seconds / total for chapter in ordered]
    seconds = [max(1, math.floor(value)) for value in exact]
    remaining = target_seconds - sum(seconds)
    if remaining > 0:
        priority = sorted(
            range(len(ordered)), key=lambda index: exact[index] - seconds[index], reverse=True
        )
        for offset in range(remaining):
            seconds[priority[offset % len(priority)]] += 1
    elif remaining < 0:
        priority = sorted(range(len(ordered)), key=lambda index: seconds[index], reverse=True)
        for _ in range(-remaining):
            index = next(index for index in priority if seconds[index] > 1)
            seconds[index] -= 1

    return [
        YouTubeScriptChapter(
            chapter_index=chapter.chapter_index,
            title=chapter.title,
            objective=chapter.objective,
            estimated_seconds=seconds[index],
            key_points=chapter.key_points,
        )
        for index, chapter in enumerate(ordered)
    ]


def _chapter_payload(chapter: YouTubeScriptChapter) -> dict[str, object]:
    return {
        **chapter.__dict__,
        "target_english_words": round(chapter.estimated_seconds * 150 / 60),
        "target_japanese_non_whitespace_characters": round(
            chapter.estimated_seconds * 280 / 60
        ),
    }


def _sections_by_index(
    sections: list[OpenAIYouTubeNarrationSection],
    *,
    allowed_indexes: set[int],
    context: str,
) -> dict[int, OpenAIYouTubeNarrationSection]:
    by_index: dict[int, OpenAIYouTubeNarrationSection] = {}
    for section in sections:
        index = section.chapter_index
        if index not in allowed_indexes:
            raise ValueError(f"{context} returned an unexpected chapter_index: {index}")
        if index in by_index:
            raise ValueError(f"{context} returned a duplicate chapter_index: {index}")
        by_index[index] = section
    return by_index


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
        return validate_outline(_normalize_outline_duration(chapters, target), target)


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
                    "required_complete_script_length": {
                        "english_words_minimum": round(
                            target * 150 * (1 - 0.20)
                        ),
                        "english_words_maximum": round(
                            target * 150 * (1 + 0.20)
                        ),
                        "japanese_non_whitespace_characters_minimum": round(
                            target * 280 * (1 - 0.20)
                        ),
                        "japanese_non_whitespace_characters_maximum": round(
                            target * 280 * (1 + 0.20)
                        ),
                    },
                    "source": _source_payload(source),
                    "chapters": [_chapter_payload(chapter) for chapter in chapters],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            text_format=OpenAIYouTubeScriptResponse,
        )
        parsed = _parsed_output(response, "OpenAI response did not contain a parsed script")
        expected_indexes = set(range(len(chapters)))
        sections = _sections_by_index(
            parsed.narration_sections,
            allowed_indexes=expected_indexes,
            context="initial script response",
        )
        missing_indexes = expected_indexes - set(sections)
        if missing_indexes:
            missing_chapters = [
                chapter for chapter in chapters if chapter.chapter_index in missing_indexes
            ]
            supplement_response = self.client.responses.parse(
                model=self.model,
                instructions=YOUTUBE_SCRIPT_SUPPLEMENT_INSTRUCTIONS,
                input=json.dumps(
                    {
                        "channel_focus": focus,
                        "target_minutes": target,
                        "source": _source_payload(source),
                        "required_missing_chapter_indexes": sorted(missing_indexes),
                        "missing_chapters": [
                            _chapter_payload(chapter) for chapter in missing_chapters
                        ],
                        "existing_narration_sections": [
                            {
                                "chapter_index": index,
                                "narration": sections[index].narration,
                            }
                            for index in sorted(sections)
                        ],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                text_format=OpenAIYouTubeNarrationSupplementResponse,
            )
            supplement = _parsed_output(
                supplement_response,
                "OpenAI response did not contain parsed narration supplements",
            )
            supplied = _sections_by_index(
                supplement.narration_sections,
                allowed_indexes=missing_indexes,
                context="narration supplement response",
            )
            still_missing = missing_indexes - set(supplied)
            if still_missing:
                raise ValueError(
                    "narration supplement is missing chapter indexes: "
                    + ", ".join(str(index) for index in sorted(still_missing))
                )
            sections.update(supplied)
        script = YouTubeScript(
            title=source.selected_title,
            thumbnail_text=source.selected_thumbnail_text,
            target_minutes=target,
            opening_hook=parsed.opening_hook,
            chapters=chapters,
            narration_sections=[
                YouTubeNarrationSection(
                    chapter_index=index,
                    narration=sections[index].narration,
                )
                for index in sorted(sections)
            ],
            closing=parsed.closing,
            seo_keywords=source.seo_keywords,
        )
        return validate_script(script)

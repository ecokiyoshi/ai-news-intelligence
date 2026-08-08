"""OpenAI-backed transformation of a completed YouTube script into dialogue."""

import json
import os
from typing import Any, Protocol

from openai import OpenAI
from pydantic import BaseModel, Field, StrictInt

from app.openai_summarizer import DEFAULT_OPENAI_MODEL
from app.youtube_dialogue import (
    DialogueChapter,
    DialogueCharacters,
    DialogueLine,
    YouTubeDialogueScript,
    YouTubeDialogueSource,
    validate_dialogue_characters,
    validate_dialogue_script,
    validate_dialogue_source,
)

YOUTUBE_DIALOGUE_INSTRUCTIONS = """\
Transform the supplied completed YouTube narration into a natural two-character explanatory
dialogue. This is a transformation task, not a research task. Use only supplied source content.
The configured explainer is calm, knowledgeable, concise, gently corrects misunderstandings, and
carries most factual exposition. The configured learner is the audience proxy: ask short useful
questions, request clarification, react naturally, and occasionally summarize understanding; do
not turn the learner into a second lecturer. Aim for roughly 60–75% explainer content as a guideline,
not a rigid quota. Avoid repetitive filler reactions and do not alternate speakers mechanically
sentence by sentence. Preserve every source chapter exactly once and in its original order, with
natural transitions. Preserve supported facts, caveats, names, dates, numbers, organizations, and
uncertainty. Do not invent facts, quotes, statistics, sources, laws, dates, outcomes, company claims,
or external research. Keep the result near the source target duration and return only the typed
structured schema.
"""


class OpenAIDialogueLine(BaseModel):
    line_index: StrictInt = Field(ge=0)
    speaker: str = Field(min_length=1)
    text: str = Field(min_length=1)


class OpenAIDialogueChapter(BaseModel):
    chapter_index: StrictInt = Field(ge=0)
    title: str = Field(min_length=1)
    lines: list[OpenAIDialogueLine] = Field(min_length=1)


class OpenAIYouTubeDialogueResponse(BaseModel):
    opening_lines: list[OpenAIDialogueLine] = Field(min_length=1)
    chapters: list[OpenAIDialogueChapter] = Field(min_length=1)
    closing_lines: list[OpenAIDialogueLine] = Field(min_length=1)


class ResponsesParser(Protocol):
    def parse(
        self,
        *,
        model: str,
        instructions: str,
        input: str,
        text_format: type[OpenAIYouTubeDialogueResponse],
    ) -> Any: ...


class OpenAIParsingClient(Protocol):
    responses: ResponsesParser


def _parsed_output(response: Any) -> OpenAIYouTubeDialogueResponse:
    for output_item in response.output:
        if getattr(output_item, "type", None) != "message":
            continue
        for content_item in output_item.content:
            parsed = getattr(content_item, "parsed", None)
            if parsed is not None:
                return parsed
    raise ValueError("OpenAI response did not contain parsed dialogue")


class OpenAIYouTubeDialogueConverter:
    """Convert supplied narration using typed OpenAI Responses API output."""

    def __init__(
        self,
        *,
        client: OpenAIParsingClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else OpenAI(timeout=timeout)
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL

    def convert(
        self,
        source: YouTubeDialogueSource,
        *,
        channel_focus: str,
        characters: DialogueCharacters,
    ) -> YouTubeDialogueScript:
        source = validate_dialogue_source(source)
        if not isinstance(channel_focus, str) or not channel_focus.strip():
            raise ValueError("channel_focus must be a non-empty string")
        focus = channel_focus.strip()
        characters = validate_dialogue_characters(characters)
        narration_by_index = {
            section.chapter_index: section.narration
            for section in source.narration_sections
        }
        response = self.client.responses.parse(
            model=self.model,
            instructions=YOUTUBE_DIALOGUE_INSTRUCTIONS,
            input=json.dumps(
                {
                    "channel_focus": focus,
                    "characters": {
                        "explainer": {
                            "name": characters.explainer_name,
                            "role": "primary calm, knowledgeable factual explainer",
                        },
                        "learner": {
                            "name": characters.learner_name,
                            "role": "audience proxy asking short useful questions",
                        },
                    },
                    "source": {
                        "title": source.title,
                        "thumbnail_text": source.thumbnail_text,
                        "target_minutes": source.target_minutes,
                        "opening_hook": source.opening_hook,
                        "chapters": [
                            {
                                "chapter_index": chapter.chapter_index,
                                "title": chapter.title,
                                "objective": chapter.objective,
                                "estimated_seconds": chapter.estimated_seconds,
                                "key_points": chapter.key_points,
                                "narration": narration_by_index[chapter.chapter_index],
                            }
                            for chapter in source.chapters
                        ],
                        "closing": source.closing,
                        "seo_keywords": source.seo_keywords,
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            text_format=OpenAIYouTubeDialogueResponse,
        )
        parsed = _parsed_output(response)
        script = YouTubeDialogueScript(
            title=source.title,
            thumbnail_text=source.thumbnail_text,
            target_minutes=source.target_minutes,
            opening_lines=[
                DialogueLine(line.line_index, line.speaker, line.text)
                for line in parsed.opening_lines
            ],
            chapters=[
                DialogueChapter(
                    chapter_index=chapter.chapter_index,
                    title=chapter.title,
                    lines=[
                        DialogueLine(line.line_index, line.speaker, line.text)
                        for line in chapter.lines
                    ],
                )
                for chapter in parsed.chapters
            ],
            closing_lines=[
                DialogueLine(line.line_index, line.speaker, line.text)
                for line in parsed.closing_lines
            ],
            seo_keywords=source.seo_keywords,
        )
        return validate_dialogue_script(script, source, characters)

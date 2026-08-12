"""OpenAI-backed transformation of a completed YouTube script into dialogue."""

import json
import os
from typing import Any, Protocol

from openai import OpenAI
from pydantic import BaseModel, Field, StrictInt

from app.openai_summarizer import DEFAULT_OPENAI_MODEL
from app.youtube_dialogue import (
    DEFAULT_DIALOGUE_DURATION_TOLERANCE,
    DialogueChapter,
    DialogueCharacters,
    DialogueLine,
    YouTubeDialogueScript,
    YouTubeDialogueSource,
    dialogue_text,
    japanese_dialogue_target_minutes,
    validate_dialogue_characters,
    validate_dialogue_script,
    validate_dialogue_structure,
    validate_dialogue_source,
)
from app.youtube_script import (
    ENGLISH_WORDS_PER_MINUTE,
    JAPANESE_CHARACTERS_PER_MINUTE,
    estimate_script_minutes,
)

YOUTUBE_DIALOGUE_INSTRUCTIONS = """\
Rewrite the supplied English source as a clear 7-10 minute Japanese YouTube conversation. This is
not sentence-by-sentence translation or research: understand the facts, extract what matters,
remove repetition, reorganize it, and write the final spoken script using only supplied facts.

Do not preserve the source chapter count. The English source may contain 9, 10, 15, 20, or more
chapters; even then, the Japanese version must not mechanically use the same number. Do not map
source chapters to Japanese chapters one-to-one. Merge, split, reorder, or remove chapters when
needed for clarity, natural flow, no repeated explanation, forward momentum, and runtime. Return
newly organized Japanese chapters indexed sequentially from zero.

Write every spoken dialogue line in natural Japanese. Do not use formal Japanese as the default
dialogue style. Sabisu and Haru should speak like close friends. Use casual, friendly Japanese that
sounds natural aloud. Avoid stiff translated expressions,
formal news-anchor or lecture language, and routine desu/masu phrasing. Haru asks short casual
questions from the viewer's perspective, reacts, then naturally raises the next question. Sabisu
answers briefly like a knowledgeable friend rather than a lecturer. Keep technical terminology when
necessary, but explain unfamiliar terms briefly in casual Japanese. Polite phrasing is allowed only
where natural, such as an opening greeting or direct viewer address. Do not sound childish.

Preserve supported facts, caveats, names, dates, numbers, organizations, and uncertainty. Do not
invent facts, quotes, statistics, sources, laws, dates, outcomes, company claims, or external
research. Return only the typed structured schema.
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


class OpenAIDialogueChapterSupplement(BaseModel):
    chapter_index: StrictInt = Field(ge=0)
    lines: list[OpenAIDialogueLine] = Field(min_length=1)


class OpenAIYouTubeDialogueSupplementResponse(BaseModel):
    chapters: list[OpenAIDialogueChapterSupplement] = Field(min_length=1)


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


def _parsed_output(response: Any) -> BaseModel:
    for output_item in response.output:
        if getattr(output_item, "type", None) != "message":
            continue
        for content_item in output_item.content:
            parsed = getattr(content_item, "parsed", None)
            if parsed is not None:
                return parsed
    raise ValueError("OpenAI response did not contain parsed dialogue")


def _duration_units(text: str, japanese: bool) -> int:
    return len("".join(text.split())) if japanese else len(text.split())


def _duration_targets(source: YouTubeDialogueSource) -> tuple[bool, int, int, int]:
    # This converter always emits Japanese, even when the supplied narration is English.
    # Duration targets must therefore use Japanese characters rather than source-language words.
    japanese = True
    rate = JAPANESE_CHARACTERS_PER_MINUTE if japanese else ENGLISH_WORDS_PER_MINUTE
    target = round(japanese_dialogue_target_minutes(source.target_minutes) * rate)
    minimum = round(target * (1 - DEFAULT_DIALOGUE_DURATION_TOLERANCE))
    maximum = round(target * (1 + DEFAULT_DIALOGUE_DURATION_TOLERANCE))
    return japanese, target, minimum, maximum


def _to_dialogue_script(
    parsed: OpenAIYouTubeDialogueResponse,
    source: YouTubeDialogueSource,
    target_minutes: int | None = None,
) -> YouTubeDialogueScript:
    return YouTubeDialogueScript(
        title=source.title,
        thumbnail_text=source.thumbnail_text,
        target_minutes=source.target_minutes if target_minutes is None else target_minutes,
        opening_lines=[DialogueLine(line.line_index, line.speaker, line.text) for line in parsed.opening_lines],
        chapters=[DialogueChapter(
            chapter.chapter_index, chapter.title,
            [DialogueLine(line.line_index, line.speaker, line.text) for line in chapter.lines],
        ) for chapter in parsed.chapters],
        closing_lines=[DialogueLine(line.line_index, line.speaker, line.text) for line in parsed.closing_lines],
        seo_keywords=source.seo_keywords,
    )


class OpenAIYouTubeDialogueConverter:
    """Convert supplied narration using typed OpenAI Responses API output."""

    output_language = "ja"

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
        japanese, target_units, minimum_units, maximum_units = _duration_targets(source)
        target_minutes = japanese_dialogue_target_minutes(source.target_minutes)
        unit_name = "Japanese non-whitespace characters" if japanese else "English words"
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
                        "source_target_minutes": source.target_minutes,
                        "japanese_target_minutes": target_minutes,
                        "length_requirements": {
                            "unit": unit_name,
                            "whole_script_target": target_units,
                            "whole_script_minimum": minimum_units,
                            "whole_script_maximum": maximum_units,
                        },
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
        if not isinstance(parsed, OpenAIYouTubeDialogueResponse):
            raise ValueError("OpenAI response did not contain parsed dialogue")
        script = _to_dialogue_script(parsed, source, target_minutes)
        script = validate_dialogue_structure(script, source, characters)
        estimated_minutes = estimate_script_minutes(dialogue_text(script))
        minimum_minutes = script.target_minutes * (1 - DEFAULT_DIALOGUE_DURATION_TOLERANCE)
        maximum_minutes = script.target_minutes * (1 + DEFAULT_DIALOGUE_DURATION_TOLERANCE)
        if estimated_minutes > maximum_minutes:
            script = self._shorten_overlong_dialogue(
                script, source=source, characters=characters, maximum_units=maximum_units
            )
            estimated_minutes = estimate_script_minutes(dialogue_text(script))
            if estimated_minutes > maximum_minutes:
                raise ValueError("estimated dialogue runtime exceeds the configured maximum after one shortening call")
        if estimated_minutes < minimum_minutes:
            script = self._supplement_short_dialogue(
                script,
                source=source,
                characters=characters,
                japanese=japanese,
                target_units=target_units,
            )
            estimated_minutes = estimate_script_minutes(dialogue_text(script))
            if estimated_minutes < minimum_minutes:
                remaining = max(
                    0,
                    target_units - _duration_units(dialogue_text(script), japanese),
                )
                raise ValueError(
                    "dialogue remains too short after one supplement call; "
                    f"remaining {unit_name}: {remaining}"
                )
            if estimated_minutes > maximum_minutes:
                raise ValueError(
                    "estimated dialogue runtime exceeds the configured maximum "
                    "after one supplement call"
                )
        return validate_dialogue_script(script, source, characters)

    def _shorten_overlong_dialogue(
        self, script: YouTubeDialogueScript, *, source: YouTubeDialogueSource,
        characters: DialogueCharacters, maximum_units: int,
    ) -> YouTubeDialogueScript:
        response = self.client.responses.parse(
            model=self.model,
            instructions=(
                "Rewrite the complete supplied dialogue in concise natural Japanese. Preserve all metadata, "
                "chapter order, speakers, line indexes, supported facts, numbers, names, and caveats. "
                f"The total spoken dialogue must not exceed {maximum_units} Japanese non-whitespace characters."
            ),
            input=json.dumps({"dialogue": script.__dict__}, ensure_ascii=False, default=lambda value: value.__dict__),
            text_format=OpenAIYouTubeDialogueResponse,
        )
        parsed = _parsed_output(response)
        if not isinstance(parsed, OpenAIYouTubeDialogueResponse):
            raise ValueError("OpenAI response did not contain shortened parsed dialogue")
        shortened = _to_dialogue_script(parsed, source, script.target_minutes)
        return validate_dialogue_structure(shortened, source, characters)

    def _supplement_short_dialogue(
        self,
        script: YouTubeDialogueScript,
        *,
        source: YouTubeDialogueSource,
        characters: DialogueCharacters,
        japanese: bool,
        target_units: int,
    ) -> YouTubeDialogueScript:
        unit_name = "Japanese non-whitespace characters" if japanese else "English words"
        current_units = _duration_units(dialogue_text(script), japanese)
        missing_units = max(1, target_units - current_units)
        rate = JAPANESE_CHARACTERS_PER_MINUTE if japanese else ENGLISH_WORDS_PER_MINUTE
        deficits: list[tuple[DialogueChapter, int]] = []
        for chapter in script.chapters:
            expected = round(target_units / len(script.chapters))
            actual = _duration_units(" ".join(line.text for line in chapter.lines), japanese)
            if actual < expected:
                deficits.append((chapter, expected - actual))
        if not deficits:
            deficits = [(max(script.chapters, key=lambda chapter: len(chapter.lines)), 1)]
        total_deficit = sum(deficit for _, deficit in deficits)
        requests = []
        allocated = 0
        for position, (chapter, deficit) in enumerate(deficits):
            amount = (
                max(1, missing_units - allocated)
                if position == len(deficits) - 1
                else max(1, round(missing_units * deficit / total_deficit))
            )
            allocated += amount
            requests.append({
                "chapter_index": chapter.chapter_index,
                "title": chapter.title,
                "additional_target": amount,
                "next_line_index": len(chapter.lines),
                "existing_lines": [line.__dict__ for line in chapter.lines],
            })
        response = self.client.responses.parse(
            model=self.model,
            instructions=(
                "Add only the requested new dialogue lines to the listed deficient chapters. "
                "Write every added spoken line in natural Japanese. "
                "Do not repeat or rewrite existing lines. Use only the supplied source facts. "
                "Start each chapter at its next_line_index and use consecutive indexes. "
                "Return every requested chapter exactly once and no other chapters."
            ),
            input=json.dumps({
                "characters": [characters.explainer_name, characters.learner_name],
                "unit": unit_name,
                "missing_total": missing_units,
                "requests": requests,
                "source_chapters": [
                    {
                        "chapter_index": chapter.chapter_index,
                        "key_points": chapter.key_points,
                        "narration": next(
                            section.narration for section in source.narration_sections
                            if section.chapter_index == chapter.chapter_index
                        ),
                    }
                    for chapter in source.chapters
                ],
            }, ensure_ascii=False, separators=(",", ":")),
            text_format=OpenAIYouTubeDialogueSupplementResponse,
        )
        parsed = _parsed_output(response)
        if not isinstance(parsed, OpenAIYouTubeDialogueSupplementResponse):
            raise ValueError("OpenAI response did not contain parsed dialogue supplement")
        requested_indexes = [chapter.chapter_index for chapter, _ in deficits]
        returned_indexes = [chapter.chapter_index for chapter in parsed.chapters]
        if returned_indexes != requested_indexes:
            raise ValueError("dialogue supplement must preserve exact requested chapter order and coverage")
        supplements = {chapter.chapter_index: chapter.lines for chapter in parsed.chapters}
        allowed_speakers = {characters.explainer_name, characters.learner_name}
        chapters = []
        for chapter in script.chapters:
            additions = supplements.get(chapter.chapter_index, [])
            expected_indexes = list(range(len(chapter.lines), len(chapter.lines) + len(additions)))
            if [line.line_index for line in additions] != expected_indexes:
                raise ValueError("dialogue supplement line indexes must continue sequentially")
            if any(line.speaker not in allowed_speakers for line in additions):
                raise ValueError("dialogue supplement speaker is not configured")
            chapters.append(DialogueChapter(
                chapter.chapter_index,
                chapter.title,
                [*chapter.lines, *(DialogueLine(line.line_index, line.speaker, line.text) for line in additions)],
            ))
        return validate_dialogue_structure(
            YouTubeDialogueScript(
                script.title, script.thumbnail_text, script.target_minutes,
                script.opening_lines, chapters, script.closing_lines, script.seo_keywords,
            ), source, characters,
        )

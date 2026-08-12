"""Claude-backed transformation of a completed YouTube script into dialogue."""

import json

from pydantic import BaseModel, Field, StrictInt

from app.anthropic_client import AnthropicClient, build_default_client, parse_structured, resolve_model
from app.youtube_dialogue import (
    DEFAULT_DIALOGUE_DURATION_TOLERANCE,
    DialogueChapter,
    DialogueCharacters,
    DialogueLine,
    YouTubeDialogueScript,
    YouTubeDialogueSource,
    dialogue_text,
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
Transform the supplied completed YouTube narration into a natural two-character explanatory
dialogue. This is a transformation task, not a research task. Use only supplied source content.
Write every spoken dialogue line in natural Japanese, translating supplied English narration while
preserving its meaning. Keep proper nouns and technical terms when needed, but do not produce
English sentences. Metadata fields that must match the source may remain in the source language.
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


class AnthropicDialogueLine(BaseModel):
    line_index: StrictInt = Field(ge=0)
    speaker: str = Field(min_length=1)
    text: str = Field(min_length=1)


class AnthropicDialogueChapter(BaseModel):
    chapter_index: StrictInt = Field(ge=0)
    title: str = Field(min_length=1)
    lines: list[AnthropicDialogueLine] = Field(min_length=1)


class AnthropicYouTubeDialogueResponse(BaseModel):
    opening_lines: list[AnthropicDialogueLine] = Field(min_length=1)
    chapters: list[AnthropicDialogueChapter] = Field(min_length=1)
    closing_lines: list[AnthropicDialogueLine] = Field(min_length=1)


class AnthropicDialogueChapterSupplement(BaseModel):
    chapter_index: StrictInt = Field(ge=0)
    lines: list[AnthropicDialogueLine] = Field(min_length=1)


class AnthropicYouTubeDialogueSupplementResponse(BaseModel):
    chapters: list[AnthropicDialogueChapterSupplement] = Field(min_length=1)


def _duration_units(text: str, japanese: bool) -> int:
    return len("".join(text.split())) if japanese else len(text.split())


def _duration_targets(source: YouTubeDialogueSource) -> tuple[bool, int, int, int]:
    # This converter always emits Japanese, even when the supplied narration is English.
    # Duration targets must therefore use Japanese characters rather than source-language words.
    japanese = True
    rate = JAPANESE_CHARACTERS_PER_MINUTE if japanese else ENGLISH_WORDS_PER_MINUTE
    target = round(source.target_minutes * rate)
    minimum = round(target * (1 - DEFAULT_DIALOGUE_DURATION_TOLERANCE))
    maximum = round(target * (1 + DEFAULT_DIALOGUE_DURATION_TOLERANCE))
    return japanese, target, minimum, maximum


class AnthropicYouTubeDialogueConverter:
    """Convert supplied narration using forced structured Claude tool calls."""

    output_language = "ja"

    def __init__(
        self,
        *,
        client: AnthropicClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else build_default_client(timeout)
        self.model = resolve_model(model)

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
        unit_name = "Japanese non-whitespace characters" if japanese else "English words"
        parsed = parse_structured(
            self.client,
            model=self.model,
            system=YOUTUBE_DIALOGUE_INSTRUCTIONS,
            input_text=json.dumps(
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
                        "length_requirements": {
                            "unit": unit_name,
                            "whole_script_target": target_units,
                            "whole_script_minimum": minimum_units,
                            "whole_script_maximum": maximum_units,
                            "chapter_targets": [
                                {
                                    "chapter_index": chapter.chapter_index,
                                    "target": round(
                                        chapter.estimated_seconds
                                        * (JAPANESE_CHARACTERS_PER_MINUTE if japanese else ENGLISH_WORDS_PER_MINUTE)
                                        / 60
                                    ),
                                }
                                for chapter in source.chapters
                            ],
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
            response_model=AnthropicYouTubeDialogueResponse,
        )
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
        script = validate_dialogue_structure(script, source, characters)
        estimated_minutes = estimate_script_minutes(dialogue_text(script))
        minimum_minutes = source.target_minutes * (1 - DEFAULT_DIALOGUE_DURATION_TOLERANCE)
        maximum_minutes = source.target_minutes * (1 + DEFAULT_DIALOGUE_DURATION_TOLERANCE)
        if estimated_minutes > maximum_minutes:
            raise ValueError("estimated dialogue runtime exceeds the configured maximum")
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
        source_by_index = {chapter.chapter_index: chapter for chapter in source.chapters}
        for chapter in script.chapters:
            expected = round(source_by_index[chapter.chapter_index].estimated_seconds * rate / 60)
            actual = _duration_units(" ".join(line.text for line in chapter.lines), japanese)
            if actual < expected:
                deficits.append((chapter, expected - actual))
        if not deficits:
            deficits = [(max(script.chapters, key=lambda chapter: source_by_index[chapter.chapter_index].estimated_seconds), 1)]
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
        parsed = parse_structured(
            self.client,
            model=self.model,
            system=(
                "Add only the requested new dialogue lines to the listed deficient chapters. "
                "Write every added spoken line in natural Japanese. "
                "Do not repeat or rewrite existing lines. Use only the supplied source facts. "
                "Start each chapter at its next_line_index and use consecutive indexes. "
                "Return every requested chapter exactly once and no other chapters."
            ),
            input_text=json.dumps({
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
                    for chapter in source.chapters if chapter.chapter_index in {item[0].chapter_index for item in deficits}
                ],
            }, ensure_ascii=False, separators=(",", ":")),
            response_model=AnthropicYouTubeDialogueSupplementResponse,
        )
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

"""Provider-independent conversion of a validated YouTube script into dialogue.

Dialogue runtime uses the existing text heuristic and remains approximate only.
"""

import math
from dataclasses import dataclass
from typing import Protocol

from app.youtube_script import (
    YouTubeNarrationSection,
    YouTubeScript,
    YouTubeScriptChapter,
    estimate_script_minutes,
    uses_japanese_duration_heuristic,
    validate_script,
    validate_target_minutes,
)

DEFAULT_DIALOGUE_DURATION_TOLERANCE = 0.25


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _nonnegative_integer(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _ratio(name: str, value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value < 1
    ):
        raise ValueError(f"{name} must be a finite number between 0 and 1")
    return float(value)


def _text_list(name: str, values: list[str]) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{name} must be a non-empty list")
    return [_required_text(f"{name} item", value) for value in values]


@dataclass(frozen=True)
class DialogueCharacters:
    explainer_name: str = "さび助"
    learner_name: str = "ハル"

    def __post_init__(self) -> None:
        explainer = _required_text("explainer_name", self.explainer_name)
        learner = _required_text("learner_name", self.learner_name)
        if explainer == learner:
            raise ValueError("explainer_name and learner_name must differ")
        object.__setattr__(self, "explainer_name", explainer)
        object.__setattr__(self, "learner_name", learner)


DEFAULT_DIALOGUE_CHARACTERS = DialogueCharacters()


@dataclass(frozen=True)
class YouTubeDialogueSource:
    title: str
    thumbnail_text: str
    target_minutes: int
    opening_hook: str
    chapters: list[YouTubeScriptChapter]
    narration_sections: list[YouTubeNarrationSection]
    closing: str
    seo_keywords: list[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _required_text("title", self.title))
        object.__setattr__(
            self, "thumbnail_text", _required_text("thumbnail_text", self.thumbnail_text)
        )
        object.__setattr__(self, "target_minutes", validate_target_minutes(self.target_minutes))
        object.__setattr__(
            self, "opening_hook", _required_text("opening_hook", self.opening_hook)
        )
        if not isinstance(self.chapters, list) or not self.chapters:
            raise ValueError("chapters must be a non-empty list")
        if not isinstance(self.narration_sections, list) or not self.narration_sections:
            raise ValueError("narration_sections must be a non-empty list")
        object.__setattr__(self, "closing", _required_text("closing", self.closing))
        object.__setattr__(self, "seo_keywords", _text_list("seo_keywords", self.seo_keywords))


@dataclass(frozen=True)
class DialogueLine:
    line_index: int
    speaker: str
    text: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "line_index", _nonnegative_integer("line_index", self.line_index)
        )
        object.__setattr__(self, "speaker", _required_text("speaker", self.speaker))
        object.__setattr__(self, "text", _required_text("text", self.text))


@dataclass(frozen=True)
class DialogueChapter:
    chapter_index: int
    title: str
    lines: list[DialogueLine]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "chapter_index",
            _nonnegative_integer("chapter_index", self.chapter_index),
        )
        object.__setattr__(self, "title", _required_text("title", self.title))
        if not isinstance(self.lines, list) or not self.lines:
            raise ValueError("lines must be a non-empty list")


@dataclass(frozen=True)
class YouTubeDialogueScript:
    title: str
    thumbnail_text: str
    target_minutes: int
    opening_lines: list[DialogueLine]
    chapters: list[DialogueChapter]
    closing_lines: list[DialogueLine]
    seo_keywords: list[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _required_text("title", self.title))
        object.__setattr__(
            self, "thumbnail_text", _required_text("thumbnail_text", self.thumbnail_text)
        )
        object.__setattr__(self, "target_minutes", validate_target_minutes(self.target_minutes))
        if not isinstance(self.opening_lines, list) or not self.opening_lines:
            raise ValueError("opening_lines must be a non-empty list")
        if not isinstance(self.chapters, list) or not self.chapters:
            raise ValueError("chapters must be a non-empty list")
        if not isinstance(self.closing_lines, list) or not self.closing_lines:
            raise ValueError("closing_lines must be a non-empty list")
        object.__setattr__(self, "seo_keywords", _text_list("seo_keywords", self.seo_keywords))


class YouTubeDialogueConverter(Protocol):
    def convert(
        self,
        source: YouTubeDialogueSource,
        *,
        channel_focus: str,
        characters: DialogueCharacters,
    ) -> YouTubeDialogueScript: ...


def validate_dialogue_characters(characters: DialogueCharacters) -> DialogueCharacters:
    if not isinstance(characters, DialogueCharacters):
        raise ValueError("characters must be DialogueCharacters")
    return DialogueCharacters(**characters.__dict__)


def validate_dialogue_source(source: YouTubeDialogueSource) -> YouTubeDialogueSource:
    if not isinstance(source, YouTubeDialogueSource):
        raise ValueError("source must be YouTubeDialogueSource")
    source = YouTubeDialogueSource(**source.__dict__)
    script = validate_script(
        YouTubeScript(
            title=source.title,
            thumbnail_text=source.thumbnail_text,
            target_minutes=source.target_minutes,
            opening_hook=source.opening_hook,
            chapters=source.chapters,
            narration_sections=source.narration_sections,
            closing=source.closing,
            seo_keywords=source.seo_keywords,
        )
    )
    return YouTubeDialogueSource(
        title=script.title,
        thumbnail_text=script.thumbnail_text,
        target_minutes=script.target_minutes,
        opening_hook=script.opening_hook,
        chapters=script.chapters,
        narration_sections=script.narration_sections,
        closing=script.closing,
        seo_keywords=script.seo_keywords,
    )


def build_youtube_dialogue_source(script: YouTubeScript) -> YouTubeDialogueSource:
    script = validate_script(script)
    return YouTubeDialogueSource(
        title=script.title,
        thumbnail_text=script.thumbnail_text,
        target_minutes=script.target_minutes,
        opening_hook=script.opening_hook,
        chapters=script.chapters,
        narration_sections=script.narration_sections,
        closing=script.closing,
        seo_keywords=script.seo_keywords,
    )


def validate_dialogue_line(
    line: DialogueLine, characters: DialogueCharacters
) -> DialogueLine:
    characters = validate_dialogue_characters(characters)
    if not isinstance(line, DialogueLine):
        raise ValueError("dialogue must contain DialogueLine values")
    line = DialogueLine(**line.__dict__)
    if line.speaker not in {characters.explainer_name, characters.learner_name}:
        raise ValueError("dialogue speaker is not one of the configured characters")
    return line


def _validate_lines(
    name: str, lines: list[DialogueLine], characters: DialogueCharacters
) -> list[DialogueLine]:
    if not isinstance(lines, list) or not lines:
        raise ValueError(f"{name} must be a non-empty list")
    validated = [validate_dialogue_line(line, characters) for line in lines]
    if [line.line_index for line in validated] != list(range(len(validated))):
        raise ValueError(f"{name} line indexes must be sequential from zero")
    return validated


def validate_dialogue_chapter(
    chapter: DialogueChapter, characters: DialogueCharacters
) -> DialogueChapter:
    if not isinstance(chapter, DialogueChapter):
        raise ValueError("chapters must contain DialogueChapter values")
    chapter = DialogueChapter(**chapter.__dict__)
    return DialogueChapter(
        chapter_index=chapter.chapter_index,
        title=chapter.title,
        lines=_validate_lines("chapter lines", chapter.lines, characters),
    )


def dialogue_text(script: YouTubeDialogueScript) -> str:
    """Join all spoken text in playback order for approximate runtime estimation."""

    return " ".join(
        [
            *(line.text for line in script.opening_lines),
            *(line.text for chapter in script.chapters for line in chapter.lines),
            *(line.text for line in script.closing_lines),
        ]
    )


def validate_dialogue_script(
    script: YouTubeDialogueScript,
    source: YouTubeDialogueSource,
    characters: DialogueCharacters = DEFAULT_DIALOGUE_CHARACTERS,
    duration_tolerance_ratio: float = DEFAULT_DIALOGUE_DURATION_TOLERANCE,
) -> YouTubeDialogueScript:
    validated = validate_dialogue_structure(script, source, characters)
    tolerance = _ratio("duration_tolerance_ratio", duration_tolerance_ratio)
    estimated_minutes = estimate_script_minutes(dialogue_text(validated))
    if not math.isclose(estimated_minutes, source.target_minutes, rel_tol=tolerance):
        raise ValueError("estimated dialogue runtime is outside the configured tolerance")
    return validated


def validate_dialogue_structure(
    script: YouTubeDialogueScript,
    source: YouTubeDialogueSource,
    characters: DialogueCharacters = DEFAULT_DIALOGUE_CHARACTERS,
) -> YouTubeDialogueScript:
    """Validate dialogue metadata and structure without enforcing its runtime."""

    source = validate_dialogue_source(source)
    characters = validate_dialogue_characters(characters)
    if not isinstance(script, YouTubeDialogueScript):
        raise ValueError("converter must return YouTubeDialogueScript")
    script = YouTubeDialogueScript(**script.__dict__)
    if script.title != source.title or script.thumbnail_text != source.thumbnail_text:
        raise ValueError("dialogue must preserve selected title and thumbnail text")
    if script.target_minutes != source.target_minutes:
        raise ValueError("dialogue must preserve target_minutes")
    if script.seo_keywords != source.seo_keywords:
        raise ValueError("dialogue must preserve seo_keywords")
    opening = _validate_lines("opening_lines", script.opening_lines, characters)
    closing = _validate_lines("closing_lines", script.closing_lines, characters)
    chapters = [validate_dialogue_chapter(chapter, characters) for chapter in script.chapters]
    source_indexes = [chapter.chapter_index for chapter in source.chapters]
    if [chapter.chapter_index for chapter in chapters] != source_indexes:
        raise ValueError("dialogue chapters must preserve exact source order and coverage")
    for dialogue_chapter, source_chapter in zip(chapters, source.chapters, strict=True):
        if dialogue_chapter.title != source_chapter.title:
            raise ValueError("dialogue chapter title must match the source chapter")
    speakers = {
        line.speaker
        for line in [
            *opening,
            *(line for chapter in chapters for line in chapter.lines),
            *closing,
        ]
    }
    required_speakers = {characters.explainer_name, characters.learner_name}
    if speakers != required_speakers:
        raise ValueError("dialogue must contain both configured characters")
    validated = YouTubeDialogueScript(
        title=script.title,
        thumbnail_text=script.thumbnail_text,
        target_minutes=script.target_minutes,
        opening_lines=opening,
        chapters=chapters,
        closing_lines=closing,
        seo_keywords=script.seo_keywords,
    )
    return validated


class LocalYouTubeDialogueConverter:
    """Deterministic offline conversion that preserves source narration verbatim."""

    def convert(
        self,
        source: YouTubeDialogueSource,
        *,
        channel_focus: str,
        characters: DialogueCharacters,
    ) -> YouTubeDialogueScript:
        source = validate_dialogue_source(source)
        focus = _required_text("channel_focus", channel_focus)
        characters = validate_dialogue_characters(characters)
        narration_by_index = {
            section.chapter_index: section.narration
            for section in source.narration_sections
        }
        japanese = uses_japanese_duration_heuristic(
            " ".join([source.title, source.opening_hook, source.closing])
        )
        chapters = []
        for chapter in source.chapters:
            chapters.append(
                DialogueChapter(
                    chapter_index=chapter.chapter_index,
                    title=chapter.title,
                    lines=[
                        DialogueLine(
                            0,
                            characters.learner_name,
                            (
                                f"{chapter.title}はどう理解すればいいの？"
                                if japanese
                                else f"How does {chapter.title} connect to {focus}?"
                            ),
                        ),
                        DialogueLine(
                            1,
                            characters.explainer_name,
                            narration_by_index[chapter.chapter_index],
                        ),
                        DialogueLine(
                            2,
                            characters.learner_name,
                            (
                                "この章の要点が整理できたよ。"
                                if japanese
                                else f"So the objective here is to {chapter.objective}."
                            ),
                        ),
                    ],
                )
            )
        return YouTubeDialogueScript(
            title=source.title,
            thumbnail_text=source.thumbnail_text,
            target_minutes=source.target_minutes,
            opening_lines=[
                DialogueLine(
                    0,
                    characters.learner_name,
                    (
                        f"{source.title}について教えて。"
                        if japanese
                        else f"What should we know about {source.title}?"
                    ),
                ),
                DialogueLine(1, characters.explainer_name, source.opening_hook),
            ],
            chapters=chapters,
            closing_lines=[
                DialogueLine(0, characters.explainer_name, source.closing),
                DialogueLine(
                    1,
                    characters.learner_name,
                    (
                        "今回のポイントが整理できたよ。"
                        if japanese
                        else "That puts the main takeaway in context."
                    ),
                ),
            ],
            seo_keywords=source.seo_keywords,
        )


def convert_youtube_script_to_dialogue(
    source_script: YouTubeScript,
    converter: YouTubeDialogueConverter,
    *,
    channel_focus: str,
    characters: DialogueCharacters = DEFAULT_DIALOGUE_CHARACTERS,
    duration_tolerance_ratio: float = DEFAULT_DIALOGUE_DURATION_TOLERANCE,
) -> YouTubeDialogueScript:
    """Convert a complete script while preserving chapters and approximate duration."""

    source = build_youtube_dialogue_source(source_script)
    focus = _required_text("channel_focus", channel_focus)
    characters = validate_dialogue_characters(characters)
    tolerance = _ratio("duration_tolerance_ratio", duration_tolerance_ratio)
    result = converter.convert(source, channel_focus=focus, characters=characters)
    return validate_dialogue_script(result, source, characters, tolerance)

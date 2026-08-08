"""Provider-independent long-form YouTube outline and script generation.

Runtime estimates are heuristics only; they do not guarantee actual playback duration.
"""

import math
import re
from dataclasses import dataclass
from typing import Protocol

from app.youtube_ideas import YouTubeIdea, validate_youtube_idea
from app.youtube_packaging import YouTubePackagingCandidate
from app.youtube_potential import RankedYouTubeIdea, YouTubePotentialResult

MIN_TARGET_MINUTES = 5
MAX_TARGET_MINUTES = 30
DEFAULT_TARGET_MINUTES = 15
DEFAULT_OUTLINE_DURATION_TOLERANCE = 0.10
DEFAULT_SCRIPT_LENGTH_TOLERANCE = 0.20
JAPANESE_CHARACTERS_PER_MINUTE = 280
ENGLISH_WORDS_PER_MINUTE = 150


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _nonnegative_integer(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _positive_integer(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _score(name: str, value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 100
    ):
        raise ValueError(f"{name} must be a finite number between 0 and 100")
    return float(value)


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


def validate_target_minutes(target_minutes: int) -> int:
    target = _positive_integer("target_minutes", target_minutes)
    if not MIN_TARGET_MINUTES <= target <= MAX_TARGET_MINUTES:
        raise ValueError(
            f"target_minutes must be between {MIN_TARGET_MINUTES} and {MAX_TARGET_MINUTES}"
        )
    return target


@dataclass(frozen=True)
class YouTubeScriptSource:
    idea_index: int
    source_article_ids: list[int]
    selected_title: str
    selected_thumbnail_text: str
    hook: str
    angle: str
    target_audience: str
    estimated_length_minutes: int
    original_chapters: list[str]
    seo_keywords: list[str]
    youtube_potential_score: float
    packaging_score: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "idea_index", _nonnegative_integer("idea_index", self.idea_index)
        )
        if not isinstance(self.source_article_ids, list) or not self.source_article_ids:
            raise ValueError("source_article_ids must be a non-empty list")
        article_ids = [
            _positive_integer("source article ID", article_id)
            for article_id in self.source_article_ids
        ]
        if len(set(article_ids)) != len(article_ids):
            raise ValueError("source_article_ids must not contain duplicates")
        object.__setattr__(self, "source_article_ids", article_ids)
        for name in (
            "selected_title",
            "selected_thumbnail_text",
            "hook",
            "angle",
            "target_audience",
        ):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
        object.__setattr__(
            self,
            "estimated_length_minutes",
            _positive_integer("estimated_length_minutes", self.estimated_length_minutes),
        )
        object.__setattr__(
            self, "original_chapters", _text_list("original_chapters", self.original_chapters)
        )
        object.__setattr__(self, "seo_keywords", _text_list("seo_keywords", self.seo_keywords))
        object.__setattr__(
            self,
            "youtube_potential_score",
            _score("youtube_potential_score", self.youtube_potential_score),
        )
        object.__setattr__(
            self, "packaging_score", _score("packaging_score", self.packaging_score)
        )


@dataclass(frozen=True)
class YouTubeScriptChapter:
    chapter_index: int
    title: str
    objective: str
    estimated_seconds: int
    key_points: list[str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "chapter_index",
            _nonnegative_integer("chapter_index", self.chapter_index),
        )
        object.__setattr__(self, "title", _required_text("title", self.title))
        object.__setattr__(self, "objective", _required_text("objective", self.objective))
        object.__setattr__(
            self,
            "estimated_seconds",
            _positive_integer("estimated_seconds", self.estimated_seconds),
        )
        object.__setattr__(self, "key_points", _text_list("key_points", self.key_points))


@dataclass(frozen=True)
class YouTubeNarrationSection:
    chapter_index: int
    narration: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "chapter_index",
            _nonnegative_integer("chapter_index", self.chapter_index),
        )
        object.__setattr__(self, "narration", _required_text("narration", self.narration))


@dataclass(frozen=True)
class YouTubeScript:
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


class YouTubeOutlineGenerator(Protocol):
    def generate_outline(
        self,
        source: YouTubeScriptSource,
        *,
        channel_focus: str,
        target_minutes: int,
    ) -> list[YouTubeScriptChapter]: ...


class YouTubeScriptGenerator(Protocol):
    def generate_script(
        self,
        source: YouTubeScriptSource,
        chapters: list[YouTubeScriptChapter],
        *,
        channel_focus: str,
        target_minutes: int,
    ) -> YouTubeScript: ...


def validate_youtube_script_source(source: YouTubeScriptSource) -> YouTubeScriptSource:
    if not isinstance(source, YouTubeScriptSource):
        raise ValueError("source must be YouTubeScriptSource")
    return YouTubeScriptSource(**source.__dict__)


def build_youtube_script_source(
    idea: YouTubeIdea | RankedYouTubeIdea,
    potential: YouTubePotentialResult | YouTubePackagingCandidate | None = None,
    packaging: YouTubePackagingCandidate | None = None,
) -> YouTubeScriptSource:
    """Copy an existing idea, potential result, and selected packaging into script context."""

    if isinstance(idea, RankedYouTubeIdea):
        if isinstance(potential, YouTubePackagingCandidate):
            if packaging is not None:
                raise ValueError("packaging must be supplied exactly once")
            packaging = potential
        elif potential is not None:
            raise ValueError("potential is already included in RankedYouTubeIdea")
        potential = idea.potential
        idea = idea.idea
    idea = validate_youtube_idea(idea)
    if not isinstance(potential, YouTubePotentialResult):
        raise ValueError("potential must be YouTubePotentialResult")
    potential = YouTubePotentialResult(**potential.__dict__)
    if not isinstance(packaging, YouTubePackagingCandidate):
        raise ValueError("packaging must be YouTubePackagingCandidate")
    packaging = YouTubePackagingCandidate(**packaging.__dict__)
    return YouTubeScriptSource(
        idea_index=potential.idea_index,
        source_article_ids=idea.source_article_ids,
        selected_title=packaging.title,
        selected_thumbnail_text=packaging.thumbnail_text,
        hook=idea.hook,
        angle=idea.angle,
        target_audience=idea.target_audience,
        estimated_length_minutes=idea.estimated_length_minutes,
        original_chapters=idea.chapters,
        seo_keywords=idea.seo_keywords,
        youtube_potential_score=potential.youtube_potential_score,
        packaging_score=packaging.packaging_score,
    )


def validate_outline(
    chapters: list[YouTubeScriptChapter],
    target_minutes: int,
    duration_tolerance_ratio: float = DEFAULT_OUTLINE_DURATION_TOLERANCE,
) -> list[YouTubeScriptChapter]:
    target = validate_target_minutes(target_minutes)
    tolerance = _ratio("duration_tolerance_ratio", duration_tolerance_ratio)
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("outline must contain at least one chapter")
    by_index: dict[int, YouTubeScriptChapter] = {}
    for chapter in chapters:
        if not isinstance(chapter, YouTubeScriptChapter):
            raise ValueError("outline must contain YouTubeScriptChapter values")
        validated = YouTubeScriptChapter(**chapter.__dict__)
        if validated.chapter_index in by_index:
            raise ValueError("outline contains a duplicate chapter_index")
        by_index[validated.chapter_index] = validated
    if set(by_index) != set(range(len(chapters))):
        raise ValueError("chapter indexes must be sequential from zero")
    ordered = [by_index[index] for index in range(len(chapters))]
    target_seconds = target * 60
    total_seconds = sum(chapter.estimated_seconds for chapter in ordered)
    if not math.isclose(total_seconds, target_seconds, rel_tol=tolerance):
        raise ValueError("outline duration is outside the configured tolerance")
    return ordered


_JAPANESE_PATTERN = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


def uses_japanese_duration_heuristic(text: str) -> bool:
    """Return whether runtime estimation will use Japanese character counting."""

    return bool(_JAPANESE_PATTERN.search(_required_text("text", text)))


def estimate_script_minutes(text: str) -> float:
    """Estimate runtime using 280 Japanese non-space chars/min or 150 English words/min."""

    text = _required_text("text", text)
    if uses_japanese_duration_heuristic(text):
        units = len(re.sub(r"\s+", "", text))
        return units / JAPANESE_CHARACTERS_PER_MINUTE
    return len(text.split()) / ENGLISH_WORDS_PER_MINUTE


def validate_script(
    script: YouTubeScript,
    *,
    duration_tolerance_ratio: float = DEFAULT_OUTLINE_DURATION_TOLERANCE,
    script_length_tolerance_ratio: float = DEFAULT_SCRIPT_LENGTH_TOLERANCE,
) -> YouTubeScript:
    if not isinstance(script, YouTubeScript):
        raise ValueError("script must be YouTubeScript")
    script = YouTubeScript(**script.__dict__)
    chapters = validate_outline(
        script.chapters, script.target_minutes, duration_tolerance_ratio
    )
    by_index: dict[int, YouTubeNarrationSection] = {}
    for section in script.narration_sections:
        if not isinstance(section, YouTubeNarrationSection):
            raise ValueError("narration_sections must contain YouTubeNarrationSection values")
        validated = YouTubeNarrationSection(**section.__dict__)
        if validated.chapter_index in by_index:
            raise ValueError("narration contains a duplicate chapter_index")
        by_index[validated.chapter_index] = validated
    expected = set(range(len(chapters)))
    if set(by_index) != expected:
        raise ValueError("narration must cover every outline chapter exactly once")
    narration = [by_index[index] for index in range(len(chapters))]
    tolerance = _ratio("script_length_tolerance_ratio", script_length_tolerance_ratio)
    complete_text = " ".join(
        [script.opening_hook, *(section.narration for section in narration), script.closing]
    )
    estimated_minutes = estimate_script_minutes(complete_text)
    if not math.isclose(estimated_minutes, script.target_minutes, rel_tol=tolerance):
        raise ValueError("estimated script runtime is outside the configured tolerance")
    return YouTubeScript(
        title=script.title,
        thumbnail_text=script.thumbnail_text,
        target_minutes=script.target_minutes,
        opening_hook=script.opening_hook,
        chapters=chapters,
        narration_sections=narration,
        closing=script.closing,
        seo_keywords=script.seo_keywords,
    )


class LocalYouTubeOutlineGenerator:
    """Deterministic local outline generator requiring no network or API key."""

    def generate_outline(
        self,
        source: YouTubeScriptSource,
        *,
        channel_focus: str,
        target_minutes: int,
    ) -> list[YouTubeScriptChapter]:
        source = validate_youtube_script_source(source)
        focus = _required_text("channel_focus", channel_focus)
        target = validate_target_minutes(target_minutes)
        labels = [
            "Why it matters",
            "Background",
            "What happened",
            "How to understand it",
            "Key implications",
            "Risks and limits",
            "What happens next",
            "Final takeaway",
        ]
        total_seconds = target * 60
        base, remainder = divmod(total_seconds, len(labels))
        chapters = []
        for index, label in enumerate(labels):
            hint = source.original_chapters[index % len(source.original_chapters)]
            chapters.append(
                YouTubeScriptChapter(
                    chapter_index=index,
                    title=f"{source.selected_title}: {label}",
                    objective=f"Explain {hint} for viewers interested in {focus}.",
                    estimated_seconds=base + (1 if index < remainder else 0),
                    key_points=[source.hook, hint, source.angle],
                )
            )
        return chapters


def _local_narration(source: YouTubeScriptSource, chapter: YouTubeScriptChapter) -> str:
    source_text = " ".join([source.selected_title, source.hook, source.angle])
    if _JAPANESE_PATTERN.search(source_text):
        target_chars = max(
            1,
            round(
                chapter.estimated_seconds * JAPANESE_CHARACTERS_PER_MINUTE / 60
            ),
        )
        seed = f"{chapter.title}では{chapter.objective}{source.hook}{source.angle}"
        return (seed * (target_chars // len(seed) + 1))[:target_chars]
    target_words = max(1, round(chapter.estimated_seconds * ENGLISH_WORDS_PER_MINUTE / 60))
    sentences = []
    counter = 1
    while len(" ".join(sentences).split()) < target_words:
        point = chapter.key_points[(counter - 1) % len(chapter.key_points)]
        sentences.append(
            f"Point {counter} examines {point}. It connects this part of {source.selected_title} "
            f"to the chapter objective without adding facts beyond the supplied context."
        )
        counter += 1
    return " ".join(" ".join(sentences).split()[:target_words])


class LocalYouTubeScriptGenerator:
    """Deterministic generic narration generator for development and automated tests."""

    def generate_script(
        self,
        source: YouTubeScriptSource,
        chapters: list[YouTubeScriptChapter],
        *,
        channel_focus: str,
        target_minutes: int,
    ) -> YouTubeScript:
        source = validate_youtube_script_source(source)
        focus = _required_text("channel_focus", channel_focus)
        target = validate_target_minutes(target_minutes)
        chapters = validate_outline(chapters, target)
        return YouTubeScript(
            title=source.selected_title,
            thumbnail_text=source.selected_thumbnail_text,
            target_minutes=target,
            opening_hook=f"{source.hook} This explanation focuses on {focus}.",
            chapters=chapters,
            narration_sections=[
                YouTubeNarrationSection(
                    chapter_index=chapter.chapter_index,
                    narration=_local_narration(source, chapter),
                )
                for chapter in chapters
            ],
            closing=f"The key takeaway is {source.angle}. Keep watching this topic as it develops.",
            seo_keywords=source.seo_keywords,
        )


def generate_youtube_script(
    source: YouTubeScriptSource,
    outline_generator: YouTubeOutlineGenerator,
    script_generator: YouTubeScriptGenerator,
    *,
    channel_focus: str,
    target_minutes: int = DEFAULT_TARGET_MINUTES,
    outline_duration_tolerance_ratio: float = DEFAULT_OUTLINE_DURATION_TOLERANCE,
    script_length_tolerance_ratio: float = DEFAULT_SCRIPT_LENGTH_TOLERANCE,
) -> YouTubeScript:
    """Generate a validated script whose runtime remains an approximate text heuristic."""

    source = validate_youtube_script_source(source)
    focus = _required_text("channel_focus", channel_focus)
    target = validate_target_minutes(target_minutes)
    outline_tolerance = _ratio(
        "outline_duration_tolerance_ratio", outline_duration_tolerance_ratio
    )
    script_tolerance = _ratio("script_length_tolerance_ratio", script_length_tolerance_ratio)
    chapters = validate_outline(
        outline_generator.generate_outline(
            source, channel_focus=focus, target_minutes=target
        ),
        target,
        outline_tolerance,
    )
    script = script_generator.generate_script(
        source, chapters, channel_focus=focus, target_minutes=target
    )
    script = validate_script(
        script,
        duration_tolerance_ratio=outline_tolerance,
        script_length_tolerance_ratio=script_tolerance,
    )
    if script.title != source.selected_title:
        raise ValueError("script title must match the selected packaging title")
    if script.thumbnail_text != source.selected_thumbnail_text:
        raise ValueError("script thumbnail_text must match the selected packaging")
    if script.target_minutes != target:
        raise ValueError("script target_minutes must match the requested duration")
    if script.chapters != chapters:
        raise ValueError("script chapters must match the validated outline")
    if script.seo_keywords != source.seo_keywords:
        raise ValueError("script seo_keywords must match the supplied idea")
    return script

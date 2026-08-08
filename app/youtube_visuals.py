"""Provider-independent 16:9 visual planning from YouTube dialogue scripts."""

from dataclasses import dataclass
from typing import Literal, Protocol

from app.youtube_dialogue import DialogueChapter, DialogueLine, YouTubeDialogueScript
from app.youtube_script import validate_target_minutes

ASPECT_RATIO = "16:9"
SUPPORTED_VISUAL_TYPES = frozenset(
    {
        "character_dialogue",
        "realistic_scene",
        "technical_explainer",
        "infographic",
        "map",
        "timeline",
        "comparison",
        "object_closeup",
        "environment",
        "title_card",
    }
)
SUPPORTED_REFERENCE_SECTIONS = frozenset({"opening", "chapter", "closing"})


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _nonnegative_integer(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _text_list(name: str, values: list[str], *, allow_empty: bool = False) -> list[str]:
    if not isinstance(values, list) or (not values and not allow_empty):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise ValueError(f"{name} must be {qualifier}")
    return [_required_text(f"{name} item", value) for value in values]


def _validate_dialogue_lines(name: str, lines: list[DialogueLine]) -> list[DialogueLine]:
    if not isinstance(lines, list) or not lines:
        raise ValueError(f"{name} must be a non-empty list")
    validated = []
    for line in lines:
        if not isinstance(line, DialogueLine):
            raise ValueError(f"{name} must contain DialogueLine values")
        validated.append(DialogueLine(**line.__dict__))
    if [line.line_index for line in validated] != list(range(len(validated))):
        raise ValueError(f"{name} indexes must be sequential from zero")
    return validated


@dataclass(frozen=True)
class YouTubeVisualSource:
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
        object.__setattr__(
            self, "opening_lines", _validate_dialogue_lines("opening_lines", self.opening_lines)
        )
        if not isinstance(self.chapters, list) or not self.chapters:
            raise ValueError("chapters must be a non-empty list")
        chapters = []
        for chapter in self.chapters:
            if not isinstance(chapter, DialogueChapter):
                raise ValueError("chapters must contain DialogueChapter values")
            chapter = DialogueChapter(**chapter.__dict__)
            chapters.append(
                DialogueChapter(
                    chapter_index=chapter.chapter_index,
                    title=chapter.title,
                    lines=_validate_dialogue_lines("chapter lines", chapter.lines),
                )
            )
        if [chapter.chapter_index for chapter in chapters] != list(range(len(chapters))):
            raise ValueError("chapter indexes must be sequential from zero")
        object.__setattr__(self, "chapters", chapters)
        object.__setattr__(
            self, "closing_lines", _validate_dialogue_lines("closing_lines", self.closing_lines)
        )
        object.__setattr__(self, "seo_keywords", _text_list("seo_keywords", self.seo_keywords))


@dataclass(frozen=True)
class DialogueLineReference:
    section: Literal["opening", "chapter", "closing"] | str
    chapter_index: int | None
    line_index: int

    def __post_init__(self) -> None:
        section = _required_text("section", self.section)
        if section not in SUPPORTED_REFERENCE_SECTIONS:
            raise ValueError("section must be opening, chapter, or closing")
        object.__setattr__(self, "section", section)
        if section == "chapter":
            if self.chapter_index is None:
                raise ValueError("chapter reference requires chapter_index")
            object.__setattr__(
                self,
                "chapter_index",
                _nonnegative_integer("chapter_index", self.chapter_index),
            )
        elif self.chapter_index is not None:
            raise ValueError("opening and closing references require chapter_index=None")
        object.__setattr__(
            self, "line_index", _nonnegative_integer("line_index", self.line_index)
        )


@dataclass(frozen=True)
class YouTubeVisualScene:
    scene_index: int
    source_refs: list[DialogueLineReference]
    purpose: str
    visual_type: str
    visual_concept: str
    image_prompt: str
    negative_prompt: str
    aspect_ratio: str
    overlay_text: list[str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "scene_index", _nonnegative_integer("scene_index", self.scene_index)
        )
        if not isinstance(self.source_refs, list) or not self.source_refs:
            raise ValueError("source_refs must be a non-empty list")
        refs = []
        for reference in self.source_refs:
            if not isinstance(reference, DialogueLineReference):
                raise ValueError("source_refs must contain DialogueLineReference values")
            refs.append(DialogueLineReference(**reference.__dict__))
        keys = [(ref.section, ref.chapter_index, ref.line_index) for ref in refs]
        if len(set(keys)) != len(keys):
            raise ValueError("source_refs must not contain duplicates")
        object.__setattr__(self, "source_refs", refs)
        object.__setattr__(self, "purpose", _required_text("purpose", self.purpose))
        visual_type = _required_text("visual_type", self.visual_type)
        if visual_type not in SUPPORTED_VISUAL_TYPES:
            raise ValueError("visual_type is not supported")
        object.__setattr__(self, "visual_type", visual_type)
        object.__setattr__(
            self, "visual_concept", _required_text("visual_concept", self.visual_concept)
        )
        prompt = _required_text("image_prompt", self.image_prompt)
        lowered_prompt = prompt.casefold()
        if "16:9" not in lowered_prompt or not any(
            term in lowered_prompt for term in ("horizontal", "wide")
        ):
            raise ValueError("image_prompt must explicitly request horizontal 16:9 composition")
        object.__setattr__(self, "image_prompt", prompt)
        object.__setattr__(
            self, "negative_prompt", _required_text("negative_prompt", self.negative_prompt)
        )
        if self.aspect_ratio != ASPECT_RATIO:
            raise ValueError(f"aspect_ratio must be {ASPECT_RATIO}")
        object.__setattr__(
            self, "overlay_text", _text_list("overlay_text", self.overlay_text, allow_empty=True)
        )


@dataclass(frozen=True)
class YouTubeVisualPlan:
    title: str
    aspect_ratio: str
    scenes: list[YouTubeVisualScene]

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _required_text("title", self.title))
        if self.aspect_ratio != ASPECT_RATIO:
            raise ValueError(f"aspect_ratio must be {ASPECT_RATIO}")
        if not isinstance(self.scenes, list) or not self.scenes:
            raise ValueError("scenes must be a non-empty list")


class YouTubeVisualPlanner(Protocol):
    def plan(
        self, source: YouTubeVisualSource, *, channel_focus: str
    ) -> YouTubeVisualPlan: ...


def validate_visual_source(source: YouTubeVisualSource) -> YouTubeVisualSource:
    if not isinstance(source, YouTubeVisualSource):
        raise ValueError("source must be YouTubeVisualSource")
    return YouTubeVisualSource(**source.__dict__)


def build_youtube_visual_source(script: YouTubeDialogueScript) -> YouTubeVisualSource:
    if not isinstance(script, YouTubeDialogueScript):
        raise ValueError("dialogue_script must be YouTubeDialogueScript")
    script = YouTubeDialogueScript(**script.__dict__)
    return YouTubeVisualSource(
        title=script.title,
        thumbnail_text=script.thumbnail_text,
        target_minutes=script.target_minutes,
        opening_lines=script.opening_lines,
        chapters=script.chapters,
        closing_lines=script.closing_lines,
        seo_keywords=script.seo_keywords,
    )


def validate_line_reference(
    reference: DialogueLineReference, source: YouTubeVisualSource
) -> DialogueLineReference:
    source = validate_visual_source(source)
    if not isinstance(reference, DialogueLineReference):
        raise ValueError("reference must be DialogueLineReference")
    reference = DialogueLineReference(**reference.__dict__)
    if reference.section == "opening":
        line_count = len(source.opening_lines)
    elif reference.section == "closing":
        line_count = len(source.closing_lines)
    else:
        if reference.chapter_index is None or reference.chapter_index >= len(source.chapters):
            raise ValueError("reference points to an unknown chapter")
        line_count = len(source.chapters[reference.chapter_index].lines)
    if reference.line_index >= line_count:
        raise ValueError("reference points to an unknown dialogue line")
    return reference


def _reference_position(
    reference: DialogueLineReference, source: YouTubeVisualSource
) -> tuple[int, int]:
    if reference.section == "opening":
        return (0, reference.line_index)
    if reference.section == "chapter":
        assert reference.chapter_index is not None
        return (reference.chapter_index + 1, reference.line_index)
    return (len(source.chapters) + 1, reference.line_index)


def validate_visual_scene(
    scene: YouTubeVisualScene, source: YouTubeVisualSource
) -> YouTubeVisualScene:
    source = validate_visual_source(source)
    if not isinstance(scene, YouTubeVisualScene):
        raise ValueError("plan must contain YouTubeVisualScene values")
    scene = YouTubeVisualScene(**scene.__dict__)
    refs = [validate_line_reference(reference, source) for reference in scene.source_refs]
    positions = [_reference_position(reference, source) for reference in refs]
    if positions != sorted(positions):
        raise ValueError("scene references must preserve source chronology")
    return YouTubeVisualScene(**{**scene.__dict__, "source_refs": refs})


def validate_visual_plan(
    plan: YouTubeVisualPlan, source: YouTubeVisualSource
) -> YouTubeVisualPlan:
    source = validate_visual_source(source)
    if not isinstance(plan, YouTubeVisualPlan):
        raise ValueError("planner must return YouTubeVisualPlan")
    plan = YouTubeVisualPlan(**plan.__dict__)
    if plan.title != source.title:
        raise ValueError("visual plan title must match the source title")
    scenes = [validate_visual_scene(scene, source) for scene in plan.scenes]
    if [scene.scene_index for scene in scenes] != list(range(len(scenes))):
        raise ValueError("scene indexes must be sequential from zero")
    first_positions = [
        _reference_position(scene.source_refs[0], source) for scene in scenes
    ]
    if first_positions != sorted(first_positions):
        raise ValueError("visual scenes must preserve source chronology")
    covered_chapters = {
        reference.chapter_index
        for scene in scenes
        for reference in scene.source_refs
        if reference.section == "chapter"
    }
    if covered_chapters != set(range(len(source.chapters))):
        raise ValueError("every source chapter must be represented by a visual scene")
    return YouTubeVisualPlan(title=plan.title, aspect_ratio=ASPECT_RATIO, scenes=scenes)


def _local_visual_type(chapter: DialogueChapter) -> str:
    text = f"{chapter.title} {' '.join(line.text for line in chapter.lines)}".casefold()
    mappings = (
        (("比較", "compare", "versus"), "comparison"),
        (("地図", "map", "location"), "map"),
        (("年表", "timeline", "history"), "timeline"),
        (("仕組み", "system", "technical", "how"), "technical_explainer"),
    )
    for terms, visual_type in mappings:
        if any(term in text for term in terms):
            return visual_type
    return "character_dialogue"


class LocalYouTubeVisualPlanner:
    """Deterministic offline planner using one coherent beat per source section/chapter."""

    def plan(
        self, source: YouTubeVisualSource, *, channel_focus: str
    ) -> YouTubeVisualPlan:
        source = validate_visual_source(source)
        focus = _required_text("channel_focus", channel_focus)
        scenes = []

        def add_scene(
            refs: list[DialogueLineReference],
            purpose: str,
            visual_type: str,
            concept: str,
            overlay_text: list[str],
        ) -> None:
            scenes.append(
                YouTubeVisualScene(
                    scene_index=len(scenes),
                    source_refs=refs,
                    purpose=purpose,
                    visual_type=visual_type,
                    visual_concept=concept,
                    image_prompt=(
                        f"{concept}; clear focal subject, balanced visual hierarchy, clean space "
                        f"for later overlay typography, horizontal 16:9 YouTube composition, "
                        f"wide cinematic framing, grounded only in supplied {focus} dialogue"
                    ),
                    negative_prompt=(
                        "no unreadable text, no garbled letters, no duplicate objects, "
                        "no malformed anatomy, no watermark, no vertical composition"
                    ),
                    aspect_ratio=ASPECT_RATIO,
                    overlay_text=overlay_text,
                )
            )

        add_scene(
            [DialogueLineReference("opening", None, line.line_index) for line in source.opening_lines],
            "Introduce the core question",
            "title_card",
            f"Opening visual concept for {source.title}",
            [source.thumbnail_text],
        )
        for chapter in source.chapters:
            excerpt = " ".join(line.text for line in chapter.lines)[:240]
            speakers = ", ".join(dict.fromkeys(line.speaker for line in chapter.lines))
            add_scene(
                [
                    DialogueLineReference("chapter", chapter.chapter_index, line.line_index)
                    for line in chapter.lines
                ],
                f"Explain chapter {chapter.chapter_index}",
                _local_visual_type(chapter),
                (
                    f"Visual explanation of {chapter.title} with consistent recurring "
                    f"dialogue characters {speakers}: {excerpt}"
                ),
                [chapter.title],
            )
        add_scene(
            [DialogueLineReference("closing", None, line.line_index) for line in source.closing_lines],
            "Conclude the explanation",
            "character_dialogue",
            (
                f"Closing visual concept for {source.title} with consistent recurring "
                f"characters {', '.join(dict.fromkeys(line.speaker for line in source.closing_lines))}"
            ),
            [],
        )
        return YouTubeVisualPlan(title=source.title, aspect_ratio=ASPECT_RATIO, scenes=scenes)


def generate_youtube_visual_plan(
    dialogue_script: YouTubeDialogueScript,
    planner: YouTubeVisualPlanner,
    *,
    channel_focus: str,
) -> YouTubeVisualPlan:
    """Create and defensively validate a text-only visual plan; no images are generated."""

    source = build_youtube_visual_source(dialogue_script)
    focus = _required_text("channel_focus", channel_focus)
    return validate_visual_plan(planner.plan(source, channel_focus=focus), source)

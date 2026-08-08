from dataclasses import replace

import pytest

from app.youtube_dialogue import DialogueChapter, DialogueLine, YouTubeDialogueScript
from app.youtube_visuals import (
    ASPECT_RATIO,
    SUPPORTED_VISUAL_TYPES,
    DialogueLineReference,
    LocalYouTubeVisualPlanner,
    YouTubeVisualPlan,
    YouTubeVisualScene,
    build_youtube_visual_source,
    generate_youtube_visual_plan,
    validate_line_reference,
    validate_visual_plan,
)


def dialogue_script() -> YouTubeDialogueScript:
    return YouTubeDialogueScript(
        title="AI Release Explained", thumbnail_text="WHAT CHANGED", target_minutes=15,
        opening_lines=[DialogueLine(0, "ハル", "What changed?"), DialogueLine(1, "さび助", "A model was released.")],
        chapters=[
            DialogueChapter(index, title, [
                DialogueLine(0, "ハル", f"What about {title}?"),
                DialogueLine(1, "さび助", f"This explains the supplied {title} context."),
                DialogueLine(2, "ハル", "That clarifies the point."),
            ])
            for index, title in enumerate(["Background", "Technical system", "Comparison"])
        ],
        closing_lines=[DialogueLine(0, "さび助", "That is the takeaway."), DialogueLine(1, "ハル", "Understood.")],
        seo_keywords=["AI", "model"],
    )


def source():
    return build_youtube_visual_source(dialogue_script())


def reference(section="chapter", chapter_index=0, line_index=0):
    return DialogueLineReference(section, chapter_index, line_index)


def scene(index=0, refs=None, **overrides):
    values = dict(
        scene_index=index, source_refs=refs or [reference()], purpose="Explain the concept",
        visual_type="technical_explainer", visual_concept="A grounded explanatory diagram",
        image_prompt="A grounded diagram, horizontal 16:9 YouTube composition, wide framing",
        negative_prompt="no unreadable text, no duplicate objects, no vertical composition",
        aspect_ratio="16:9", overlay_text=[],
    )
    values.update(overrides)
    return YouTubeVisualScene(**values)


def test_source_conversion_preserves_dialogue_and_metadata() -> None:
    built = source()
    script = dialogue_script()
    assert built.title == script.title
    assert built.thumbnail_text == script.thumbnail_text
    assert built.target_minutes == script.target_minutes
    assert built.opening_lines == script.opening_lines
    assert built.chapters == script.chapters
    assert built.closing_lines == script.closing_lines
    assert built.seo_keywords == script.seo_keywords


@pytest.mark.parametrize("values", [
    ("opening", None, 0), ("chapter", 0, 1), ("closing", None, 0),
])
def test_valid_line_references(values) -> None:
    ref = DialogueLineReference(*values)
    assert validate_line_reference(ref, source()) == ref


@pytest.mark.parametrize("values", [
    ("opening", 0, 0), ("closing", 1, 0), ("chapter", None, 0),
    ("chapter", -1, 0), ("chapter", True, 0), ("chapter", 0, -1),
    ("chapter", 0, True), ("other", None, 0),
])
def test_invalid_reference_model_rejected(values) -> None:
    with pytest.raises(ValueError): DialogueLineReference(*values)


@pytest.mark.parametrize("ref", [
    DialogueLineReference("opening", None, 9),
    DialogueLineReference("chapter", 9, 0),
    DialogueLineReference("chapter", 0, 9),
    DialogueLineReference("closing", None, 9),
])
def test_unknown_reference_rejected(ref) -> None:
    with pytest.raises(ValueError): validate_line_reference(ref, source())


def test_duplicate_reference_in_one_scene_rejected() -> None:
    ref = reference()
    with pytest.raises(ValueError, match="duplicates"): scene(refs=[ref, ref])


@pytest.mark.parametrize("field,value", [
    ("scene_index", -1), ("scene_index", True), ("purpose", " "),
    ("visual_type", "random_type"), ("visual_concept", ""),
    ("image_prompt", " "), ("negative_prompt", ""),
    ("aspect_ratio", "9:16"), ("aspect_ratio", "1:1"),
    ("overlay_text", [" "]),
])
def test_scene_rejects_invalid_fields(field: str, value) -> None:
    values = scene().__dict__.copy()
    values[field] = value
    with pytest.raises(ValueError): YouTubeVisualScene(**values)


def test_image_prompt_requires_explicit_horizontal_16_by_9() -> None:
    with pytest.raises(ValueError, match="horizontal 16:9"):
        scene(image_prompt="A generic image")


@pytest.mark.parametrize("visual_type", sorted(SUPPORTED_VISUAL_TYPES))
def test_all_supported_visual_types_accepted(visual_type: str) -> None:
    assert scene(visual_type=visual_type).visual_type == visual_type


def test_local_planner_is_deterministic_uses_coherent_multi_line_scenes() -> None:
    planner = LocalYouTubeVisualPlanner()
    first = planner.plan(source(), channel_focus="AI news")
    second = planner.plan(source(), channel_focus="AI news")
    assert first == second
    assert [item.scene_index for item in first.scenes] == list(range(5))
    assert all(item.aspect_ratio == ASPECT_RATIO for item in first.scenes)
    assert all("16:9" in item.image_prompt and "horizontal" in item.image_prompt for item in first.scenes)
    assert all(len(item.source_refs) > 1 for item in first.scenes)
    covered = {
        ref.chapter_index for item in first.scenes for ref in item.source_refs
        if ref.section == "chapter"
    }
    assert covered == {0, 1, 2}


def test_visual_plan_rejects_missing_chapter_coverage() -> None:
    plan = LocalYouTubeVisualPlanner().plan(source(), channel_focus="AI")
    without_chapter = [item for item in plan.scenes if not any(
        ref.section == "chapter" and ref.chapter_index == 1 for ref in item.source_refs
    )]
    without_chapter = [replace(item, scene_index=index) for index, item in enumerate(without_chapter)]
    with pytest.raises(ValueError, match="every source chapter"):
        validate_visual_plan(replace(plan, scenes=without_chapter), source())


def test_visual_plan_rejects_nonsequential_scene_indexes() -> None:
    plan = LocalYouTubeVisualPlanner().plan(source(), channel_focus="AI")
    with pytest.raises(ValueError, match="scene indexes"):
        validate_visual_plan(replace(plan, scenes=[replace(plan.scenes[0], scene_index=2), *plan.scenes[1:]]), source())


def test_visual_plan_rejects_chronology_reversal() -> None:
    plan = LocalYouTubeVisualPlanner().plan(source(), channel_focus="AI")
    scenes = list(plan.scenes)
    scenes[2], scenes[3] = replace(scenes[3], scene_index=2), replace(scenes[2], scene_index=3)
    with pytest.raises(ValueError, match="chronology"):
        validate_visual_plan(replace(plan, scenes=scenes), source())


def test_empty_overlay_is_allowed() -> None:
    assert scene(overlay_text=[]).overlay_text == []


def test_service_rejects_empty_focus_before_planner() -> None:
    class Never:
        def plan(self, *args, **kwargs): raise AssertionError("planner called")
    with pytest.raises(ValueError):
        generate_youtube_visual_plan(dialogue_script(), Never(), channel_focus=" ")


def test_end_to_end_local_visual_plan() -> None:
    plan = generate_youtube_visual_plan(
        dialogue_script(), LocalYouTubeVisualPlanner(), channel_focus="AI news"
    )
    assert isinstance(plan, YouTubeVisualPlan)
    assert plan.title == dialogue_script().title
    assert plan.aspect_ratio == "16:9"

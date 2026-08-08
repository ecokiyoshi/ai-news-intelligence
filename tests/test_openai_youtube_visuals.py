from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from app.openai_youtube_visuals import (
    YOUTUBE_VISUAL_INSTRUCTIONS,
    OpenAIDialogueLineReference,
    OpenAIYouTubeVisualPlanResponse,
    OpenAIYouTubeVisualPlanner,
    OpenAIYouTubeVisualScene,
)
from app.youtube_dialogue import DialogueChapter, DialogueLine, YouTubeDialogueScript
from app.youtube_visuals import (
    LocalYouTubeVisualPlanner,
    YouTubeVisualPlanner,
    build_youtube_visual_source,
)


def source():
    script = YouTubeDialogueScript(
        title="AI Release Explained", thumbnail_text="WHAT CHANGED", target_minutes=15,
        opening_lines=[DialogueLine(0, "ハル", "What changed?"), DialogueLine(1, "さび助", "A model was released.")],
        chapters=[
            DialogueChapter(index, title, [
                DialogueLine(0, "ハル", f"Question about {title}"),
                DialogueLine(1, "さび助", f"Explanation of {title}"),
            ])
            for index, title in enumerate(["Background", "Technical details"])
        ],
        closing_lines=[DialogueLine(0, "さび助", "Final takeaway"), DialogueLine(1, "ハル", "Understood")],
        seo_keywords=["AI", "model"],
    )
    return build_youtube_visual_source(script)


def valid_response() -> OpenAIYouTubeVisualPlanResponse:
    plan = LocalYouTubeVisualPlanner().plan(source(), channel_focus="AI news")
    return OpenAIYouTubeVisualPlanResponse(scenes=[
        OpenAIYouTubeVisualScene(
            scene_index=scene.scene_index,
            source_refs=[OpenAIDialogueLineReference(**ref.__dict__) for ref in scene.source_refs],
            purpose=scene.purpose, visual_type=scene.visual_type,
            visual_concept=scene.visual_concept, image_prompt=scene.image_prompt,
            negative_prompt=scene.negative_prompt, aspect_ratio=scene.aspect_ratio,
            overlay_text=scene.overlay_text,
        ) for scene in plan.scenes
    ])


class FakeResponses:
    def __init__(self, parsed=None, error: Exception | None = None):
        self.parsed = parsed
        self.error = error
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error: raise self.error
        return SimpleNamespace(output=[SimpleNamespace(
            type="message", content=[SimpleNamespace(parsed=self.parsed)]
        )])


class FakeClient:
    def __init__(self, parsed=None, error: Exception | None = None):
        self.responses = FakeResponses(parsed, error)


def test_openai_planner_uses_typed_responses_and_complete_dialogue_context() -> None:
    client = FakeClient(valid_response())
    planner: YouTubeVisualPlanner = cast(
        YouTubeVisualPlanner,
        OpenAIYouTubeVisualPlanner(client=client, model="visual-model"),
    )
    plan = planner.plan(source(), channel_focus="AI news")
    assert plan.title == "AI Release Explained"
    call = client.responses.calls[0]
    assert call["model"] == "visual-model"
    assert call["text_format"] is OpenAIYouTubeVisualPlanResponse
    for expected in (
        "AI news", "AI Release Explained", "WHAT CHANGED", '"target_minutes":15',
        '"seo_keywords"', '"opening"', "What changed?", "ハル", "さび助",
        '"chapter_index":0', "Background", "Question about Background",
        "Explanation of Technical details", '"closing"', "Final takeaway", '"16:9"',
    ):
        assert expected in call["input"]


def test_openai_instructions_require_16_by_9_overlay_and_no_one_line_segmentation() -> None:
    lowered = YOUTUBE_VISUAL_INSTRUCTIONS.casefold()
    assert "16:9" in lowered and "horizontal" in lowered and "youtube" in lowered
    assert "long japanese text" in lowered and "overlay text separately" in lowered
    assert "one image for every line" in lowered
    assert "planning and prompt generation only" in lowered
    assert "scene_index" in lowered and "starting at zero" in lowered
    assert "increasing consecutively" in lowered


def test_openai_reference_schema_rejects_unsupported_section() -> None:
    with pytest.raises(ValidationError):
        OpenAIDialogueLineReference(
            section="intro", chapter_index=None, line_index=0
        )


@pytest.mark.parametrize("kind", ["one_based", "gapped", "duplicate"])
def test_openai_planner_normalizes_provider_scene_indexes(kind: str) -> None:
    parsed = valid_response()
    if kind == "one_based":
        for index, scene in enumerate(parsed.scenes, start=1):
            scene.scene_index = index
    elif kind == "gapped":
        for index, scene in enumerate(parsed.scenes):
            scene.scene_index = index * 2
    else:
        for scene in parsed.scenes:
            scene.scene_index = 3

    plan = OpenAIYouTubeVisualPlanner(client=FakeClient(parsed)).plan(
        source(), channel_focus="AI news"
    )

    assert [scene.scene_index for scene in plan.scenes] == list(range(len(plan.scenes)))


def test_openai_planner_still_rejects_provider_scene_order_reversal() -> None:
    parsed = valid_response()
    parsed.scenes[1], parsed.scenes[2] = parsed.scenes[2], parsed.scenes[1]

    with pytest.raises(ValueError, match="chronology"):
        OpenAIYouTubeVisualPlanner(client=FakeClient(parsed)).plan(
            source(), channel_focus="AI news"
        )


@pytest.mark.parametrize("kind", ["reference", "coverage", "visual_type", "ratio", "prompt"])
def test_openai_planner_rejects_malformed_output(kind: str) -> None:
    parsed = valid_response()
    if kind == "reference":
        parsed.scenes[1].source_refs[0].line_index = 99
    elif kind == "coverage":
        parsed.scenes = [
            scene for scene in parsed.scenes
            if not any(ref.section == "chapter" and ref.chapter_index == 1 for ref in scene.source_refs)
        ]
        for index, scene in enumerate(parsed.scenes): scene.scene_index = index
    elif kind == "visual_type":
        parsed.scenes[0].visual_type = "random_type"
    elif kind == "ratio":
        parsed.scenes[0].aspect_ratio = "9:16"
    else:
        parsed.scenes[0].image_prompt = " "
    with pytest.raises(ValueError):
        OpenAIYouTubeVisualPlanner(client=FakeClient(parsed)).plan(
            source(), channel_focus="AI news"
        )


def test_openai_planner_rejects_missing_parsed_output() -> None:
    with pytest.raises(ValueError, match="parsed visual plan"):
        OpenAIYouTubeVisualPlanner(client=FakeClient()).plan(
            source(), channel_focus="AI"
        )


def test_openai_planner_propagates_provider_exception_without_network_or_key() -> None:
    with pytest.raises(RuntimeError, match="API unavailable"):
        OpenAIYouTubeVisualPlanner(
            client=FakeClient(error=RuntimeError("API unavailable"))
        ).plan(source(), channel_focus="AI")

"""Claude-backed text-only visual planning for YouTube dialogue scripts."""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt

from app.anthropic_client import AnthropicClient, build_default_client, parse_structured, resolve_model
from app.youtube_visuals import (
    ASPECT_RATIO,
    SUPPORTED_VISUAL_TYPES,
    DialogueLineReference,
    YouTubeVisualPlan,
    YouTubeVisualScene,
    YouTubeVisualSource,
    validate_scene_limit,
    validate_visual_plan,
    validate_visual_source,
)

YOUTUBE_VISUAL_INSTRUCTIONS = """\
This is visual planning and prompt generation only; do not generate or retrieve images. Segment
the supplied dialogue into coherent visual beats. Do not blindly create one image for every line;
combine adjacent lines when they share one concept. Preserve source chronology, represent every
source chapter with at least one scene, and trace every scene to exact supplied dialogue lines. Use
scene_index values starting at zero and increasing consecutively in the returned scene-array order.
Use no more than the supplied maximum_scene_count, combining adjacent visual beats as needed while still
covering every source chapter and preserving chronology. Use only supplied factual context. Do not
invent facts, statistics, events, organizations, equipment,
product models, locations, dates, quotes, or outcomes. Choose only one of these visual types:
character_dialogue, realistic_scene, technical_explainer, infographic, map, timeline, comparison,
object_closeup, environment, title_card. Create a detailed but concise reusable image prompt with a
clear focal subject, visual hierarchy, suitable framing, lighting, mood, technical accuracy, and
explicit horizontal 16:9 YouTube composition. Keep recurring characters consistent without
inventing canonical design details. Avoid rendered long Japanese text and garbled Japanese text;
leave clean space for typography and provide any concise overlay text separately. Include a concise
negative prompt that rejects unreadable text, duplicate objects, watermarks, and vertical framing.
Do not include image-API parameters. Return structured output only. Do not use web research.
"""


class AnthropicDialogueLineReference(BaseModel):
    section: Literal["opening", "chapter", "closing"]
    chapter_index: StrictInt | None = None
    line_index: StrictInt = Field(ge=0)


class AnthropicYouTubeVisualScene(BaseModel):
    scene_index: StrictInt = Field(ge=0)
    source_refs: list[AnthropicDialogueLineReference] = Field(min_length=1)
    purpose: str = Field(min_length=1)
    visual_type: str = Field(min_length=1)
    visual_concept: str = Field(min_length=1)
    image_prompt: str = Field(min_length=1)
    negative_prompt: str = Field(min_length=1)
    aspect_ratio: str = Field(min_length=1)
    overlay_text: list[str]


class AnthropicYouTubeVisualPlanResponse(BaseModel):
    scenes: list[AnthropicYouTubeVisualScene] = Field(min_length=1)


def _line_payload(line: Any) -> dict[str, object]:
    return {"line_index": line.line_index, "speaker": line.speaker, "text": line.text}


class AnthropicYouTubeVisualPlanner:
    """Plan grounded 16:9 visual concepts using a forced structured Claude tool call."""

    def __init__(
        self,
        *,
        client: AnthropicClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else build_default_client(timeout)
        self.model = resolve_model(model)

    def plan(
        self, source: YouTubeVisualSource, *, channel_focus: str, scene_limit: int
    ) -> YouTubeVisualPlan:
        source = validate_visual_source(source)
        if not isinstance(channel_focus, str) or not channel_focus.strip():
            raise ValueError("channel_focus must be a non-empty string")
        focus = channel_focus.strip()
        limit = validate_scene_limit(scene_limit)
        parsed = parse_structured(
            self.client,
            model=self.model,
            system=YOUTUBE_VISUAL_INSTRUCTIONS,
            input_text=json.dumps(
                {
                    "channel_focus": focus,
                    "maximum_scene_count": limit,
                    "required_aspect_ratio": ASPECT_RATIO,
                    "supported_visual_types": sorted(SUPPORTED_VISUAL_TYPES),
                    "source": {
                        "title": source.title,
                        "thumbnail_text": source.thumbnail_text,
                        "target_minutes": source.target_minutes,
                        "seo_keywords": source.seo_keywords,
                        "opening": [_line_payload(line) for line in source.opening_lines],
                        "chapters": [
                            {
                                "chapter_index": chapter.chapter_index,
                                "title": chapter.title,
                                "lines": [_line_payload(line) for line in chapter.lines],
                            }
                            for chapter in source.chapters
                        ],
                        "closing": [_line_payload(line) for line in source.closing_lines],
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            response_model=AnthropicYouTubeVisualPlanResponse,
        )
        plan = YouTubeVisualPlan(
            title=source.title,
            aspect_ratio=ASPECT_RATIO,
            scenes=[
                YouTubeVisualScene(
                    scene_index=scene_index,
                    source_refs=[
                        DialogueLineReference(
                            section=reference.section,
                            chapter_index=reference.chapter_index,
                            line_index=reference.line_index,
                        )
                        for reference in scene.source_refs
                    ],
                    purpose=scene.purpose,
                    visual_type=scene.visual_type,
                    visual_concept=scene.visual_concept,
                    image_prompt=scene.image_prompt,
                    negative_prompt=scene.negative_prompt,
                    aspect_ratio=scene.aspect_ratio,
                    overlay_text=scene.overlay_text,
                )
                for scene_index, scene in enumerate(parsed.scenes)
            ],
        )
        return validate_visual_plan(plan, source)

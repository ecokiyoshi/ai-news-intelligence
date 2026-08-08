"""OpenAI-backed text-only visual planning for YouTube dialogue scripts."""

import json
import os
from typing import Any, Literal, Protocol

from openai import OpenAI
from pydantic import BaseModel, Field, StrictInt

from app.openai_summarizer import DEFAULT_OPENAI_MODEL
from app.youtube_visuals import (
    ASPECT_RATIO,
    SUPPORTED_VISUAL_TYPES,
    DialogueLineReference,
    YouTubeVisualPlan,
    YouTubeVisualScene,
    YouTubeVisualSource,
    validate_visual_plan,
    validate_visual_source,
)

YOUTUBE_VISUAL_INSTRUCTIONS = """\
This is visual planning and prompt generation only; do not generate or retrieve images. Segment
the supplied dialogue into coherent visual beats. Do not blindly create one image for every line;
combine adjacent lines when they share one concept. Preserve source chronology, represent every
source chapter with at least one scene, and trace every scene to exact supplied dialogue lines. Use
scene_index values starting at zero and increasing consecutively in the returned scene-array order.
Use only supplied factual context. Do not invent facts, statistics, events, organizations, equipment,
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


class OpenAIDialogueLineReference(BaseModel):
    section: Literal["opening", "chapter", "closing"]
    chapter_index: StrictInt | None = None
    line_index: StrictInt = Field(ge=0)


class OpenAIYouTubeVisualScene(BaseModel):
    scene_index: StrictInt = Field(ge=0)
    source_refs: list[OpenAIDialogueLineReference] = Field(min_length=1)
    purpose: str = Field(min_length=1)
    visual_type: str = Field(min_length=1)
    visual_concept: str = Field(min_length=1)
    image_prompt: str = Field(min_length=1)
    negative_prompt: str = Field(min_length=1)
    aspect_ratio: str = Field(min_length=1)
    overlay_text: list[str]


class OpenAIYouTubeVisualPlanResponse(BaseModel):
    scenes: list[OpenAIYouTubeVisualScene] = Field(min_length=1)


class ResponsesParser(Protocol):
    def parse(
        self,
        *,
        model: str,
        instructions: str,
        input: str,
        text_format: type[OpenAIYouTubeVisualPlanResponse],
    ) -> Any: ...


class OpenAIParsingClient(Protocol):
    responses: ResponsesParser


def _line_payload(line: Any) -> dict[str, object]:
    return {"line_index": line.line_index, "speaker": line.speaker, "text": line.text}


def _parsed_output(response: Any) -> OpenAIYouTubeVisualPlanResponse:
    for output_item in response.output:
        if getattr(output_item, "type", None) != "message":
            continue
        for content_item in output_item.content:
            parsed = getattr(content_item, "parsed", None)
            if parsed is not None:
                return parsed
    raise ValueError("OpenAI response did not contain a parsed visual plan")


class OpenAIYouTubeVisualPlanner:
    """Plan grounded 16:9 visual concepts using typed Responses API output."""

    def __init__(
        self,
        *,
        client: OpenAIParsingClient | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.client = client if client is not None else OpenAI(timeout=timeout)
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL

    def plan(
        self, source: YouTubeVisualSource, *, channel_focus: str
    ) -> YouTubeVisualPlan:
        source = validate_visual_source(source)
        if not isinstance(channel_focus, str) or not channel_focus.strip():
            raise ValueError("channel_focus must be a non-empty string")
        focus = channel_focus.strip()
        response = self.client.responses.parse(
            model=self.model,
            instructions=YOUTUBE_VISUAL_INSTRUCTIONS,
            input=json.dumps(
                {
                    "channel_focus": focus,
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
            text_format=OpenAIYouTubeVisualPlanResponse,
        )
        parsed = _parsed_output(response)
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

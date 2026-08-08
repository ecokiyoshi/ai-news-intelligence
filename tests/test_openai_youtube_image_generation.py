import base64
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.openai_youtube_image_generation import OpenAISceneImageGenerator
from app.youtube_image_generation import (
    DEFAULT_IMAGE_SIZE,
    LocalSceneImageGenerator,
    build_image_request,
    compose_generation_prompt,
)
from app.youtube_visuals import DialogueLineReference, YouTubeVisualScene


def request():
    scene = YouTubeVisualScene(
        scene_index=0, source_refs=[DialogueLineReference("chapter", 0, 0)],
        purpose="Explain", visual_type="technical_explainer",
        visual_concept="Grounded concept",
        image_prompt="Original grounded visual, horizontal 16:9 YouTube composition",
        negative_prompt="unreadable text, duplicate objects, vertical composition",
        aspect_ratio="16:9", overlay_text=["Later overlay"],
    )
    base = build_image_request(scene)
    return replace(base, image_prompt=compose_generation_prompt(base))


def png_bytes() -> bytes:
    base = build_image_request(YouTubeVisualScene(
        scene_index=0, source_refs=[DialogueLineReference("chapter", 0, 0)],
        purpose="Explain", visual_type="technical_explainer", visual_concept="Concept",
        image_prompt="Prompt, horizontal 16:9 YouTube composition",
        negative_prompt="no bad output", aspect_ratio="16:9", overlay_text=[],
    ))
    return LocalSceneImageGenerator().generate(base, size=DEFAULT_IMAGE_SIZE).data


class FakeImages:
    def __init__(self, encoded=None, revised_prompt="Revised", error=None, empty=False):
        self.encoded = encoded
        self.revised_prompt = revised_prompt
        self.error = error
        self.empty = empty
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error: raise self.error
        if self.empty: return SimpleNamespace(data=[])
        return SimpleNamespace(data=[SimpleNamespace(
            b64_json=self.encoded, revised_prompt=self.revised_prompt
        )])


class FakeClient:
    def __init__(self, **kwargs):
        self.images = FakeImages(**kwargs)


def test_openai_generator_uses_installed_images_api_shape_and_decodes_base64() -> None:
    expected = png_bytes()
    client = FakeClient(encoded=base64.b64encode(expected).decode(), revised_prompt="Better prompt")
    payload = OpenAISceneImageGenerator(client=client, model="image-model").generate(
        request(), size="1792x1024"
    )
    assert payload.data == expected
    assert payload.revised_prompt == "Better prompt"
    assert payload.provider == "openai" and payload.model == "image-model"
    assert (payload.width, payload.height) == (1792, 1024)
    call = client.images.calls[0]
    assert call == {
        "model": "image-model", "prompt": request().image_prompt,
        "size": "1792x1024", "n": 1, "output_format": "png",
    }
    assert "response_format" not in call
    for expected_text in (
        "Original grounded visual", "horizontal 16:9", "unreadable text",
        "long Japanese text", "clean negative space", "typography",
    ):
        assert expected_text in call["prompt"]


def test_openai_generator_rejects_unsupported_size_before_api_call() -> None:
    client = FakeClient(encoded=base64.b64encode(png_bytes()).decode())
    with pytest.raises(ValueError, match="supports"):
        OpenAISceneImageGenerator(client=client).generate(request(), size="1536x864")
    assert client.images.calls == []


@pytest.mark.parametrize("kwargs", [
    {"empty": True}, {"encoded": None}, {"encoded": ""}, {"encoded": "not base64"},
])
def test_openai_generator_rejects_missing_empty_or_invalid_image_data(kwargs) -> None:
    with pytest.raises(ValueError):
        OpenAISceneImageGenerator(client=FakeClient(**kwargs)).generate(
            request(), size="1792x1024"
        )


def test_openai_exception_propagates_to_core_retry_layer() -> None:
    with pytest.raises(RuntimeError, match="API unavailable"):
        OpenAISceneImageGenerator(
            client=FakeClient(error=RuntimeError("API unavailable"))
        ).generate(request(), size="1792x1024")

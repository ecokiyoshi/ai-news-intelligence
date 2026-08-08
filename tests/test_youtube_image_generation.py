import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.youtube_image_generation import (
    DEFAULT_IMAGE_SIZE,
    GeneratedImagePayload,
    LocalSceneImageGenerator,
    SceneImageGenerationError,
    YouTubeImageRequest,
    build_image_request,
    compose_generation_prompt,
    deterministic_scene_filename,
    generate_youtube_scene_images,
    validate_generated_payload,
)
from app.youtube_visuals import (
    DialogueLineReference,
    YouTubeVisualPlan,
    YouTubeVisualScene,
)


def scene(index: int) -> YouTubeVisualScene:
    return YouTubeVisualScene(
        scene_index=index,
        source_refs=[DialogueLineReference("chapter", index, 0)],
        purpose=f"Explain scene {index}", visual_type="technical_explainer",
        visual_concept=f"Grounded concept {index}",
        image_prompt=f"Grounded scene {index}, horizontal 16:9 YouTube composition",
        negative_prompt="unreadable text, duplicate objects, vertical composition",
        aspect_ratio="16:9", overlay_text=[f"Scene {index}"] if index else [],
    )


def plan(count: int = 3) -> YouTubeVisualPlan:
    return YouTubeVisualPlan(
        title="Video title", aspect_ratio="16:9", scenes=[scene(i) for i in range(count)]
    )


def request() -> YouTubeImageRequest:
    return build_image_request(scene(0))


def valid_payload() -> GeneratedImagePayload:
    return LocalSceneImageGenerator().generate(request(), size=DEFAULT_IMAGE_SIZE)


@pytest.mark.parametrize("field,value", [
    ("scene_index", -1), ("scene_index", True), ("visual_type", "unknown"),
    ("visual_concept", " "), ("image_prompt", ""), ("negative_prompt", " "),
    ("aspect_ratio", "9:16"), ("overlay_text", [""]),
])
def test_request_rejects_invalid_fields(field: str, value) -> None:
    values = request().__dict__.copy()
    values[field] = value
    with pytest.raises(ValueError): YouTubeImageRequest(**values)


def test_build_request_copies_upstream_scene_without_mutation() -> None:
    built = build_image_request(scene(1))
    assert built.scene_index == 1
    assert built.image_prompt == scene(1).image_prompt
    assert built.negative_prompt == scene(1).negative_prompt
    assert built.overlay_text == scene(1).overlay_text


def test_prompt_composition_preserves_authority_and_production_constraints() -> None:
    prompt = compose_generation_prompt(build_image_request(scene(1)))
    assert scene(1).image_prompt in prompt
    assert scene(1).negative_prompt in prompt
    assert "horizontal 16:9" in prompt
    assert "long Japanese text" in prompt
    assert "clean negative space" in prompt and "typography" in prompt
    assert "Scene 1" not in prompt  # overlay metadata is not burned into the image


def test_local_generator_is_deterministic_valid_png() -> None:
    generator = LocalSceneImageGenerator()
    first = generator.generate(request(), size=DEFAULT_IMAGE_SIZE)
    second = generator.generate(request(), size=DEFAULT_IMAGE_SIZE)
    assert first == second
    assert first.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert (first.width, first.height) == (1792, 1024)
    assert first.provider == "local"


@pytest.mark.parametrize("field,value", [
    ("data", b""), ("media_type", "application/octet-stream"),
    ("width", 0), ("width", True), ("height", -1), ("height", True),
    ("provider", " "), ("model", ""), ("revised_prompt", " "),
])
def test_payload_rejects_invalid_fields(field: str, value) -> None:
    values = valid_payload().__dict__.copy()
    values[field] = value
    with pytest.raises(ValueError): GeneratedImagePayload(**values)


@pytest.mark.parametrize("width,height", [(1024, 1024), (864, 1536), (1200, 800)])
def test_payload_rejects_square_vertical_or_wrong_ratio(width: int, height: int) -> None:
    values = valid_payload().__dict__.copy()
    values.update(width=width, height=height)
    with pytest.raises(ValueError): GeneratedImagePayload(**values)


def test_media_signature_mismatch_rejected_without_retry() -> None:
    payload = replace(valid_payload(), media_type="image/jpeg")
    with pytest.raises(ValueError, match="match media_type"):
        validate_generated_payload(payload)


@pytest.mark.parametrize("index,media_type,expected", [
    (0, "image/png", "scene_000.png"),
    (12, "image/jpeg", "scene_012.jpg"),
    (3, "image/webp", "scene_003.webp"),
])
def test_deterministic_filename(index: int, media_type: str, expected: str) -> None:
    assert deterministic_scene_filename(index, media_type) == expected


def test_successful_generation_creates_files_result_and_manifest(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "output"
    result = generate_youtube_scene_images(
        plan(), LocalSceneImageGenerator(), output_directory=output
    )
    assert [asset.scene_index for asset in result.assets] == [0, 1, 2]
    assert [Path(asset.file_path).name for asset in result.assets] == [
        "scene_000.png", "scene_001.png", "scene_002.png"
    ]
    assert all(Path(asset.file_path).is_file() for asset in result.assets)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["title"] == "Video title"
    assert manifest["aspect_ratio"] == "16:9"
    assert [asset["scene_index"] for asset in manifest["assets"]] == [0, 1, 2]
    first = manifest["assets"][0]
    for key in ("visual_type", "source_refs", "file_name", "provider", "model",
                "media_type", "width", "height", "prompt_used", "overlay_text"):
        assert key in first
    raw = manifest_path.read_text()
    assert "OPENAI_API_KEY" not in raw and "Bearer" not in raw
    assert not list(output.glob(".*.tmp"))


def test_selection_is_validated_and_generated_in_source_order(tmp_path: Path) -> None:
    result = generate_youtube_scene_images(
        plan(), LocalSceneImageGenerator(), output_directory=tmp_path,
        scene_indexes=[2, 0],
    )
    assert [asset.scene_index for asset in result.assets] == [0, 2]


class CountingGenerator:
    def __init__(self, failures: int = 0, invalid: bool = False):
        self.failures = failures
        self.invalid = invalid
        self.calls = []

    def generate(self, request, *, size):
        self.calls.append(request.scene_index)
        if len(self.calls) <= self.failures: raise RuntimeError("transient")
        payload = LocalSceneImageGenerator().generate(request, size=size)
        return SimpleInvalidPayload() if self.invalid else payload


class SimpleInvalidPayload:
    data = b"invalid"
    media_type = "image/png"
    width = 1792
    height = 1024
    provider = "fake"
    model = "fake"
    revised_prompt = None


@pytest.mark.parametrize("selection", [[], [1, 1], [True], [99]])
def test_invalid_selection_fails_before_provider_call(tmp_path: Path, selection) -> None:
    generator = CountingGenerator()
    with pytest.raises(ValueError):
        generate_youtube_scene_images(
            plan(), generator, output_directory=tmp_path, scene_indexes=selection
        )
    assert generator.calls == []


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_scene_limit_fails_before_provider_call(tmp_path: Path, limit) -> None:
    generator = CountingGenerator()
    with pytest.raises(ValueError):
        generate_youtube_scene_images(
            plan(), generator, output_directory=tmp_path, scene_limit=limit
        )
    assert generator.calls == []


def test_cost_guard_fails_before_provider_call(tmp_path: Path) -> None:
    generator = CountingGenerator()
    with pytest.raises(ValueError, match="scene_limit"):
        generate_youtube_scene_images(plan(), generator, output_directory=tmp_path, scene_limit=2)
    assert generator.calls == []


def test_collision_preflight_and_explicit_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "scene_000.png"
    target.write_bytes(b"old")
    generator = CountingGenerator()
    with pytest.raises(FileExistsError):
        generate_youtube_scene_images(plan(1), generator, output_directory=tmp_path)
    assert generator.calls == [] and target.read_bytes() == b"old"
    result = generate_youtube_scene_images(
        plan(1), generator, output_directory=tmp_path, overwrite=True
    )
    assert target.read_bytes().startswith(b"\x89PNG") and len(result.assets) == 1


def test_retry_succeeds_on_third_attempt(tmp_path: Path) -> None:
    generator = CountingGenerator(failures=2)
    result = generate_youtube_scene_images(
        plan(1), generator, output_directory=tmp_path, max_retries=2
    )
    assert len(result.assets) == 1 and len(generator.calls) == 3


def test_retry_exhaustion_identifies_scene(tmp_path: Path) -> None:
    generator = CountingGenerator(failures=99)
    with pytest.raises(SceneImageGenerationError, match="scene 0") as error:
        generate_youtube_scene_images(
            plan(1), generator, output_directory=tmp_path, max_retries=2
        )
    assert error.value.scene_index == 0
    assert len(generator.calls) == 3
    assert not (tmp_path / "manifest.json").exists()


def test_invalid_payload_is_not_retried(tmp_path: Path) -> None:
    generator = CountingGenerator(invalid=True)
    with pytest.raises(ValueError):
        generate_youtube_scene_images(plan(1), generator, output_directory=tmp_path)
    assert generator.calls == [0]


def test_partial_failure_keeps_prior_file_without_manifest(tmp_path: Path) -> None:
    class Partial:
        def generate(self, request, *, size):
            if request.scene_index == 1: raise RuntimeError("failed")
            return LocalSceneImageGenerator().generate(request, size=size)
    with pytest.raises(SceneImageGenerationError, match="scene 1"):
        generate_youtube_scene_images(
            plan(2), Partial(), output_directory=tmp_path, max_retries=0
        )
    assert (tmp_path / "scene_000.png").exists()
    assert not (tmp_path / "manifest.json").exists()


@pytest.mark.parametrize("max_retries", [-1, True, 1.5])
def test_invalid_retry_config_rejected(tmp_path: Path, max_retries) -> None:
    with pytest.raises(ValueError):
        generate_youtube_scene_images(
            plan(), LocalSceneImageGenerator(), output_directory=tmp_path,
            max_retries=max_retries,
        )

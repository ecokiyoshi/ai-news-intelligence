"""Provider-independent scene image generation and filesystem orchestration."""

import json
import math
import os
import struct
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.youtube_visuals import (
    ASPECT_RATIO,
    SUPPORTED_VISUAL_TYPES,
    YouTubeVisualPlan,
    YouTubeVisualScene,
)

DEFAULT_IMAGE_SIZE = "1792x1024"
DEFAULT_SCENE_LIMIT = 50
DEFAULT_MAX_RETRIES = 2
DEFAULT_ASPECT_RATIO_TOLERANCE = 0.03
MANIFEST_FILENAME = "manifest.json"
SUPPORTED_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
MEDIA_TYPE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


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


def _optional_text(name: str, value: str | None) -> str | None:
    return None if value is None else _required_text(name, value)


def _text_list(name: str, values: list[str]) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{name} must be a list")
    return [_required_text(f"{name} item", value) for value in values]


def parse_image_size(size: str) -> tuple[int, int]:
    size = _required_text("size", size)
    parts = size.split("x")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError("size must use WIDTHxHEIGHT format")
    return _positive_integer("width", int(parts[0])), _positive_integer(
        "height", int(parts[1])
    )


def _validate_dimensions(
    width: int,
    height: int,
    tolerance: float = DEFAULT_ASPECT_RATIO_TOLERANCE,
) -> tuple[int, int]:
    width = _positive_integer("width", width)
    height = _positive_integer("height", height)
    if width <= height:
        raise ValueError("generated image must be horizontal")
    if not math.isclose(width / height, 16 / 9, abs_tol=tolerance):
        raise ValueError("generated image dimensions must approximate 16:9")
    return width, height


@dataclass(frozen=True)
class YouTubeImageRequest:
    scene_index: int
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
        visual_type = _required_text("visual_type", self.visual_type)
        if visual_type not in SUPPORTED_VISUAL_TYPES:
            raise ValueError("visual_type is not supported")
        object.__setattr__(self, "visual_type", visual_type)
        for name in ("visual_concept", "image_prompt", "negative_prompt"):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
        if self.aspect_ratio != ASPECT_RATIO:
            raise ValueError(f"aspect_ratio must be {ASPECT_RATIO}")
        object.__setattr__(self, "overlay_text", _text_list("overlay_text", self.overlay_text))


@dataclass(frozen=True)
class GeneratedImagePayload:
    data: bytes
    media_type: str
    width: int
    height: int
    provider: str
    model: str
    revised_prompt: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("data must be non-empty bytes")
        if self.media_type not in SUPPORTED_MEDIA_TYPES:
            raise ValueError("media_type is not supported")
        width, height = _validate_dimensions(self.width, self.height)
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "provider", _required_text("provider", self.provider))
        object.__setattr__(self, "model", _required_text("model", self.model))
        object.__setattr__(
            self, "revised_prompt", _optional_text("revised_prompt", self.revised_prompt)
        )


@dataclass(frozen=True)
class GeneratedSceneImage:
    scene_index: int
    provider: str
    model: str
    file_path: str
    media_type: str
    width: int
    height: int
    prompt_used: str
    revised_prompt: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "scene_index", _nonnegative_integer("scene_index", self.scene_index)
        )
        object.__setattr__(self, "provider", _required_text("provider", self.provider))
        object.__setattr__(self, "model", _required_text("model", self.model))
        object.__setattr__(self, "file_path", _required_text("file_path", self.file_path))
        if self.media_type not in SUPPORTED_MEDIA_TYPES:
            raise ValueError("media_type is not supported")
        width, height = _validate_dimensions(self.width, self.height)
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)
        object.__setattr__(
            self, "prompt_used", _required_text("prompt_used", self.prompt_used)
        )
        object.__setattr__(
            self, "revised_prompt", _optional_text("revised_prompt", self.revised_prompt)
        )


@dataclass(frozen=True)
class YouTubeImageGenerationResult:
    title: str
    output_directory: str
    assets: list[GeneratedSceneImage]

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _required_text("title", self.title))
        object.__setattr__(
            self,
            "output_directory",
            _required_text("output_directory", self.output_directory),
        )
        if not isinstance(self.assets, list) or not self.assets:
            raise ValueError("assets must be a non-empty list")
        assets = []
        for asset in self.assets:
            if not isinstance(asset, GeneratedSceneImage):
                raise ValueError("assets must contain GeneratedSceneImage values")
            assets.append(GeneratedSceneImage(**asset.__dict__))
        indexes = [asset.scene_index for asset in assets]
        if len(set(indexes)) != len(indexes):
            raise ValueError("asset scene indexes must be unique")
        if indexes != sorted(indexes):
            raise ValueError("assets must preserve visual plan order")
        object.__setattr__(self, "assets", assets)


class SceneImageGenerator(Protocol):
    def generate(
        self, request: YouTubeImageRequest, *, size: str
    ) -> GeneratedImagePayload: ...


class SceneImageGenerationError(RuntimeError):
    def __init__(self, scene_index: int, attempts: int) -> None:
        self.scene_index = scene_index
        self.attempts = attempts
        super().__init__(
            f"image generation failed for scene {scene_index} after {attempts} attempts"
        )


def validate_image_request(request: YouTubeImageRequest) -> YouTubeImageRequest:
    if not isinstance(request, YouTubeImageRequest):
        raise ValueError("request must be YouTubeImageRequest")
    return YouTubeImageRequest(**request.__dict__)


def build_image_request(scene: YouTubeVisualScene) -> YouTubeImageRequest:
    if not isinstance(scene, YouTubeVisualScene):
        raise ValueError("scene must be YouTubeVisualScene")
    scene = YouTubeVisualScene(**scene.__dict__)
    return YouTubeImageRequest(
        scene_index=scene.scene_index,
        visual_type=scene.visual_type,
        visual_concept=scene.visual_concept,
        image_prompt=scene.image_prompt,
        negative_prompt=scene.negative_prompt,
        aspect_ratio=scene.aspect_ratio,
        overlay_text=scene.overlay_text,
    )


def compose_generation_prompt(request: YouTubeImageRequest) -> str:
    """Keep the upstream prompt authoritative and add deterministic production constraints."""

    request = validate_image_request(request)
    typography = (
        "Leave clean negative space for later overlay typography; do not render the supplied "
        "overlay text into the image."
        if request.overlay_text
        else "Do not render long Japanese text into the image."
    )
    return (
        f"{request.image_prompt}\n\n"
        "Composition requirements:\n"
        "- horizontal 16:9 YouTube composition\n"
        "- clear focal subject and no vertical framing\n\n"
        f"Avoid:\n- {request.negative_prompt}\n\n"
        f"Typography:\n- do not render long Japanese text or garbled letters\n- {typography}"
    )


def _validate_signature(data: bytes, media_type: str) -> None:
    valid = {
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": data.startswith(b"\xff\xd8"),
        "image/webp": len(data) >= 12
        and data.startswith(b"RIFF")
        and data[8:12] == b"WEBP",
    }[media_type]
    if not valid:
        raise ValueError("image bytes do not match media_type")


def validate_generated_payload(payload: GeneratedImagePayload) -> GeneratedImagePayload:
    if not isinstance(payload, GeneratedImagePayload):
        raise ValueError("generator must return GeneratedImagePayload")
    payload = GeneratedImagePayload(**payload.__dict__)
    _validate_signature(payload.data, payload.media_type)
    return payload


def validate_generated_asset(asset: GeneratedSceneImage) -> GeneratedSceneImage:
    if not isinstance(asset, GeneratedSceneImage):
        raise ValueError("asset must be GeneratedSceneImage")
    return GeneratedSceneImage(**asset.__dict__)


def deterministic_scene_filename(scene_index: int, media_type: str) -> str:
    scene_index = _nonnegative_integer("scene_index", scene_index)
    if media_type not in MEDIA_TYPE_EXTENSIONS:
        raise ValueError("media_type is not supported")
    return f"scene_{scene_index:03d}{MEDIA_TYPE_EXTENSIONS[media_type]}"


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _solid_png(width: int, height: int) -> bytes:
    row = b"\x00" + b"\x20\x40\x60" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(row * height, 9))
        + _png_chunk(b"IEND", b"")
    )


class LocalSceneImageGenerator:
    """Deterministic offline generator producing a valid solid-color PNG."""

    def generate(
        self, request: YouTubeImageRequest, *, size: str
    ) -> GeneratedImagePayload:
        validate_image_request(request)
        width, height = parse_image_size(size)
        return GeneratedImagePayload(
            data=_solid_png(width, height),
            media_type="image/png",
            width=width,
            height=height,
            provider="local",
            model="deterministic-solid-png",
        )


def _validate_plan(plan: YouTubeVisualPlan) -> YouTubeVisualPlan:
    if not isinstance(plan, YouTubeVisualPlan):
        raise ValueError("visual_plan must be YouTubeVisualPlan")
    plan = YouTubeVisualPlan(**plan.__dict__)
    scenes = []
    for scene in plan.scenes:
        if not isinstance(scene, YouTubeVisualScene):
            raise ValueError("visual plan must contain YouTubeVisualScene values")
        scenes.append(YouTubeVisualScene(**scene.__dict__))
    if [scene.scene_index for scene in scenes] != list(range(len(scenes))):
        raise ValueError("visual plan scene indexes must be sequential from zero")
    return YouTubeVisualPlan(title=plan.title, aspect_ratio=plan.aspect_ratio, scenes=scenes)


def _selected_scenes(
    plan: YouTubeVisualPlan, scene_indexes: list[int] | None, scene_limit: int
) -> list[YouTubeVisualScene]:
    limit = _positive_integer("scene_limit", scene_limit)
    available = {scene.scene_index for scene in plan.scenes}
    if scene_indexes is None:
        selected = available
    else:
        if not isinstance(scene_indexes, list) or not scene_indexes:
            raise ValueError("scene_indexes must be None or a non-empty list")
        normalized = [
            _nonnegative_integer("scene index", scene_index)
            for scene_index in scene_indexes
        ]
        if len(set(normalized)) != len(normalized):
            raise ValueError("scene_indexes must not contain duplicates")
        if not set(normalized) <= available:
            raise ValueError("scene_indexes contains an unknown scene")
        selected = set(normalized)
    scenes = [scene for scene in plan.scenes if scene.scene_index in selected]
    if len(scenes) > limit:
        raise ValueError("selected scene count exceeds scene_limit")
    return scenes


def _atomic_write(path: Path, data: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
            temporary_path = Path(file.name)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _manifest(plan: YouTubeVisualPlan, assets: list[GeneratedSceneImage]) -> bytes:
    scenes = {scene.scene_index: scene for scene in plan.scenes}
    content = {
        "title": plan.title,
        "aspect_ratio": plan.aspect_ratio,
        "assets": [
            {
                "scene_index": asset.scene_index,
                "visual_type": scenes[asset.scene_index].visual_type,
                "source_refs": [reference.__dict__ for reference in scenes[asset.scene_index].source_refs],
                "file_name": Path(asset.file_path).name,
                "provider": asset.provider,
                "model": asset.model,
                "media_type": asset.media_type,
                "width": asset.width,
                "height": asset.height,
                "prompt_used": asset.prompt_used,
                "revised_prompt": asset.revised_prompt,
                "overlay_text": scenes[asset.scene_index].overlay_text,
            }
            for asset in assets
        ],
    }
    return (json.dumps(content, ensure_ascii=False, indent=2) + "\n").encode()


def generate_youtube_scene_images(
    visual_plan: YouTubeVisualPlan,
    generator: SceneImageGenerator,
    *,
    output_directory: Path,
    size: str = DEFAULT_IMAGE_SIZE,
    scene_indexes: list[int] | None = None,
    scene_limit: int = DEFAULT_SCENE_LIMIT,
    overwrite: bool = False,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> YouTubeImageGenerationResult:
    """Generate selected scenes; provider errors retry, validation failures do not."""

    plan = _validate_plan(visual_plan)
    width, height = parse_image_size(size)
    _validate_dimensions(width, height)
    scenes = _selected_scenes(plan, scene_indexes, scene_limit)
    if not isinstance(output_directory, Path):
        raise ValueError("output_directory must be pathlib.Path")
    if isinstance(overwrite, bool) is False:
        raise ValueError("overwrite must be a bool")
    retries = _nonnegative_integer("max_retries", max_retries)
    if output_directory.exists() and not output_directory.is_dir():
        raise ValueError("output_directory exists and is not a directory")
    output_directory.mkdir(parents=True, exist_ok=True)
    manifest_path = output_directory / MANIFEST_FILENAME
    possible_targets = [
        output_directory / f"scene_{scene.scene_index:03d}{extension}"
        for scene in scenes
        for extension in MEDIA_TYPE_EXTENSIONS.values()
    ]
    if not overwrite and (manifest_path.exists() or any(path.exists() for path in possible_targets)):
        raise FileExistsError("output image or manifest already exists")
    if overwrite:
        manifest_path.unlink(missing_ok=True)

    assets = []
    for scene in scenes:
        request = build_image_request(scene)
        prompt = compose_generation_prompt(request)
        provider_request = YouTubeImageRequest(
            **{**request.__dict__, "image_prompt": prompt}
        )
        attempts = 0
        while True:
            attempts += 1
            try:
                payload = generator.generate(provider_request, size=size)
                break
            except Exception as error:
                if attempts > retries:
                    raise SceneImageGenerationError(scene.scene_index, attempts) from error
        payload = validate_generated_payload(payload)
        target_path = output_directory / deterministic_scene_filename(
            scene.scene_index, payload.media_type
        )
        _atomic_write(target_path, payload.data)
        assets.append(
            GeneratedSceneImage(
                scene_index=scene.scene_index,
                provider=payload.provider,
                model=payload.model,
                file_path=str(target_path),
                media_type=payload.media_type,
                width=payload.width,
                height=payload.height,
                prompt_used=prompt,
                revised_prompt=payload.revised_prompt,
            )
        )
    result = YouTubeImageGenerationResult(
        title=plan.title,
        output_directory=str(output_directory),
        assets=assets,
    )
    if not all(Path(asset.file_path).is_file() for asset in result.assets):
        raise RuntimeError("generated asset file is missing")
    _atomic_write(manifest_path, _manifest(plan, result.assets))
    return result

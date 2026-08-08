"""OpenAI Images API provider for scene image bytes and metadata only."""

import base64
import os
from typing import Any, Protocol

from openai import OpenAI

from app.youtube_image_generation import (
    GeneratedImagePayload,
    YouTubeImageRequest,
    parse_image_size,
    validate_image_request,
)

DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-2"
OPENAI_SUPPORTED_IMAGE_SIZES = frozenset({"1792x1024"})


class ImagesGenerator(Protocol):
    def generate(
        self,
        *,
        model: str,
        prompt: str,
        size: str,
        n: int,
        output_format: str,
        response_format: str,
    ) -> Any: ...


class OpenAIImageClient(Protocol):
    images: ImagesGenerator


class OpenAISceneImageGenerator:
    """Generate PNG bytes via the installed SDK's client.images.generate interface."""

    def __init__(
        self,
        *,
        client: OpenAIImageClient | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.client = client if client is not None else OpenAI(timeout=timeout)
        self.model = model or os.getenv("OPENAI_IMAGE_MODEL") or DEFAULT_OPENAI_IMAGE_MODEL

    def generate(
        self, request: YouTubeImageRequest, *, size: str
    ) -> GeneratedImagePayload:
        request = validate_image_request(request)
        if size not in OPENAI_SUPPORTED_IMAGE_SIZES:
            raise ValueError(
                f"OpenAI image provider supports these project sizes: "
                f"{sorted(OPENAI_SUPPORTED_IMAGE_SIZES)}"
            )
        width, height = parse_image_size(size)
        response = self.client.images.generate(
            model=self.model,
            prompt=request.image_prompt,
            size=size,
            n=1,
            output_format="png",
            response_format="b64_json",
        )
        data_items = getattr(response, "data", None)
        if not data_items:
            raise ValueError("OpenAI image response did not contain image data")
        image = data_items[0]
        encoded = getattr(image, "b64_json", None)
        if not isinstance(encoded, str) or not encoded.strip():
            raise ValueError("OpenAI image response did not contain base64 image data")
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except Exception as error:
            raise ValueError("OpenAI image response contained invalid base64 data") from error
        if not image_bytes:
            raise ValueError("OpenAI image response contained empty image data")
        revised_prompt = getattr(image, "revised_prompt", None)
        return GeneratedImagePayload(
            data=image_bytes,
            media_type="image/png",
            width=width,
            height=height,
            provider="openai",
            model=self.model,
            revised_prompt=revised_prompt,
        )

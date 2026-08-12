"""Small ElevenLabs REST client with bounded retry and secret-safe errors."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class ElevenLabsError(RuntimeError):
    pass


@dataclass
class ElevenLabsClient:
    api_key: str
    model_id: str = "eleven_v3"
    output_format: str = "mp3_44100_128"
    timeout: float = 60.0
    max_attempts: int = 3
    language_code: str = "ja"

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("ELEVENLABS_API_KEY is required")

    def get_voice_settings(self, voice_id: str) -> dict[str, object]:
        """Load the settings saved for a voice in the ElevenLabs web app."""
        url = "https://api.elevenlabs.io/v1/voices/" + urllib.parse.quote(voice_id, safe="") + "/settings"
        request = urllib.request.Request(url, headers={"xi-api-key": self.api_key})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ElevenLabsError("Could not load ElevenLabs voice settings") from error
        keys = {"stability", "similarity_boost", "style", "use_speaker_boost", "speed"}
        return {key: value[key] for key in keys if value.get(key) is not None}

    def synthesize(
        self, voice_id: str, text: str, *, voice_settings: dict[str, object] | None = None,
        previous_text: str | None = None, next_text: str | None = None,
    ) -> bytes:
        if not voice_id.strip():
            raise ValueError("ElevenLabs voice ID is required")
        if not text.strip():
            raise ValueError("speech text must not be empty")
        url = "https://api.elevenlabs.io/v1/text-to-speech/" + urllib.parse.quote(voice_id, safe="")
        url += "?output_format=" + urllib.parse.quote(self.output_format, safe="")
        payload: dict[str, object] = {
            "text": text,
            "model_id": self.model_id,
            "apply_text_normalization": "on",
        }
        if voice_settings:
            payload["voice_settings"] = voice_settings
        if self.model_id == "eleven_v3":
            # v3 rejects context and Japanese-specific normalization at the API layer.
            payload["language_code"] = self.language_code
        else:
            payload["apply_language_text_normalization"] = True
            if previous_text:
                payload["previous_text"] = previous_text
            if next_text:
                payload["next_text"] = next_text
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"xi-api-key": self.api_key, "content-type": "application/json", "accept": "audio/mpeg"},
        )
        retryable = {429, 500, 502, 503, 504}
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    audio = response.read()
                if not audio:
                    raise ElevenLabsError("ElevenLabs returned empty audio")
                return audio
            except urllib.error.HTTPError as error:
                if error.code not in retryable or attempt == self.max_attempts:
                    raise ElevenLabsError(f"ElevenLabs request failed with HTTP {error.code}") from error
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == self.max_attempts:
                    raise ElevenLabsError("ElevenLabs request timed out or could not connect") from error
            time.sleep((2 ** (attempt - 1)) + random.uniform(0, 0.25))
        raise ElevenLabsError("ElevenLabs request failed")

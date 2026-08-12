"""Project-level resumable audio generation orchestration."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from app.audio.audio_merger import merge_audio
from app.audio.dialogue_parser import lines_from_run
from app.audio.elevenlabs_client import ElevenLabsClient
from app.audio.normalization import normalize_japanese_tts

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioGenerationResult:
    project_id: str
    total_segments: int
    generated_segments: int
    reused_segments: int
    failed_segments: int
    audio_directory: str
    manifest_path: str
    merged_audio_path: str | None


def _safe_project(root: Path, project_id: str) -> Path:
    if not project_id or Path(project_id).name != project_id or project_id in {".", ".."}:
        raise ValueError("invalid project ID")
    candidate = (root.resolve() / project_id).resolve()
    if candidate.parent != root.resolve():
        raise ValueError("invalid project ID")
    return candidate


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def generate_project_audio(
    project_id: str, *, output_root: Path | str | None = None, force: bool = False,
    merge: bool = False, environ: Mapping[str, str] | None = None,
    client_factory: Callable[..., ElevenLabsClient] = ElevenLabsClient,
) -> AudioGenerationResult:
    values = os.environ if environ is None else environ
    root = Path(output_root or values.get("OUTPUT_DIR", "generated-outputs")).expanduser()
    project = _safe_project(root, project_id)
    run_path = project / "run.json"
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"project not found: {project_id}") from error
    lines = lines_from_run(run.get("dialogue"))
    api_key = values.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY is required")
    voice_map = {
        "sabisuke": values.get("ELEVENLABS_SABISUKE_VOICE_ID", "").strip(),
        "haru": values.get("ELEVENLABS_HARU_VOICE_ID", "").strip(),
    }
    missing = [speaker for speaker, voice_id in voice_map.items() if not voice_id]
    if missing:
        raise ValueError("ElevenLabs voice ID is required for: " + ", ".join(missing))
    model = values.get("ELEVENLABS_MODEL_ID", "eleven_v3")
    output_format = values.get("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")
    timeout = float(values.get("ELEVENLABS_TIMEOUT_SECONDS", "60"))
    client = client_factory(
        api_key=api_key, model_id=model, output_format=output_format, timeout=timeout,
        language_code=values.get("ELEVENLABS_LANGUAGE_CODE", "ja"),
    )
    voice_settings = {
        speaker: client.get_voice_settings(voice_id)
        for speaker, voice_id in voice_map.items()
    }
    audio_dir = project / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = audio_dir / "manifest.json"
    try:
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        old = {}
    cached = {item.get("cache_key"): item for item in old.get("segments", []) if isinstance(item, dict)}
    manifest = {"project_id": project_id, "provider": "elevenlabs", "model_id": model, "output_format": output_format, "status": "generating", "segments": []}
    generated = reused = failed = 0
    failure: Exception | None = None
    width = max(3, len(str(len(lines))))
    for line in lines:
        use_map = values.get("ELEVENLABS_USE_PRONUNCIATION_MAP", "false").lower() in {"1", "true", "yes", "on"}
        normalized = normalize_japanese_tts(line.text) if use_map else line.text
        cache_key = hashlib.sha256(f"{line.speaker}\0{normalized}\0{model}\0{output_format}".encode()).hexdigest()
        filename = f"{line.index:0{width}d}_{line.speaker}.mp3"
        path = audio_dir / filename
        previous = cached.get(cache_key)
        segment = {"index": line.index, "speaker": line.speaker, "display_name": line.display_name, "text": line.text, "normalized_text": normalized, "file": filename, "cache_key": cache_key}
        if (
            not force
            and previous
            and previous.get("file") == filename
            and path.is_file()
            and path.stat().st_size
        ):
            logger.info("Reusing cached audio segment %s/%s", line.index, len(lines))
            reused += 1
        else:
            logger.info("Generating audio segment %s/%s: %s", line.index, len(lines), line.speaker)
            try:
                position = line.index - 1
                previous_text = lines[position - 1].text if position else None
                next_text = lines[position + 1].text if position + 1 < len(lines) else None
                audio = client.synthesize(
                    voice_map[line.speaker], normalized,
                    voice_settings=voice_settings[line.speaker],
                    previous_text=previous_text, next_text=next_text,
                )
                temporary = path.with_suffix(".mp3.tmp")
                temporary.write_bytes(audio)
                temporary.replace(path)
                generated += 1
            except Exception as error:
                segment["error"] = type(error).__name__
                failed += 1
                failure = error
        manifest["segments"].append(segment)
        _write_json(manifest_path, manifest)
        if failure:
            break
    merged_path = None
    if not failure and merge:
        gap = int(values.get("ELEVENLABS_DIALOGUE_GAP_MS", "200"))
        merged = merge_audio([audio_dir / item["file"] for item in manifest["segments"]], audio_dir / "dialogue_full.mp3", gap)
        merged_path = str(merged)
    manifest["status"] = "failed" if failure else "completed"
    manifest["merged_audio"] = Path(merged_path).name if merged_path else None
    _write_json(manifest_path, manifest)
    result = AudioGenerationResult(project_id, len(lines), generated, reused, failed, str(audio_dir), str(manifest_path), merged_path)
    if failure:
        raise RuntimeError(f"audio generation failed after {generated + reused} segments") from failure
    logger.info("Audio generation completed project_id=%s", project_id)
    return result

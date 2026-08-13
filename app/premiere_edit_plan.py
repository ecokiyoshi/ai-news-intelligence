"""Build a deterministic, secret-safe Premiere edit plan from completed run artifacts."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import wave
from pathlib import Path
from typing import Any

SCHEMA = "ai-news-intelligence/premiere-edit-plan"
SCHEMA_VERSION = 1
OUTPUT_FILENAME = "premiere-edit-plan.json"
SPEAKER_ROLES = {"sabisuke": "dialogue.sabisuke", "haru": "dialogue.haru"}
SPEAKER_ALIASES = {"さび助": "sabisuke", "ハル": "haru", **{key: key for key in SPEAKER_ROLES}}


def _object(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _list(name: str, value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _index(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _safe_relative(run_dir: Path, value: Any, *, must_exist: bool = True) -> str:
    raw = _text("asset path", value)
    candidate = Path(raw)
    resolved = candidate.resolve() if candidate.is_absolute() else (run_dir / candidate).resolve()
    try:
        relative = resolved.relative_to(run_dir.resolve())
    except ValueError as error:
        raise ValueError(f"asset path escapes the run directory: {raw}") from error
    if must_exist and not resolved.is_file():
        raise ValueError(f"referenced asset is missing: {relative.as_posix()}")
    return relative.as_posix()


def _dialogue_map(dialogue: dict[str, Any]) -> tuple[dict[tuple[str, int | None, int], dict[str, Any]], list[dict[str, Any]]]:
    mapped: dict[tuple[str, int | None, int], dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []

    def add(section: str, chapter_index: int | None, raw_lines: Any) -> None:
        for position, raw in enumerate(_list(f"dialogue {section} lines", raw_lines)):
            line = _object("dialogue line", raw)
            line_index = _index("dialogue line_index", line.get("line_index", position))
            speaker_raw = _text("dialogue speaker", line.get("speaker"))
            speaker = SPEAKER_ALIASES.get(speaker_raw)
            if speaker is None:
                raise ValueError(f"unsupported dialogue speaker: {speaker_raw}")
            item = {
                "segment_index": len(ordered) + 1,
                "source_ref": {"section": section, "chapter_index": chapter_index, "line_index": line_index},
                "speaker": speaker,
                "display_name": "さび助" if speaker == "sabisuke" else "ハル",
                "text": _text("dialogue text", line.get("text")),
            }
            key = (section, chapter_index, line_index)
            if key in mapped:
                raise ValueError(f"duplicate dialogue reference: {key}")
            mapped[key] = item
            ordered.append(item)

    add("opening", None, dialogue.get("opening_lines", []))
    for chapter_position, raw_chapter in enumerate(_list("dialogue chapters", dialogue.get("chapters", []))):
        chapter = _object("dialogue chapter", raw_chapter)
        chapter_index = _index("chapter_index", chapter.get("chapter_index", chapter_position))
        add("chapter", chapter_index, chapter.get("lines", []))
    add("closing", None, dialogue.get("closing_lines", []))
    if not ordered:
        raise ValueError("dialogue contains no lines")
    return mapped, ordered


def _duration_from_file(path: Path) -> float | None:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as audio:
                return audio.getnframes() / audio.getframerate()
        except (wave.Error, OSError, ZeroDivisionError):
            return None
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        duration = float(completed.stdout.strip())
        return duration if math.isfinite(duration) and duration > 0 else None
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, OSError):
        return None


def _estimated_duration(text: str) -> float:
    return round(max(1.0, len("".join(text.split())) / 7.0), 3)


def _audio_segments(run_dir: Path, ordered: list[dict[str, Any]], *, require_audio: bool) -> dict[int, dict[str, Any]]:
    manifest_path = run_dir / "audio" / "manifest.json"
    if not manifest_path.is_file():
        if require_audio:
            raise ValueError("audio/manifest.json is required")
        return {}
    manifest = _object("audio manifest", json.loads(manifest_path.read_text(encoding="utf-8")))
    segments = _list("audio manifest segments", manifest.get("segments", []))
    result: dict[int, dict[str, Any]] = {}
    for raw in segments:
        segment = _object("audio segment", raw)
        number = _index("audio segment index", segment.get("index"))
        if number == 0 or number > len(ordered) or number in result:
            raise ValueError("audio segment indexes must uniquely reference dialogue segments")
        manifest_speaker = SPEAKER_ALIASES.get(str(segment.get("speaker", ordered[number - 1]["speaker"])).strip())
        if manifest_speaker != ordered[number - 1]["speaker"]:
            raise ValueError(f"audio speaker does not match dialogue segment {number}")
        path = _safe_relative(run_dir, f"audio/{_text('audio file', segment.get('file'))}")
        duration = segment.get("duration_seconds")
        if duration is None and isinstance(segment.get("duration_ms"), (int, float)):
            duration = segment["duration_ms"] / 1000
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0:
            duration = _duration_from_file(run_dir / path)
        source = "audio" if duration else "estimate"
        duration = round(duration or _estimated_duration(ordered[number - 1]["text"]), 3)
        result[number] = {"asset": path, "duration_seconds": duration, "timing_source": source}
    if require_audio and len(result) != len(ordered):
        raise ValueError("audio manifest must contain every dialogue segment")
    return result


def build_premiere_edit_plan(run_directory: Path | str, *, require_audio: bool = False) -> dict[str, Any]:
    """Validate a completed run directory and return a deterministic edit plan."""
    run_dir = Path(run_directory).resolve()
    run = _object("run.json", json.loads((run_dir / "run.json").read_text(encoding="utf-8")))
    image_manifest = _object("image manifest", json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")))
    run_id = _text("run_id", run.get("run_id"))
    visual_plan = _object("visual_plan", run.get("visual_plan"))
    if visual_plan.get("aspect_ratio") != "16:9" or image_manifest.get("aspect_ratio") != "16:9":
        raise ValueError("Premiere edit plans require 16:9 visual artifacts")
    raw_scenes = _list("visual_plan scenes", visual_plan.get("scenes"))
    indexes = [_index("scene_index", _object("scene", item).get("scene_index")) for item in raw_scenes]
    if indexes != list(range(len(raw_scenes))):
        raise ValueError("scene indexes must be unique and sequential from zero")
    images: dict[int, str] = {}
    for raw in _list("image manifest assets", image_manifest.get("assets")):
        asset = _object("image asset", raw)
        scene_index = _index("image scene_index", asset.get("scene_index"))
        if scene_index in images:
            raise ValueError("duplicate image scene index")
        images[scene_index] = _safe_relative(run_dir, asset.get("file_name") or asset.get("file_path"))
    if set(images) != set(indexes):
        raise ValueError("every scene must have exactly one image")

    dialogue_by_ref, ordered = _dialogue_map(_object("dialogue", run.get("dialogue")))
    audio = _audio_segments(run_dir, ordered, require_audio=require_audio)
    used_refs: set[tuple[str, int | None, int]] = set()
    cursor = 0.0
    scenes: list[dict[str, Any]] = []
    for raw in raw_scenes:
        scene = _object("scene", raw)
        scene_index = scene["scene_index"]
        dialogue_segments = []
        scene_cursor = cursor
        for raw_ref in _list("scene source_refs", scene.get("source_refs")):
            ref = _object("source reference", raw_ref)
            key = (ref.get("section"), ref.get("chapter_index"), ref.get("line_index"))
            if key in used_refs:
                raise ValueError(f"dialogue reference assigned to multiple scenes: {key}")
            try:
                source = dialogue_by_ref[key]
            except KeyError as error:
                raise ValueError(f"scene references unknown dialogue: {key}") from error
            used_refs.add(key)
            timing = audio.get(source["segment_index"])
            duration = timing["duration_seconds"] if timing else _estimated_duration(source["text"])
            dialogue_segments.append({
                **source,
                "track_role": SPEAKER_ROLES[source["speaker"]],
                "asset": timing["asset"] if timing else None,
                "start_seconds": round(scene_cursor, 3),
                "duration_seconds": duration,
                "timing_source": timing["timing_source"] if timing else "estimate",
            })
            scene_cursor += duration
        duration = round(max(1.0, scene_cursor - cursor), 3)
        scenes.append({
            "scene_index": scene_index,
            "start_seconds": round(cursor, 3),
            "duration_seconds": duration,
            "image": {"asset": images[scene_index], "track_role": "video.scene"},
            "dialogue": dialogue_segments,
            "overlay_text": list(scene.get("overlay_text") or []),
            "source_refs": list(scene.get("source_refs") or []),
        })
        cursor += duration
    if used_refs != set(dialogue_by_ref):
        missing = sorted(set(dialogue_by_ref) - used_refs, key=str)
        raise ValueError(f"visual scenes do not cover every dialogue line: {missing[:3]}")

    plan = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "run": {"id": run_id, "title": _text("title", visual_plan.get("title"))},
        "sequence": {
            "name": f"AINI-{run_id}", "aspect_ratio": "16:9", "width": 1920, "height": 1080,
            "frame_rate": 30, "duration_seconds": round(cursor, 3),
        },
        "track_roles": {
            "video.scene": {"media_type": "video", "preferred_index": 0},
            "dialogue.sabisuke": {"media_type": "audio", "preferred_index": 0},
            "dialogue.haru": {"media_type": "audio", "preferred_index": 1},
            "captions.overlay": {"media_type": "metadata", "preferred_index": None},
        },
        "reserved_track_roles": ["audio.bgm", "audio.sfx", "video.transitions", "video.motion_graphics"],
        "idempotency": {"strategy": "refuse_existing_sequence", "generated_sequence_name": f"AINI-{run_id}"},
        "captions": {"mode": "sidecar_metadata", "reason": "Premiere Pro UXP 25.6 has no documented caption-creation mutation API."},
        "source": {"run": "run.json", "images": "manifest.json", "audio": "audio/manifest.json" if audio else None},
        "scenes": scenes,
    }
    return plan


def write_premiere_edit_plan(run_directory: Path | str, output: Path | str | None = None, *, require_audio: bool = False) -> Path:
    run_dir = Path(run_directory).resolve()
    target = Path(output).resolve() if output else run_dir / OUTPUT_FILENAME
    plan = build_premiere_edit_plan(run_dir, require_audio=require_audio)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-audio", action="store_true")
    args = parser.parse_args()
    print(write_premiere_edit_plan(args.run_directory, args.output, require_audio=args.require_audio))


if __name__ == "__main__":
    main()

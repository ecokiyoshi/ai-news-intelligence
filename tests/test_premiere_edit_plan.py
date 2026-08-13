import json
from pathlib import Path

import pytest

from app.premiere_edit_plan import build_premiere_edit_plan, write_premiere_edit_plan


def completed_run(tmp_path: Path) -> Path:
    run = tmp_path / "run-77"
    (run / "audio").mkdir(parents=True)
    for name in ("scene_000.png", "scene_001.png"):
        (run / name).write_bytes(b"png")
    for name in ("001_sabisuke.mp3", "002_haru.mp3"):
        (run / "audio" / name).write_bytes(b"mp3")
    dialogue = {
        "opening_lines": [{"line_index": 0, "speaker": "さび助", "text": "最初の話です"}],
        "chapters": [{"chapter_index": 0, "lines": [{"line_index": 0, "speaker": "ハル", "text": "次の話だね"}]}],
        "closing_lines": [],
    }
    scenes = [
        {"scene_index": 0, "source_refs": [{"section": "opening", "chapter_index": None, "line_index": 0}], "overlay_text": ["導入"]},
        {"scene_index": 1, "source_refs": [{"section": "chapter", "chapter_index": 0, "line_index": 0}], "overlay_text": []},
    ]
    (run / "run.json").write_text(json.dumps({
        "run_id": "run-77", "visual_plan": {"title": "Premiereテスト", "aspect_ratio": "16:9", "scenes": scenes},
        "dialogue": dialogue, "providers": {"unsafe": {"api_key": "must-not-leak"}},
    }, ensure_ascii=False), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({
        "aspect_ratio": "16:9", "assets": [
            {"scene_index": 1, "file_name": "scene_001.png"}, {"scene_index": 0, "file_name": "scene_000.png"}
        ]
    }), encoding="utf-8")
    (run / "audio" / "manifest.json").write_text(json.dumps({"segments": [
        {"index": 1, "file": "001_sabisuke.mp3", "duration_ms": 1200},
        {"index": 2, "file": "002_haru.mp3", "duration_ms": 2300},
    ]}), encoding="utf-8")
    return run


def test_builds_stable_ordered_secret_safe_plan(tmp_path: Path) -> None:
    run = completed_run(tmp_path)
    first = build_premiere_edit_plan(run, require_audio=True)
    second = build_premiere_edit_plan(run, require_audio=True)
    assert first == second
    assert [scene["scene_index"] for scene in first["scenes"]] == [0, 1]
    assert [scene["image"]["asset"] for scene in first["scenes"]] == ["scene_000.png", "scene_001.png"]
    assert [scene["dialogue"][0]["speaker"] for scene in first["scenes"]] == ["sabisuke", "haru"]
    assert [scene["dialogue"][0]["track_role"] for scene in first["scenes"]] == ["dialogue.sabisuke", "dialogue.haru"]
    assert first["sequence"]["duration_seconds"] == 3.5
    assert "must-not-leak" not in json.dumps(first)
    path = write_premiere_edit_plan(run, require_audio=True)
    assert json.loads(path.read_text(encoding="utf-8")) == first


def test_missing_audio_can_estimate_but_strict_mode_rejects(tmp_path: Path) -> None:
    run = completed_run(tmp_path)
    (run / "audio" / "manifest.json").unlink()
    assert build_premiere_edit_plan(run)["scenes"][0]["dialogue"][0]["timing_source"] == "estimate"
    with pytest.raises(ValueError, match="audio/manifest"):
        build_premiere_edit_plan(run, require_audio=True)


@pytest.mark.parametrize("mutation, message", [
    ("missing_image", "missing"), ("duplicate_scene", "sequential"), ("unsafe_path", "escapes")
])
def test_rejects_invalid_scene_assets(tmp_path: Path, mutation: str, message: str) -> None:
    run = completed_run(tmp_path)
    if mutation == "missing_image":
        (run / "scene_000.png").unlink()
    elif mutation == "duplicate_scene":
        data = json.loads((run / "run.json").read_text(encoding="utf-8"))
        data["visual_plan"]["scenes"][1]["scene_index"] = 0
        (run / "run.json").write_text(json.dumps(data), encoding="utf-8")
    else:
        manifest = json.loads((run / "manifest.json").read_text())
        manifest["assets"][1]["file_name"] = "../outside.png"
        (tmp_path / "outside.png").write_bytes(b"png")
        (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        build_premiere_edit_plan(run)


def test_rejects_duplicate_source_reference_and_schema_mismatch(tmp_path: Path) -> None:
    run = completed_run(tmp_path)
    data = json.loads((run / "run.json").read_text(encoding="utf-8"))
    data["visual_plan"]["scenes"][1]["source_refs"] = data["visual_plan"]["scenes"][0]["source_refs"]
    (run / "run.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="multiple scenes"):
        build_premiere_edit_plan(run)
    data["visual_plan"]["aspect_ratio"] = "9:16"
    (run / "run.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="16:9"):
        build_premiere_edit_plan(run)

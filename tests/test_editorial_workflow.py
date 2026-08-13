import json
from pathlib import Path

import pytest

from app.editorial_workflow import (
    EditorialConflictError,
    approve_revision,
    continue_approved_run,
    save_revision,
)
from app.youtube_image_generation import LocalSceneImageGenerator
from app.youtube_visuals import LocalYouTubeVisualPlanner


def content() -> dict:
    return {
        "selected_youtube_idea": {
            "source_article_ids": [1],
            "title": "An editorial idea",
            "hook": "A useful hook",
            "angle": "A distinct angle",
            "target_audience": "Technical viewers",
            "estimated_length_minutes": 10,
            "thumbnail_text": "THE CHANGE",
            "chapters": ["Context"],
            "seo_keywords": ["AI"],
        },
        "script": {
            "title": "An editorial idea",
            "thumbnail_text": "THE CHANGE",
            "target_minutes": 10,
            "opening_hook": "A useful opening",
            "chapters": [
                {
                    "chapter_index": 0,
                    "title": "Context",
                    "objective": "Explain context",
                    "estimated_seconds": 300,
                    "key_points": ["One fact"],
                }
            ],
            "narration_sections": [
                {"chapter_index": 0, "narration": "Detailed narration"}
            ],
            "closing": "A clear closing",
            "seo_keywords": ["AI"],
        },
        "dialogue": {
            "title": "An editorial idea",
            "thumbnail_text": "THE CHANGE",
            "target_minutes": 10,
            "opening_lines": [
                {"line_index": 0, "speaker": "さび助", "text": "始めよう。"}
            ],
            "chapters": [
                {
                    "chapter_index": 0,
                    "title": "Context",
                    "lines": [
                        {"line_index": 0, "speaker": "ハル", "text": "何が変わるの？"},
                        {"line_index": 1, "speaker": "さび助", "text": "ここが重要だよ。"},
                    ],
                }
            ],
            "closing_lines": [
                {"line_index": 0, "speaker": "ハル", "text": "よく分かった。"}
            ],
            "seo_keywords": ["AI"],
        },
    }


def draft(tmp_path: Path) -> Path:
    run = tmp_path / "run-one"
    run.mkdir()
    payload = {
        "run_id": "run-one",
        "channel_focus": "AI explainers",
        **content(),
        "editorial": {
            "schema": "ai-news-intelligence/editorial-workflow",
            "schema_version": 1,
            "status": "in_review",
            "revision": 1,
            "generated_at": "2026-08-14T00:00:00+00:00",
            "updated_at": "2026-08-14T00:00:00+00:00",
            "approved_at": None,
        },
        "visual_plan": None,
        "generated_images": [],
        "output_files": ["run.json"],
    }
    (run / "run.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return run


def test_save_revision_archives_history_and_rejects_stale_writer(tmp_path: Path) -> None:
    run = draft(tmp_path)
    edited = content()
    edited["dialogue"]["chapters"][0]["lines"][1]["text"] = "承認前の修正版だよ。"

    saved = save_revision(run, expected_revision=1, content=edited)

    assert saved["editorial"]["revision"] == 2
    assert saved["dialogue"]["chapters"][0]["lines"][1]["text"] == "承認前の修正版だよ。"
    assert (run / "editorial-revisions" / "revision_0001.json").is_file()
    assert (run / "editorial-revisions" / "revision_0002.json").is_file()
    with pytest.raises(EditorialConflictError, match="stale revision"):
        save_revision(run, expected_revision=1, content=edited)


def test_approval_is_revision_bound_and_prevents_more_edits(tmp_path: Path) -> None:
    run = draft(tmp_path)
    with pytest.raises(EditorialConflictError, match="no longer current"):
        approve_revision(run, expected_revision=2)

    approved = approve_revision(run, expected_revision=1)

    assert approved["editorial"]["status"] == "approved"
    assert approved["editorial"]["approved_revision"] == 1
    assert approved["editorial"]["approved_at"]
    with pytest.raises(EditorialConflictError, match="only in-review"):
        save_revision(run, expected_revision=1, content=content())


def test_continue_generates_once_from_approved_dialogue(tmp_path: Path) -> None:
    class CountingGenerator:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, request, *, size):
            self.calls += 1
            return LocalSceneImageGenerator().generate(request, size=size)

    run = draft(tmp_path)
    approve_revision(run, expected_revision=1)
    generator = CountingGenerator()
    first = continue_approved_run(
        run,
        visual_planner=LocalYouTubeVisualPlanner(),
        image_generator=generator,
        scene_limit=10,
        image_size="160x90",
    )
    calls = generator.calls
    second = continue_approved_run(
        run,
        visual_planner=LocalYouTubeVisualPlanner(),
        image_generator=generator,
        scene_limit=10,
        image_size="160x90",
    )

    assert first["editorial"]["status"] == "completed"
    assert first["visual_plan"]["scenes"]
    assert first["generated_images"]
    assert (run / "manifest.json").is_file()
    assert generator.calls == calls
    assert second["generated_images"] == first["generated_images"]


def test_failed_generation_keeps_approved_revision_retryable(tmp_path: Path) -> None:
    class FailingGenerator:
        def generate(self, request, *, size):
            raise RuntimeError("provider unavailable")

    run = draft(tmp_path)
    approve_revision(run, expected_revision=1)
    with pytest.raises(Exception):
        continue_approved_run(
            run,
            visual_planner=LocalYouTubeVisualPlanner(),
            image_generator=FailingGenerator(),
            scene_limit=10,
            image_size="160x90",
        )
    failed = json.loads((run / "run.json").read_text(encoding="utf-8"))
    assert failed["editorial"]["status"] == "generation_failed"
    assert failed["editorial"]["approved_revision"] == 1
    assert not (run / "manifest.json").exists()

    retried = continue_approved_run(
        run,
        visual_planner=LocalYouTubeVisualPlanner(),
        image_generator=LocalSceneImageGenerator(),
        scene_limit=10,
        image_size="160x90",
    )
    assert retried["editorial"]["status"] == "completed"

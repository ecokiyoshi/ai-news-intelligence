"""Editorial revisions, approval, and safe downstream continuation."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from tempfile import TemporaryDirectory
from typing import Any

from app.youtube_dialogue import DialogueChapter, DialogueLine, YouTubeDialogueScript
from app.youtube_ideas import YouTubeIdea
from app.youtube_image_generation import SceneImageGenerator, generate_youtube_scene_images
from app.youtube_script import YouTubeNarrationSection, YouTubeScript, YouTubeScriptChapter
from app.youtube_visuals import YouTubeVisualPlanner, generate_youtube_visual_plan

EDITORIAL_SCHEMA = "ai-news-intelligence/editorial-workflow"
EDITORIAL_SCHEMA_VERSION = 1
REVIEW_STATUS = "in_review"
APPROVED_STATUS = "approved"
PROCESSING_STATUS = "processing"
FAILED_STATUS = "generation_failed"
LEGACY_STATUS = "completed"
RUN_FILENAME = "run.json"
REVISION_DIRECTORY = "editorial-revisions"


class EditorialWorkflowError(ValueError):
    """Base error for invalid editorial workflow operations."""


class EditorialConflictError(EditorialWorkflowError):
    """Raised when an editor submits a stale revision."""


_locks_guard = Lock()
_run_locks: dict[str, Lock] = {}


def _run_lock(run_directory: Path) -> Lock:
    key = str(run_directory.resolve())
    with _locks_guard:
        return _run_locks.setdefault(key, Lock())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_run(run_directory: Path) -> dict[str, Any]:
    try:
        value = json.loads((run_directory / RUN_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EditorialWorkflowError("run metadata is invalid") from error
    if not isinstance(value, dict):
        raise EditorialWorkflowError("run metadata is invalid")
    return value


def new_review_metadata(created_at: datetime) -> dict[str, Any]:
    timestamp = created_at.isoformat()
    return {
        "schema": EDITORIAL_SCHEMA,
        "schema_version": EDITORIAL_SCHEMA_VERSION,
        "status": REVIEW_STATUS,
        "revision": 1,
        "generated_at": timestamp,
        "updated_at": timestamp,
        "approved_at": None,
        "approved_revision": None,
        "last_error": None,
    }


def editorial_status(run: dict[str, Any]) -> str:
    """Treat artifacts created before this feature as completed."""

    editorial = run.get("editorial")
    if not isinstance(editorial, dict):
        return LEGACY_STATUS
    status = editorial.get("status")
    return status if isinstance(status, str) and status else LEGACY_STATUS


def _idea(value: Any) -> YouTubeIdea:
    if not isinstance(value, dict):
        raise EditorialWorkflowError("selected_youtube_idea must be an object")
    try:
        return YouTubeIdea(**value)
    except (TypeError, ValueError) as error:
        raise EditorialWorkflowError(f"selected_youtube_idea: {error}") from error


def _script(value: Any) -> YouTubeScript:
    if not isinstance(value, dict):
        raise EditorialWorkflowError("script must be an object")
    try:
        return YouTubeScript(
            **{
                **value,
                "chapters": [YouTubeScriptChapter(**item) for item in value.get("chapters", [])],
                "narration_sections": [
                    YouTubeNarrationSection(**item)
                    for item in value.get("narration_sections", [])
                ],
            }
        )
    except (TypeError, ValueError) as error:
        raise EditorialWorkflowError(f"script: {error}") from error


def _line(value: Any) -> DialogueLine:
    if not isinstance(value, dict):
        raise EditorialWorkflowError("dialogue line must be an object")
    return DialogueLine(**value)


def _dialogue(value: Any) -> YouTubeDialogueScript:
    if not isinstance(value, dict):
        raise EditorialWorkflowError("dialogue must be an object")
    try:
        return YouTubeDialogueScript(
            **{
                **value,
                "opening_lines": [_line(item) for item in value.get("opening_lines", [])],
                "chapters": [
                    DialogueChapter(
                        **{
                            **item,
                            "lines": [_line(line) for line in item.get("lines", [])],
                        }
                    )
                    for item in value.get("chapters", [])
                ],
                "closing_lines": [_line(item) for item in value.get("closing_lines", [])],
            }
        )
    except (TypeError, ValueError, EditorialWorkflowError) as error:
        raise EditorialWorkflowError(f"dialogue: {error}") from error


def validate_editorial_content(content: Any) -> dict[str, Any]:
    if not isinstance(content, dict):
        raise EditorialWorkflowError("content must be an object")
    expected = {"selected_youtube_idea", "script", "dialogue"}
    if set(content) != expected:
        raise EditorialWorkflowError(
            "content must contain only selected_youtube_idea, script, and dialogue"
        )
    return {
        "selected_youtube_idea": asdict(_idea(content["selected_youtube_idea"])),
        "script": asdict(_script(content["script"])),
        "dialogue": asdict(_dialogue(content["dialogue"])),
    }


def editable_content(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected_youtube_idea": run.get("selected_youtube_idea"),
        "script": run.get("script"),
        "dialogue": run.get("dialogue"),
    }


def _require_editorial(run: dict[str, Any]) -> dict[str, Any]:
    editorial = run.get("editorial")
    if not isinstance(editorial, dict):
        raise EditorialWorkflowError("legacy completed runs are read-only")
    return editorial


def _archive_revision(run_directory: Path, revision: int, content: dict[str, Any]) -> None:
    directory = run_directory / REVISION_DIRECTORY
    directory.mkdir(exist_ok=True)
    path = directory / f"revision_{revision:04d}.json"
    if not path.exists():
        _atomic_json(path, {"revision": revision, "content": content})


def save_revision(
    run_directory: Path, *, expected_revision: int, content: Any
) -> dict[str, Any]:
    validated = validate_editorial_content(content)
    with _run_lock(run_directory):
        run = _load_run(run_directory)
        editorial = _require_editorial(run)
        if editorial_status(run) != REVIEW_STATUS:
            raise EditorialConflictError("only in-review runs can be edited")
        current = editorial.get("revision")
        if current != expected_revision:
            raise EditorialConflictError(
                f"stale revision: expected {expected_revision}, current {current}"
            )
        _archive_revision(run_directory, current, editable_content(run))
        revision = current + 1
        run.update(validated)
        editorial.update(
            {"revision": revision, "updated_at": _now(), "last_error": None}
        )
        _archive_revision(run_directory, revision, validated)
        _atomic_json(run_directory / RUN_FILENAME, run)
        return run


def approve_revision(run_directory: Path, *, expected_revision: int) -> dict[str, Any]:
    with _run_lock(run_directory):
        run = _load_run(run_directory)
        editorial = _require_editorial(run)
        if editorial_status(run) != REVIEW_STATUS:
            raise EditorialConflictError("only in-review runs can be approved")
        if editorial.get("revision") != expected_revision:
            raise EditorialConflictError("the reviewed revision is no longer current")
        validate_editorial_content(editable_content(run))
        timestamp = _now()
        editorial.update(
            {
                "status": APPROVED_STATUS,
                "approved_revision": expected_revision,
                "approved_at": timestamp,
                "updated_at": timestamp,
                "last_error": None,
            }
        )
        _archive_revision(run_directory, expected_revision, editable_content(run))
        _atomic_json(run_directory / RUN_FILENAME, run)
        return run


def continue_approved_run(
    run_directory: Path,
    *,
    visual_planner: YouTubeVisualPlanner,
    image_generator: SceneImageGenerator,
    scene_limit: int,
    image_size: str,
) -> dict[str, Any]:
    with _run_lock(run_directory):
        run = _load_run(run_directory)
        editorial = _require_editorial(run)
        status = editorial_status(run)
        if status == LEGACY_STATUS:
            return run
        if status not in {APPROVED_STATUS, FAILED_STATUS}:
            raise EditorialConflictError(
                "run must be approved before downstream generation"
            )
        editorial.update(
            {"status": PROCESSING_STATUS, "updated_at": _now(), "last_error": None}
        )
        _atomic_json(run_directory / RUN_FILENAME, run)
        try:
            dialogue = _dialogue(run.get("dialogue"))
            visual_plan = generate_youtube_visual_plan(
                dialogue,
                visual_planner,
                channel_focus=str(run.get("channel_focus", "")),
                scene_limit=scene_limit,
            )
            with TemporaryDirectory(prefix="editorial-generation-", dir=run_directory) as temporary:
                staging = Path(temporary)
                images = generate_youtube_scene_images(
                    visual_plan,
                    image_generator,
                    output_directory=staging,
                    size=image_size,
                    scene_limit=scene_limit,
                )
                generated_images = []
                for asset in images.assets:
                    source = Path(asset.file_path)
                    target = run_directory / source.name
                    source.replace(target)
                    generated_images.append({**asdict(asset), "file_path": str(target)})
                (staging / "manifest.json").replace(run_directory / "manifest.json")
        except Exception as error:
            editorial.update(
                {
                    "status": FAILED_STATUS,
                    "updated_at": _now(),
                    "last_error": type(error).__name__,
                }
            )
            _atomic_json(run_directory / RUN_FILENAME, run)
            raise
        run["visual_plan"] = asdict(visual_plan)
        run["generated_images"] = generated_images
        run["output_files"] = [
            *(Path(asset["file_path"]).name for asset in generated_images),
            "manifest.json",
            RUN_FILENAME,
        ]
        editorial.update(
            {"status": LEGACY_STATUS, "updated_at": _now(), "last_error": None}
        )
        _atomic_json(run_directory / RUN_FILENAME, run)
        return run

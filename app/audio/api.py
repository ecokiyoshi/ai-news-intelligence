"""FastAPI endpoints for dialogue audio generation and playback."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.audio.speech_generator import generate_project_audio
from app.dashboard import _run_directory

router = APIRouter(prefix="/api/projects", tags=["audio"])


class GenerateAudioRequest(BaseModel):
    force: bool = False
    merge: bool = True


@router.post("/{project_id}/audio/generate")
def generate_audio(project_id: str, request: GenerateAudioRequest) -> dict:
    try:
        result = generate_project_audio(project_id, force=request.force, merge=request.merge)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    payload = asdict(result)
    payload.update({"status": "completed", "segments_generated": result.generated_segments, "segments_reused": result.reused_segments, "segments_failed": result.failed_segments, "merged_audio": Path(result.merged_audio_path).name if result.merged_audio_path else None})
    return payload


@router.get("/{project_id}/audio/{filename}", response_class=FileResponse)
def audio_file(project_id: str, filename: str) -> FileResponse:
    if Path(filename).name != filename or Path(filename).suffix.lower() != ".mp3":
        raise HTTPException(status_code=404, detail="Audio not found")
    path = _run_directory(project_id) / "audio" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(path, media_type="audio/mpeg")

"""Optional FFmpeg concatenation of generated MP3 dialogue segments."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def merge_audio(files: list[Path], destination: Path, gap_ms: int = 200) -> Path:
    if not files:
        raise ValueError("at least one audio segment is required")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to merge dialogue audio")
    if gap_ms < 0:
        raise ValueError("dialogue gap must not be negative")
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        concat = Path(temporary) / "concat.txt"
        silence = Path(temporary) / "silence.mp3"
        subprocess.run([ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(gap_ms / 1000), "-q:a", "9", "-y", str(silence)], check=True)
        entries = []
        for index, path in enumerate(files):
            entries.append(f"file '{path.resolve().as_posix()}'")
            if gap_ms and index < len(files) - 1:
                entries.append(f"file '{silence.resolve().as_posix()}'")
        concat.write_text("\n".join(entries), encoding="utf-8")
        subprocess.run([ffmpeg, "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c:a", "libmp3lame", "-b:a", "128k", "-y", str(destination)], check=True)
    return destination

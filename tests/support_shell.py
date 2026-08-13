"""Cross-platform helpers for exercising production Bash scripts in tests."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def bash_executable() -> str:
    configured = os.environ.get("AI_NEWS_BASH", "").strip()
    candidates = [
        configured,
        shutil.which("bash") or "",
        r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else "",
        r"C:\Program Files\Git\usr\bin\bash.exe" if os.name == "nt" else "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError(
        "Bash is required. On Windows install Git for Windows or set AI_NEWS_BASH."
    )


def bash_command(script: Path | str, *arguments: str) -> list[str]:
    if str(script) == "-n":
        return [bash_executable(), "-n", *map(str, arguments)]
    command = (
        'if [[ -n "${AI_NEWS_TEST_PATH:-}" ]]; then '
        'export PATH="${AI_NEWS_TEST_PATH}:$PATH"; fi; exec bash "$@"'
    )
    return [bash_executable(), "-c", command, "ai-news-bash", str(script), *map(str, arguments)]


def bash_path(path: Path | str) -> str:
    value = Path(path).resolve().as_posix()
    if os.name == "nt" and len(value) >= 2 and value[1] == ":":
        return f"/{value[0].lower()}{value[2:]}"
    return value


def prepend_path(environment: dict[str, str], directory: Path) -> None:
    if os.name == "nt":
        environment["AI_NEWS_TEST_PATH"] = bash_path(directory)
        environment["MSYS2_ARG_CONV_EXCL"] = "*"
    else:
        environment["PATH"] = os.pathsep.join((str(directory), environment.get("PATH", "")))


def install_python3_shim(directory: Path) -> None:
    """Give Git Bash fixtures a python3 command backed by the active Windows Python."""
    if os.name != "nt":
        return
    executable = Path(sys.executable).resolve().as_posix()
    if len(executable) >= 2 and executable[1] == ":":
        executable = f"/{executable[0].lower()}{executable[2:]}"
    shim = directory / "python3"
    shim.write_text(
        '#!/usr/bin/env bash\n'
        'script="$1"\nshift\n'
        '[[ "$script" == /* ]] && script="$(cygpath -w "$script")"\n'
        f'"{executable}" "$script" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    shim.chmod(0o755)

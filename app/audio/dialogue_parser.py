"""Parse Japanese speaker-labelled dialogue into safe speech segments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SPEAKERS = {"さび助": "sabisuke", "ハル": "haru", "sabisuke": "sabisuke", "haru": "haru"}
LABEL = re.compile(r"^\s*(?:\[|【)?\s*(さび助|ハル|sabisuke|haru)\s*(?:\]|】|:|：)\s*(.*)$", re.I)


@dataclass(frozen=True)
class DialogueLine:
    index: int
    speaker: str
    display_name: str
    text: str


def parse_dialogue(text: str, *, strict: bool = True) -> list[DialogueLine]:
    """Parse labels, joining continuation/Markdown lines into the current turn."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("dialogue text must not be empty")
    parsed: list[tuple[str, str, list[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        match = LABEL.match(line)
        if match:
            display = match.group(1)
            speaker = SPEAKERS[display if display in SPEAKERS else display.lower()]
            parsed.append((speaker, "さび助" if speaker == "sabisuke" else "ハル", [match.group(2).strip()]))
        elif parsed:
            parsed[-1][2].append(line)
        elif strict:
            raise ValueError(f"unlabelled dialogue text: {line[:40]}")
    result = [
        DialogueLine(i, speaker, display, "\n".join(parts).strip())
        for i, (speaker, display, parts) in enumerate(parsed, 1)
        if "\n".join(parts).strip()
    ]
    if not result:
        raise ValueError("dialogue contains no supported speaker lines")
    return result


def lines_from_run(dialogue: Any) -> list[DialogueLine]:
    """Flatten the existing structured run dialogue in playback order."""
    if isinstance(dialogue, str):
        return parse_dialogue(dialogue)
    if not isinstance(dialogue, dict):
        raise ValueError("run dialogue is missing or invalid")
    raw_lines = list(dialogue.get("opening_lines") or [])
    for chapter in dialogue.get("chapters") or []:
        if isinstance(chapter, dict):
            raw_lines.extend(chapter.get("lines") or [])
    raw_lines.extend(dialogue.get("closing_lines") or [])
    result = []
    for raw in raw_lines:
        if not isinstance(raw, dict):
            continue
        display = str(raw.get("speaker", "")).strip()
        key = SPEAKERS.get(display) or SPEAKERS.get(display.lower())
        text = str(raw.get("text", "")).strip()
        if not key:
            raise ValueError(f"unsupported dialogue speaker: {display or '<empty>'}")
        if text:
            result.append(DialogueLine(len(result) + 1, key, "さび助" if key == "sabisuke" else "ハル", text))
    if not result:
        raise ValueError("run dialogue contains no speakable lines")
    return result

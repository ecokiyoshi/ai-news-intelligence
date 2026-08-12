"""Conservative, testable Japanese TTS pronunciation normalization."""

from __future__ import annotations

import re

PRONUNCIATION_MAP = {
    "OpenAI": "オープンエーアイ", "NVIDIA": "エヌビディア", "Broadcom": "ブロードコム",
    "GPU": "ジーピーユー", "CPU": "シーピーユー", "API": "エーピーアイ",
    "AMD": "エーエムディー", "AI": "エーアイ", "5G": "ファイブジー", "Wi-Fi": "ワイファイ",
}
_TERMS = re.compile("|".join(re.escape(key) for key in sorted(PRONUNCIATION_MAP, key=len, reverse=True)))
_GW = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*GW\b", re.I)


def normalize_japanese_tts(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    normalized = _GW.sub(r"\1ギガワット", text)
    return _TERMS.sub(lambda match: PRONUNCIATION_MAP[match.group(0)], normalized)

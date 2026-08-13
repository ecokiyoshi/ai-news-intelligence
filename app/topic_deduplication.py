"""Detect repeated YouTube topics from persisted production runs."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app.youtube_ideas import YouTubeIdea

_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "in", "is", "it", "of", "on", "or", "the", "to", "what", "why", "with",
    "explained", "explanation", "idea", "news", "practical", "release", "signal",
    "signals", "video",
}


@dataclass(frozen=True)
class PreviousTopic:
    run_id: str
    title: str


def _tokens(title: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", title).casefold().replace(
        "\N{RIGHT SINGLE QUOTATION MARK}", "'"
    )
    return {
        token.rstrip("s") if len(token) > 4 else token
        for token in _WORD.findall(normalized)
        if token not in _STOP_WORDS and len(token) > 1
    }


def titles_are_similar(left: str, right: str) -> bool:
    """Return true when titles describe substantially the same concrete topic."""

    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return False
    shared = left_tokens & right_tokens
    union = left_tokens | right_tokens
    jaccard = len(shared) / len(union)
    containment = len(shared) / min(len(left_tokens), len(right_tokens))
    return jaccard >= 0.45 or (len(shared) >= 3 and containment >= 0.5)


def load_previous_topics(
    output_root: Path, *, channel_focus: str, limit: int = 10
) -> list[PreviousTopic]:
    """Load recent successful topics, ignoring partial or malformed run artifacts."""

    if not output_root.is_dir():
        return []
    topics: list[PreviousTopic] = []
    for path in sorted(output_root.glob("*/run.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if str(data.get("channel_focus", "")).strip() != channel_focus.strip():
                continue
            title = data["selected_youtube_idea"]["title"]
            if not isinstance(title, str) or not title.strip():
                continue
            topics.append(PreviousTopic(str(data.get("run_id", path.parent.name)), title))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if len(topics) >= limit:
            break
    return topics


def remove_repeated_ideas(
    ideas: list[YouTubeIdea], previous_topics: list[PreviousTopic]
) -> tuple[list[YouTubeIdea], list[YouTubeIdea]]:
    fresh, repeated = [], []
    for idea in ideas:
        target = repeated if any(
            titles_are_similar(idea.title, topic.title) for topic in previous_topics
        ) else fresh
        target.append(idea)
    return fresh, repeated


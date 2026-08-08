from types import SimpleNamespace
from typing import cast

import pytest

from app.openai_youtube_dialogue import (
    OpenAIDialogueChapter,
    OpenAIDialogueLine,
    OpenAIYouTubeDialogueConverter,
    OpenAIYouTubeDialogueResponse,
)
from app.youtube_dialogue import (
    DEFAULT_DIALOGUE_CHARACTERS,
    LocalYouTubeDialogueConverter,
    YouTubeDialogueConverter,
    build_youtube_dialogue_source,
)
from app.youtube_ideas import YouTubeIdea
from app.youtube_packaging import YouTubePackagingCandidate
from app.youtube_potential import YouTubePotentialResult
from app.youtube_script import (
    LocalYouTubeOutlineGenerator,
    LocalYouTubeScriptGenerator,
    build_youtube_script_source,
    generate_youtube_script,
)


def source():
    idea = YouTubeIdea(
        source_article_ids=[1], title="AI release", hook="A model was released.",
        angle="Explain its technical implications.", target_audience="AI viewers",
        estimated_length_minutes=15, thumbnail_text="MODEL",
        chapters=["Background", "Impact"], seo_keywords=["AI", "model"],
    )
    potential = YouTubePotentialResult(
        idea_index=0, youtube_potential_score=71.25, topic_appeal_score=70,
        clarity_score=70, surprise_score=70, searchability_score=70,
        visual_explainability_score=70, reason="Valid",
    )
    packaging = YouTubePackagingCandidate(
        candidate_index=0, title="AI Release Explained", thumbnail_text="WHAT CHANGED",
        rationale="Clear", packaging_score=76.5, clarity_score=75, curiosity_score=75,
        specificity_score=75, truthfulness_score=75, thumbnail_synergy_score=75,
        evaluation_reason="Valid",
    )
    script_source = build_youtube_script_source(idea, potential, packaging)
    script = generate_youtube_script(
        script_source, LocalYouTubeOutlineGenerator(), LocalYouTubeScriptGenerator(),
        channel_focus="AI news", target_minutes=15,
    )
    return build_youtube_dialogue_source(script)


def valid_response() -> OpenAIYouTubeDialogueResponse:
    dialogue = LocalYouTubeDialogueConverter().convert(
        source(), channel_focus="AI news", characters=DEFAULT_DIALOGUE_CHARACTERS
    )
    return OpenAIYouTubeDialogueResponse(
        opening_lines=[OpenAIDialogueLine(**line.__dict__) for line in dialogue.opening_lines],
        chapters=[
            OpenAIDialogueChapter(
                chapter_index=chapter.chapter_index,
                title=chapter.title,
                lines=[OpenAIDialogueLine(**line.__dict__) for line in chapter.lines],
            )
            for chapter in dialogue.chapters
        ],
        closing_lines=[OpenAIDialogueLine(**line.__dict__) for line in dialogue.closing_lines],
    )


class FakeResponses:
    def __init__(self, parsed=None, error: Exception | None = None):
        self.parsed = parsed
        self.error = error
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error: raise self.error
        return SimpleNamespace(output=[SimpleNamespace(
            type="message", content=[SimpleNamespace(parsed=self.parsed)]
        )])


class FakeClient:
    def __init__(self, parsed=None, error: Exception | None = None):
        self.responses = FakeResponses(parsed, error)


def test_openai_converter_uses_typed_responses_and_complete_paired_context() -> None:
    client = FakeClient(valid_response())
    converter: YouTubeDialogueConverter = cast(
        YouTubeDialogueConverter,
        OpenAIYouTubeDialogueConverter(client=client, model="dialogue-model"),
    )
    result = converter.convert(
        source(), channel_focus="AI news", characters=DEFAULT_DIALOGUE_CHARACTERS
    )
    assert result.title == "AI Release Explained"
    assert len(result.chapters) == 8
    call = client.responses.calls[0]
    assert call["model"] == "dialogue-model"
    assert call["text_format"] is OpenAIYouTubeDialogueResponse
    for expected in (
        "AI news", "さび助", "ハル", "primary calm", "audience proxy",
        "AI Release Explained", "WHAT CHANGED", '"target_minutes":15',
        "A model was released", '"chapter_index":0', "Why it matters",
        "Explain", "Background", '"estimated_seconds"', '"key_points"',
        '"narration"', '"closing"', '"seo_keywords"',
    ):
        assert expected in call["input"]


@pytest.mark.parametrize("kind", ["missing", "reordered", "speaker", "blank"])
def test_openai_converter_rejects_malformed_output(kind: str) -> None:
    parsed = valid_response()
    if kind == "missing":
        parsed.chapters.pop(1)
    elif kind == "reordered":
        parsed.chapters[1], parsed.chapters[2] = parsed.chapters[2], parsed.chapters[1]
    elif kind == "speaker":
        parsed.opening_lines[0].speaker = "Narrator"
    else:
        parsed.opening_lines[0].text = " "
    with pytest.raises(ValueError):
        OpenAIYouTubeDialogueConverter(client=FakeClient(parsed)).convert(
            source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS
        )


def test_openai_converter_rejects_missing_parsed_output() -> None:
    with pytest.raises(ValueError, match="parsed dialogue"):
        OpenAIYouTubeDialogueConverter(client=FakeClient()).convert(
            source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS
        )


def test_openai_converter_propagates_provider_exception_without_network_or_key() -> None:
    with pytest.raises(RuntimeError, match="API unavailable"):
        OpenAIYouTubeDialogueConverter(
            client=FakeClient(error=RuntimeError("API unavailable"))
        ).convert(source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS)

from types import SimpleNamespace
from typing import cast

import pytest

from app.openai_youtube_dialogue import (
    YOUTUBE_DIALOGUE_INSTRUCTIONS,
    OpenAIDialogueChapter,
    OpenAIDialogueLine,
    OpenAIDialogueChapterSupplement,
    OpenAIYouTubeDialogueConverter,
    OpenAIYouTubeDialogueResponse,
    OpenAIYouTubeDialogueSupplementResponse,
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
        channel_focus="AI news", target_minutes=10,
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


class SequencedFakeResponses:
    def __init__(self, *parsed):
        self.parsed = list(parsed)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        value = self.parsed.pop(0)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(output=[SimpleNamespace(
            type="message", content=[SimpleNamespace(parsed=value)]
        )])


class FakeClient:
    def __init__(self, parsed=None, error: Exception | None = None):
        self.responses = FakeResponses(parsed, error)


def short_response() -> OpenAIYouTubeDialogueResponse:
    parsed = valid_response()
    parsed.opening_lines = [
        OpenAIDialogueLine(line_index=0, speaker="ハル", text="Why?"),
        OpenAIDialogueLine(line_index=1, speaker="さび助", text="Context."),
    ]
    for chapter in parsed.chapters:
        chapter.lines = [
            OpenAIDialogueLine(line_index=0, speaker="ハル", text="Why?"),
            OpenAIDialogueLine(line_index=1, speaker="さび助", text="Explanation."),
        ]
    parsed.closing_lines = [
        OpenAIDialogueLine(line_index=0, speaker="さび助", text="Summary."),
        OpenAIDialogueLine(line_index=1, speaker="ハル", text="Understood."),
    ]
    return parsed


def sufficient_supplement(parsed: OpenAIYouTubeDialogueResponse | None = None):
    parsed = parsed or short_response()
    return OpenAIYouTubeDialogueSupplementResponse(chapters=[
        OpenAIDialogueChapterSupplement(
            chapter_index=chapter.chapter_index,
            lines=[OpenAIDialogueLine(
                line_index=len(chapter.lines), speaker="さび助", text=" ".join(["detail"] * 180),
            )],
        )
        for chapter in parsed.chapters
    ])


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
    assert "every spoken dialogue line in natural Japanese" in call["instructions"]
    assert call["text_format"] is OpenAIYouTubeDialogueResponse
    for expected in (
        "AI news", "さび助", "ハル", "primary calm", "audience proxy",
        "AI Release Explained", "WHAT CHANGED", '"japanese_target_minutes":10',
        "A model was released", '"chapter_index":0', "Why it matters",
        "Explain", "Background", '"estimated_seconds"', '"key_points"',
        '"narration"', '"closing"', '"seo_keywords"',
        '"whole_script_target":2800', '"whole_script_minimum":2100',
        '"whole_script_maximum":3500',
    ):
        assert expected in call["input"]
    assert len(client.responses.calls) == 1


def test_prompt_allows_chapter_reorganization_and_rejects_one_to_one_translation() -> None:
    instructions = YOUTUBE_DIALOGUE_INSTRUCTIONS
    assert "Do not preserve the source chapter count" in instructions
    assert "9, 10, 15, 20" in instructions
    assert "Do not map" in instructions and "one-to-one" in instructions
    assert all(word in instructions for word in ("Merge", "split", "reorder", "remove"))


def test_prompt_requires_friendly_spoken_japanese() -> None:
    instructions = YOUTUBE_DIALOGUE_INSTRUCTIONS
    assert "close friends" in instructions
    assert "Do not use formal Japanese" in instructions
    assert "sounds natural aloud" in instructions
    assert "Haru asks short casual" in instructions
    assert "knowledgeable friend rather than a lecturer" in instructions


def test_openai_converter_supplements_only_short_chapters_and_preserves_existing() -> None:
    initial = short_response()
    original = initial.model_copy(deep=True)
    responses = SequencedFakeResponses(initial, sufficient_supplement(initial))
    client = SimpleNamespace(responses=responses)

    result = OpenAIYouTubeDialogueConverter(client=client).convert(
        source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS
    )

    assert len(responses.calls) == 2
    assert responses.calls[1]["text_format"] is OpenAIYouTubeDialogueSupplementResponse
    assert result.opening_lines[0].text == original.opening_lines[0].text
    assert result.closing_lines[0].text == original.closing_lines[0].text
    for before, after in zip(original.chapters, result.chapters, strict=True):
        assert [line.text for line in after.lines[:len(before.lines)]] == [
            line.text for line in before.lines
        ]
        assert len(after.lines) == len(before.lines) + 1


def test_openai_converter_rejects_unexpected_supplement_coverage() -> None:
    initial = short_response()
    supplement = sufficient_supplement(initial)
    supplement.chapters.pop()
    client = SimpleNamespace(responses=SequencedFakeResponses(initial, supplement))
    with pytest.raises(ValueError, match="exact requested chapter order and coverage"):
        OpenAIYouTubeDialogueConverter(client=client).convert(
            source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS
        )


@pytest.mark.parametrize("kind", ["chapter", "reordered", "speaker", "line_index"])
def test_openai_converter_rejects_malformed_supplement(kind: str) -> None:
    initial = short_response()
    supplement = sufficient_supplement(initial)
    if kind == "chapter":
        supplement.chapters[0].chapter_index = 99
    elif kind == "reordered":
        supplement.chapters[0], supplement.chapters[1] = (
            supplement.chapters[1],
            supplement.chapters[0],
        )
    elif kind == "speaker":
        supplement.chapters[0].lines[0].speaker = "Narrator"
    else:
        supplement.chapters[0].lines[0].line_index += 1

    with pytest.raises(ValueError):
        OpenAIYouTubeDialogueConverter(
            client=SimpleNamespace(
                responses=SequencedFakeResponses(initial, supplement)
            )
        ).convert(source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS)


def test_openai_converter_reports_remaining_shortfall_after_one_supplement() -> None:
    initial = short_response()
    supplement = sufficient_supplement(initial)
    for chapter in supplement.chapters:
        chapter.lines[0].text = "detail"
    responses = SequencedFakeResponses(initial, supplement)
    with pytest.raises(ValueError, match=r"remains too short.*remaining Japanese non-whitespace characters"):
        OpenAIYouTubeDialogueConverter(
            client=SimpleNamespace(responses=responses)
        ).convert(source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS)
    assert len(responses.calls) == 2


def test_openai_converter_reports_overlong_supplement_without_retrying() -> None:
    initial = short_response()
    supplement = sufficient_supplement(initial)
    for chapter in supplement.chapters:
        chapter.lines[0].text = " ".join(["detail"] * 1000)
    responses = SequencedFakeResponses(initial, supplement)
    with pytest.raises(ValueError, match=r"exceeds.*after one supplement call"):
        OpenAIYouTubeDialogueConverter(
            client=SimpleNamespace(responses=responses)
        ).convert(source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS)
    assert len(responses.calls) == 2


def test_openai_converter_shortens_an_overlong_dialogue_once() -> None:
    parsed = valid_response()
    parsed.chapters[0].lines[1].text = " ".join(["detail"] * 3000)
    responses = SequencedFakeResponses(parsed, valid_response())
    result = OpenAIYouTubeDialogueConverter(
        client=SimpleNamespace(responses=responses)
    ).convert(source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS)
    assert result.title == "AI Release Explained"
    assert len(responses.calls) == 2
    assert "must not exceed 3500 Japanese" in responses.calls[1]["instructions"]


def test_openai_converter_propagates_supplement_provider_exception() -> None:
    responses = SequencedFakeResponses(short_response(), RuntimeError("supplement unavailable"))
    with pytest.raises(RuntimeError, match="supplement unavailable"):
        OpenAIYouTubeDialogueConverter(
            client=SimpleNamespace(responses=responses)
        ).convert(source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS)


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

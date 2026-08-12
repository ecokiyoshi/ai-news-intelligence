from types import SimpleNamespace

import pytest

from app.anthropic_youtube_dialogue import (
    AnthropicDialogueChapter,
    AnthropicDialogueChapterSupplement,
    AnthropicDialogueLine,
    AnthropicYouTubeDialogueConverter,
    AnthropicYouTubeDialogueResponse,
    AnthropicYouTubeDialogueSupplementResponse,
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
from support_anthropic import FakeClient, NoToolCallClient, SequencedMessages, call_input_text


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


def valid_response() -> AnthropicYouTubeDialogueResponse:
    dialogue = LocalYouTubeDialogueConverter().convert(
        source(), channel_focus="AI news", characters=DEFAULT_DIALOGUE_CHARACTERS
    )
    return AnthropicYouTubeDialogueResponse(
        opening_lines=[AnthropicDialogueLine(**line.__dict__) for line in dialogue.opening_lines],
        chapters=[
            AnthropicDialogueChapter(
                chapter_index=chapter.chapter_index,
                title=chapter.title,
                lines=[AnthropicDialogueLine(**line.__dict__) for line in chapter.lines],
            )
            for chapter in dialogue.chapters
        ],
        closing_lines=[AnthropicDialogueLine(**line.__dict__) for line in dialogue.closing_lines],
    )


def short_response() -> AnthropicYouTubeDialogueResponse:
    parsed = valid_response()
    parsed.opening_lines = [
        AnthropicDialogueLine(line_index=0, speaker="ハル", text="Why?"),
        AnthropicDialogueLine(line_index=1, speaker="さび助", text="Context."),
    ]
    for chapter in parsed.chapters:
        chapter.lines = [
            AnthropicDialogueLine(line_index=0, speaker="ハル", text="Why?"),
            AnthropicDialogueLine(line_index=1, speaker="さび助", text="Explanation."),
        ]
    parsed.closing_lines = [
        AnthropicDialogueLine(line_index=0, speaker="さび助", text="Summary."),
        AnthropicDialogueLine(line_index=1, speaker="ハル", text="Understood."),
    ]
    return parsed


def sufficient_supplement(parsed: AnthropicYouTubeDialogueResponse | None = None):
    parsed = parsed or short_response()
    return AnthropicYouTubeDialogueSupplementResponse(chapters=[
        AnthropicDialogueChapterSupplement(
            chapter_index=chapter.chapter_index,
            lines=[AnthropicDialogueLine(
                line_index=len(chapter.lines), speaker="さび助", text=" ".join(["detail"] * 280),
            )],
        )
        for chapter in parsed.chapters
    ])


def test_anthropic_converter_uses_structured_tool_call_and_complete_paired_context() -> None:
    client = FakeClient(valid_response())
    converter: YouTubeDialogueConverter = AnthropicYouTubeDialogueConverter(
        client=client, model="dialogue-model"
    )
    result = converter.convert(
        source(), channel_focus="AI news", characters=DEFAULT_DIALOGUE_CHARACTERS
    )
    assert result.title == "AI Release Explained"
    assert len(result.chapters) == 8
    call = client.messages.calls[0]
    assert call["model"] == "dialogue-model"
    assert "every spoken dialogue line in natural Japanese" in call["system"]
    text = call_input_text(call)
    for expected in (
        "AI news", "さび助", "ハル", "primary calm", "audience proxy",
        "AI Release Explained", "WHAT CHANGED", '"target_minutes":15',
        "A model was released", '"chapter_index":0', "Why it matters",
        "Explain", "Background", '"estimated_seconds"', '"key_points"',
        '"narration"', '"closing"', '"seo_keywords"',
        '"whole_script_target":4200', '"whole_script_minimum":3150',
        '"whole_script_maximum":5250', '"chapter_targets"',
    ):
        assert expected in text
    assert len(client.messages.calls) == 1


def test_anthropic_converter_supplements_only_short_chapters_and_preserves_existing() -> None:
    initial = short_response()
    original = initial.model_copy(deep=True)
    messages = SequencedMessages([initial, sufficient_supplement(initial)])
    client = SimpleNamespace(messages=messages)

    result = AnthropicYouTubeDialogueConverter(client=client).convert(
        source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS
    )

    assert len(messages.calls) == 2
    assert result.opening_lines[0].text == original.opening_lines[0].text
    assert result.closing_lines[0].text == original.closing_lines[0].text
    for before, after in zip(original.chapters, result.chapters, strict=True):
        assert [line.text for line in after.lines[:len(before.lines)]] == [
            line.text for line in before.lines
        ]
        assert len(after.lines) == len(before.lines) + 1


def test_anthropic_converter_rejects_unexpected_supplement_coverage() -> None:
    initial = short_response()
    supplement = sufficient_supplement(initial)
    supplement.chapters.pop()
    client = SimpleNamespace(messages=SequencedMessages([initial, supplement]))
    with pytest.raises(ValueError, match="exact requested chapter order and coverage"):
        AnthropicYouTubeDialogueConverter(client=client).convert(
            source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS
        )


@pytest.mark.parametrize("kind", ["chapter", "reordered", "speaker", "line_index"])
def test_anthropic_converter_rejects_malformed_supplement(kind: str) -> None:
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
        AnthropicYouTubeDialogueConverter(
            client=SimpleNamespace(messages=SequencedMessages([initial, supplement]))
        ).convert(source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS)


def test_anthropic_converter_reports_remaining_shortfall_after_one_supplement() -> None:
    initial = short_response()
    supplement = sufficient_supplement(initial)
    for chapter in supplement.chapters:
        chapter.lines[0].text = "detail"
    messages = SequencedMessages([initial, supplement])
    with pytest.raises(ValueError, match=r"remains too short.*remaining Japanese non-whitespace characters"):
        AnthropicYouTubeDialogueConverter(
            client=SimpleNamespace(messages=messages)
        ).convert(source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS)
    assert len(messages.calls) == 2


def test_anthropic_converter_reports_overlong_supplement_without_retrying() -> None:
    initial = short_response()
    supplement = sufficient_supplement(initial)
    for chapter in supplement.chapters:
        chapter.lines[0].text = " ".join(["detail"] * 1000)
    messages = SequencedMessages([initial, supplement])
    with pytest.raises(ValueError, match=r"exceeds.*after one supplement call"):
        AnthropicYouTubeDialogueConverter(
            client=SimpleNamespace(messages=messages)
        ).convert(source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS)
    assert len(messages.calls) == 2


def test_anthropic_converter_shortens_an_overlong_dialogue_once() -> None:
    parsed = valid_response()
    parsed.chapters[0].lines[1].text = " ".join(["detail"] * 3000)
    messages = SequencedMessages([parsed, valid_response()])
    result = AnthropicYouTubeDialogueConverter(
        client=SimpleNamespace(messages=messages)
    ).convert(source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS)
    assert result.title == "AI Release Explained"
    assert len(messages.calls) == 2
    assert "must not exceed 5250 Japanese" in messages.calls[1]["system"]


def test_anthropic_converter_propagates_supplement_provider_exception() -> None:
    messages = SequencedMessages([short_response(), RuntimeError("supplement unavailable")])
    with pytest.raises(RuntimeError, match="supplement unavailable"):
        AnthropicYouTubeDialogueConverter(
            client=SimpleNamespace(messages=messages)
        ).convert(source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS)


@pytest.mark.parametrize("kind", ["missing", "reordered", "speaker", "blank"])
def test_anthropic_converter_rejects_malformed_output(kind: str) -> None:
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
        AnthropicYouTubeDialogueConverter(client=FakeClient(parsed)).convert(
            source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS
        )


def test_anthropic_converter_rejects_missing_tool_call_output() -> None:
    with pytest.raises(ValueError, match="structured tool call"):
        AnthropicYouTubeDialogueConverter(client=NoToolCallClient()).convert(
            source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS
        )


def test_anthropic_converter_propagates_provider_exception_without_network_or_key() -> None:
    with pytest.raises(RuntimeError, match="API unavailable"):
        AnthropicYouTubeDialogueConverter(
            client=FakeClient(error=RuntimeError("API unavailable"))
        ).convert(source(), channel_focus="AI", characters=DEFAULT_DIALOGUE_CHARACTERS)

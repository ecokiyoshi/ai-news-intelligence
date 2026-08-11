from types import SimpleNamespace

import pytest

from app.anthropic_youtube_script import (
    AnthropicYouTubeNarrationSection,
    AnthropicYouTubeNarrationSupplementResponse,
    AnthropicYouTubeOutlineGenerator,
    AnthropicYouTubeOutlineResponse,
    AnthropicYouTubeScriptChapter,
    AnthropicYouTubeScriptGenerator,
    AnthropicYouTubeScriptResponse,
)
from app.youtube_script import (
    YouTubeOutlineGenerator,
    YouTubeScriptChapter,
    YouTubeScriptGenerator,
    YouTubeScriptSource,
)
from support_anthropic import FakeClient, NoToolCallClient, SequencedClient, call_input_text


def source() -> YouTubeScriptSource:
    return YouTubeScriptSource(
        idea_index=2, source_article_ids=[10, 11],
        selected_title="The AI Release Explained", selected_thumbnail_text="WHAT CHANGED",
        hook="A new AI model changes the landscape.",
        angle="Explain the technical and industry implications.",
        target_audience="AI industry viewers", estimated_length_minutes=15,
        original_chapters=["Background", "Release", "Impact"],
        seo_keywords=["AI model", "AI news"], youtube_potential_score=12.345,
        packaging_score=67.891,
    )


def anthropic_chapter(index: int, seconds: int = 450, **overrides):
    values = dict(chapter_index=index, title=f"Chapter {index}",
                  objective="Explain supplied context", estimated_seconds=seconds,
                  key_points=["Context", "Impact"])
    values.update(overrides)
    return AnthropicYouTubeScriptChapter(**values)


def chapter(index: int, seconds: int = 450):
    return YouTubeScriptChapter(index, f"Chapter {index}", "Explain context", seconds, ["Context"])


def narration(index: int, words: int = 1124):
    return AnthropicYouTubeNarrationSection(
        chapter_index=index, narration="word " * words
    )


def test_anthropic_outline_uses_structured_tool_call_and_complete_context() -> None:
    parsed = AnthropicYouTubeOutlineResponse(chapters=[anthropic_chapter(1), anthropic_chapter(0)])
    client = FakeClient(parsed)
    generator: YouTubeOutlineGenerator = AnthropicYouTubeOutlineGenerator(
        client=client, model="outline-model"
    )
    chapters = generator.generate_outline(source(), channel_focus="AI news", target_minutes=15)
    assert [x.chapter_index for x in chapters] == [0, 1]
    assert sum(x.estimated_seconds for x in chapters) == 15 * 60
    call = client.messages.calls[0]
    assert call["model"] == "outline-model"
    text = call_input_text(call)
    for expected in (
        "AI news", '"target_minutes":15', '"idea_index":2',
        '"source_article_ids":[10,11]', "The AI Release Explained", "WHAT CHANGED",
        "A new AI model changes the landscape", "technical and industry",
        "AI industry viewers", "Background", "AI model", "12.345", "67.891",
    ):
        assert expected in text


def test_anthropic_outline_normalizes_provider_duration_proportionally() -> None:
    parsed = AnthropicYouTubeOutlineResponse(
        chapters=[anthropic_chapter(0, 100), anthropic_chapter(1, 200)]
    )
    chapters = AnthropicYouTubeOutlineGenerator(client=FakeClient(parsed)).generate_outline(
        source(), channel_focus="AI", target_minutes=15
    )
    assert [chapter.estimated_seconds for chapter in chapters] == [300, 600]


def test_anthropic_script_uses_structured_response_and_outline_context() -> None:
    parsed = AnthropicYouTubeScriptResponse(
        opening_hook="Opening hook",
        narration_sections=[narration(1), narration(0)],
        closing="Final takeaway",
    )
    client = FakeClient(parsed)
    generator: YouTubeScriptGenerator = AnthropicYouTubeScriptGenerator(
        client=client, model="script-model"
    )
    script = generator.generate_script(
        source(), [chapter(0), chapter(1)], channel_focus="AI news", target_minutes=15
    )
    assert script.title == source().selected_title
    assert [x.chapter_index for x in script.narration_sections] == [0, 1]
    call = client.messages.calls[0]
    assert call["model"] == "script-model"
    text = call_input_text(call)
    assert '"chapter_index":0' in text
    assert "Explain context" in text
    assert "67.891" in text
    assert '"english_words_minimum":1800' in text
    assert '"english_words_maximum":2700' in text
    assert '"japanese_non_whitespace_characters_minimum":3360' in text
    assert '"japanese_non_whitespace_characters_maximum":5040' in text
    assert '"target_english_words":1125' in text
    assert len(client.messages.calls) == 1


def test_anthropic_script_supplements_only_missing_chapters() -> None:
    existing = narration(0)
    missing = narration(1)
    client = SequencedClient([
        AnthropicYouTubeScriptResponse(
            opening_hook="Opening hook",
            narration_sections=[existing],
            closing="Final takeaway",
        ),
        AnthropicYouTubeNarrationSupplementResponse(narration_sections=[missing]),
    ])
    script = AnthropicYouTubeScriptGenerator(client=client).generate_script(
        source(), [chapter(0), chapter(1)], channel_focus="AI", target_minutes=15
    )
    assert [section.chapter_index for section in script.narration_sections] == [0, 1]
    assert script.narration_sections[0].narration == existing.narration.strip()
    assert len(client.messages.calls) == 2
    supplement_call = client.messages.calls[1]
    supplement_text = call_input_text(supplement_call)
    assert '"required_missing_chapter_indexes":[1]' in supplement_text
    assert '"chapter_index":0' in supplement_text
    assert '"chapter_index":1' in supplement_text


def test_anthropic_script_rejects_unrequested_supplement_chapter() -> None:
    client = SequencedClient([
        AnthropicYouTubeScriptResponse(
            opening_hook="Opening",
            narration_sections=[narration(0)],
            closing="Closing",
        ),
        SimpleNamespace(narration_sections=[narration(2)]),
    ])
    with pytest.raises(ValueError, match="unexpected chapter_index: 2"):
        AnthropicYouTubeScriptGenerator(client=client).generate_script(
            source(), [chapter(0), chapter(1)], channel_focus="AI", target_minutes=15
        )


def test_anthropic_script_reports_chapter_still_missing_after_supplement() -> None:
    # Three chapters so two are initially missing (1 and 2): the supplement schema's
    # min_length=1 constraint means it can supply a non-empty response that still
    # leaves one requested chapter uncovered, exercising the "still missing" branch.
    client = SequencedClient([
        AnthropicYouTubeScriptResponse(
            opening_hook="Opening",
            narration_sections=[narration(0)],
            closing="Closing",
        ),
        AnthropicYouTubeNarrationSupplementResponse(narration_sections=[narration(1)]),
    ])
    with pytest.raises(ValueError, match="missing chapter indexes: 2"):
        AnthropicYouTubeScriptGenerator(client=client).generate_script(
            source(),
            [chapter(0, 300), chapter(1, 300), chapter(2, 300)],
            channel_focus="AI",
            target_minutes=15,
        )


def test_anthropic_script_propagates_supplement_provider_error() -> None:
    client = SequencedClient([
        AnthropicYouTubeScriptResponse(
            opening_hook="Opening",
            narration_sections=[narration(0)],
            closing="Closing",
        ),
        RuntimeError("supplement unavailable"),
    ])
    with pytest.raises(RuntimeError, match="supplement unavailable"):
        AnthropicYouTubeScriptGenerator(client=client).generate_script(
            source(), [chapter(0), chapter(1)], channel_focus="AI", target_minutes=15
        )


@pytest.mark.parametrize("chapters", [
    [anthropic_chapter(0), anthropic_chapter(0)],
    [anthropic_chapter(0), anthropic_chapter(2)],
    [SimpleNamespace(chapter_index=0, title=" ", objective="Objective", estimated_seconds=900, key_points=["Point"])],
])
def test_anthropic_outline_rejects_malformed_output(chapters) -> None:
    with pytest.raises(ValueError):
        AnthropicYouTubeOutlineGenerator(
            client=FakeClient(SimpleNamespace(chapters=chapters))
        ).generate_outline(source(), channel_focus="AI", target_minutes=15)


@pytest.mark.parametrize("sections,closing", [
    ([narration(0)], "Closing"),
    ([narration(0, 562), narration(0, 562), narration(1, 1124)], "Closing"),
    ([narration(0), narration(1), narration(2)], "Closing"),
    ([SimpleNamespace(chapter_index=0, narration=" "), narration(1)], "Closing"),
    ([narration(0), narration(1)], " "),
])
def test_anthropic_script_rejects_malformed_output(sections, closing) -> None:
    parsed = SimpleNamespace(
        opening_hook="Opening", narration_sections=sections, closing=closing
    )
    with pytest.raises(ValueError):
        AnthropicYouTubeScriptGenerator(client=FakeClient(parsed)).generate_script(
            source(), [chapter(0), chapter(1)], channel_focus="AI", target_minutes=15
        )


@pytest.mark.parametrize("provider", ["outline", "script"])
def test_anthropic_providers_reject_missing_tool_call_output(provider: str) -> None:
    with pytest.raises(ValueError, match="structured tool call"):
        if provider == "outline":
            AnthropicYouTubeOutlineGenerator(client=NoToolCallClient()).generate_outline(
                source(), channel_focus="AI", target_minutes=15
            )
        else:
            AnthropicYouTubeScriptGenerator(client=NoToolCallClient()).generate_script(
                source(), [chapter(0), chapter(1)], channel_focus="AI", target_minutes=15
            )


@pytest.mark.parametrize("provider", ["outline", "script"])
def test_anthropic_provider_exception_propagates_without_network_or_key(provider: str) -> None:
    client = FakeClient(error=RuntimeError("API unavailable"))
    with pytest.raises(RuntimeError, match="API unavailable"):
        if provider == "outline":
            AnthropicYouTubeOutlineGenerator(client=client).generate_outline(
                source(), channel_focus="AI", target_minutes=15
            )
        else:
            AnthropicYouTubeScriptGenerator(client=client).generate_script(
                source(), [chapter(0), chapter(1)], channel_focus="AI", target_minutes=15
            )

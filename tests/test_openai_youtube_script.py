from types import SimpleNamespace
from typing import cast

import pytest

from app.openai_youtube_script import (
    OpenAIYouTubeNarrationSection,
    OpenAIYouTubeOutlineResponse,
    OpenAIYouTubeScriptChapter,
    OpenAIYouTubeScriptGenerator,
    OpenAIYouTubeScriptResponse,
    OpenAIYouTubeOutlineGenerator,
)
from app.youtube_script import (
    YouTubeOutlineGenerator,
    YouTubeScriptChapter,
    YouTubeScriptGenerator,
    YouTubeScriptSource,
)


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


def openai_chapter(index: int, seconds: int = 450, **overrides):
    values = dict(chapter_index=index, title=f"Chapter {index}",
                  objective="Explain supplied context", estimated_seconds=seconds,
                  key_points=["Context", "Impact"])
    values.update(overrides)
    return OpenAIYouTubeScriptChapter(**values)


def chapter(index: int, seconds: int = 450):
    return YouTubeScriptChapter(index, f"Chapter {index}", "Explain context", seconds, ["Context"])


def narration(index: int, words: int = 1124):
    return OpenAIYouTubeNarrationSection(
        chapter_index=index, narration="word " * words
    )


def test_openai_outline_uses_typed_responses_api_and_complete_context() -> None:
    parsed = OpenAIYouTubeOutlineResponse(chapters=[openai_chapter(1), openai_chapter(0)])
    client = FakeClient(parsed)
    generator: YouTubeOutlineGenerator = cast(
        YouTubeOutlineGenerator,
        OpenAIYouTubeOutlineGenerator(client=client, model="outline-model"),
    )
    chapters = generator.generate_outline(source(), channel_focus="AI news", target_minutes=15)
    assert [x.chapter_index for x in chapters] == [0, 1]
    call = client.responses.calls[0]
    assert call["model"] == "outline-model"
    assert call["text_format"] is OpenAIYouTubeOutlineResponse
    for expected in (
        "AI news", '"target_minutes":15', '"idea_index":2',
        '"source_article_ids":[10,11]', "The AI Release Explained", "WHAT CHANGED",
        "A new AI model changes the landscape", "technical and industry",
        "AI industry viewers", "Background", "AI model", "12.345", "67.891",
    ):
        assert expected in call["input"]


def test_openai_script_uses_typed_response_and_outline_context() -> None:
    parsed = OpenAIYouTubeScriptResponse(
        opening_hook="Opening hook",
        narration_sections=[narration(1), narration(0)],
        closing="Final takeaway",
    )
    client = FakeClient(parsed)
    generator: YouTubeScriptGenerator = cast(
        YouTubeScriptGenerator,
        OpenAIYouTubeScriptGenerator(client=client, model="script-model"),
    )
    script = generator.generate_script(
        source(), [chapter(0), chapter(1)], channel_focus="AI news", target_minutes=15
    )
    assert script.title == source().selected_title
    assert [x.chapter_index for x in script.narration_sections] == [0, 1]
    call = client.responses.calls[0]
    assert call["model"] == "script-model"
    assert call["text_format"] is OpenAIYouTubeScriptResponse
    assert '"chapter_index":0' in call["input"]
    assert "Explain context" in call["input"]
    assert "67.891" in call["input"]


@pytest.mark.parametrize("chapters", [
    [openai_chapter(0), openai_chapter(0)],
    [openai_chapter(0), openai_chapter(2)],
    [SimpleNamespace(chapter_index=0, title=" ", objective="Objective", estimated_seconds=900, key_points=["Point"])],
    [SimpleNamespace(chapter_index=0, title="Title", objective="Objective", estimated_seconds=0, key_points=["Point"])],
])
def test_openai_outline_rejects_malformed_output(chapters) -> None:
    with pytest.raises(ValueError):
        OpenAIYouTubeOutlineGenerator(
            client=FakeClient(SimpleNamespace(chapters=chapters))
        ).generate_outline(source(), channel_focus="AI", target_minutes=15)


@pytest.mark.parametrize("sections,closing", [
    ([narration(0)], "Closing"),
    ([narration(0, 562), narration(0, 562), narration(1, 1124)], "Closing"),
    ([narration(0), narration(1), narration(2)], "Closing"),
    ([SimpleNamespace(chapter_index=0, narration=" "), narration(1)], "Closing"),
    ([narration(0), narration(1)], " "),
])
def test_openai_script_rejects_malformed_output(sections, closing) -> None:
    parsed = SimpleNamespace(
        opening_hook="Opening", narration_sections=sections, closing=closing
    )
    with pytest.raises(ValueError):
        OpenAIYouTubeScriptGenerator(client=FakeClient(parsed)).generate_script(
            source(), [chapter(0), chapter(1)], channel_focus="AI", target_minutes=15
        )


@pytest.mark.parametrize("provider", ["outline", "script"])
def test_openai_providers_reject_missing_parsed_output(provider: str) -> None:
    with pytest.raises(ValueError, match="parsed"):
        if provider == "outline":
            OpenAIYouTubeOutlineGenerator(client=FakeClient()).generate_outline(
                source(), channel_focus="AI", target_minutes=15
            )
        else:
            OpenAIYouTubeScriptGenerator(client=FakeClient()).generate_script(
                source(), [chapter(0), chapter(1)], channel_focus="AI", target_minutes=15
            )


@pytest.mark.parametrize("provider", ["outline", "script"])
def test_openai_provider_exception_propagates_without_network_or_key(provider: str) -> None:
    client = FakeClient(error=RuntimeError("API unavailable"))
    with pytest.raises(RuntimeError, match="API unavailable"):
        if provider == "outline":
            OpenAIYouTubeOutlineGenerator(client=client).generate_outline(
                source(), channel_focus="AI", target_minutes=15
            )
        else:
            OpenAIYouTubeScriptGenerator(client=client).generate_script(
                source(), [chapter(0), chapter(1)], channel_focus="AI", target_minutes=15
            )

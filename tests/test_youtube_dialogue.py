from dataclasses import replace

import pytest

from app.youtube_dialogue import (
    DEFAULT_DIALOGUE_CHARACTERS,
    DialogueChapter,
    DialogueCharacters,
    DialogueLine,
    LocalYouTubeDialogueConverter,
    YouTubeDialogueScript,
    build_youtube_dialogue_source,
    convert_youtube_script_to_dialogue,
    dialogue_text,
    validate_dialogue_chapter,
    validate_dialogue_line,
    validate_dialogue_script,
)
from app.youtube_ideas import YouTubeIdea
from app.youtube_packaging import YouTubePackagingCandidate
from app.youtube_potential import YouTubePotentialResult
from app.youtube_script import (
    LocalYouTubeOutlineGenerator,
    LocalYouTubeScriptGenerator,
    YouTubeNarrationSection,
    YouTubeScript,
    YouTubeScriptChapter,
    build_youtube_script_source,
    estimate_script_minutes,
    generate_youtube_script,
)


def source_script():
    idea = YouTubeIdea(
        source_article_ids=[1], title="AI release", hook="A new model was released.",
        angle="Explain the technical and industry implications.",
        target_audience="AI viewers", estimated_length_minutes=15,
        thumbnail_text="NEW MODEL", chapters=["Background", "Release", "Impact"],
        seo_keywords=["AI model", "AI news"],
    )
    potential = YouTubePotentialResult(
        idea_index=0, youtube_potential_score=70, topic_appeal_score=70,
        clarity_score=70, surprise_score=70, searchability_score=70,
        visual_explainability_score=70, reason="Valid",
    )
    packaging = YouTubePackagingCandidate(
        candidate_index=0, title="The AI Release Explained", thumbnail_text="WHAT CHANGED",
        rationale="Clear", packaging_score=75, clarity_score=75, curiosity_score=75,
        specificity_score=75, truthfulness_score=75, thumbnail_synergy_score=75,
        evaluation_reason="Valid",
    )
    source = build_youtube_script_source(idea, potential, packaging)
    return generate_youtube_script(
        source, LocalYouTubeOutlineGenerator(), LocalYouTubeScriptGenerator(),
        channel_focus="AI news", target_minutes=15,
    )


def local_dialogue():
    return convert_youtube_script_to_dialogue(
        source_script(), LocalYouTubeDialogueConverter(), channel_focus="AI news"
    )


def all_lines(script):
    return [
        *script.opening_lines,
        *(line for chapter in script.chapters for line in chapter.lines),
        *script.closing_lines,
    ]


def test_default_characters_and_invalid_config() -> None:
    assert DEFAULT_DIALOGUE_CHARACTERS.explainer_name == "さび助"
    assert DEFAULT_DIALOGUE_CHARACTERS.learner_name == "ハル"
    for values in (("", "ハル"), ("さび助", " "), ("same", "same")):
        with pytest.raises(ValueError): DialogueCharacters(*values)


def test_source_conversion_preserves_complete_script_order() -> None:
    script = source_script()
    source = build_youtube_dialogue_source(script)
    assert source.title == script.title
    assert source.thumbnail_text == script.thumbnail_text
    assert source.target_minutes == 15
    assert source.chapters == script.chapters
    assert source.narration_sections == script.narration_sections
    assert source.closing == script.closing
    assert source.seo_keywords == script.seo_keywords


def test_local_converter_is_deterministic_and_structurally_complete() -> None:
    first = local_dialogue()
    second = local_dialogue()
    assert first == second
    assert [chapter.chapter_index for chapter in first.chapters] == list(range(8))
    assert [chapter.title for chapter in first.chapters] == [
        chapter.title for chapter in source_script().chapters
    ]
    speakers = {line.speaker for line in all_lines(first)}
    assert speakers == {"さび助", "ハル"}
    assert [line.line_index for line in first.opening_lines] == [0, 1]
    assert [line.line_index for line in first.closing_lines] == [0, 1]
    assert all(
        [line.line_index for line in chapter.lines] == [0, 1, 2]
        for chapter in first.chapters
    )


@pytest.mark.parametrize("line", [
    DialogueLine(0, "ナレーター", "Text"),
    DialogueLine(0, "さび助", "Text"),
])
def test_line_validation_rejects_third_speaker_or_wrong_custom_config(line) -> None:
    with pytest.raises(ValueError):
        validate_dialogue_line(line, DialogueCharacters("Explainer", "Learner"))


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_line_rejects_invalid_index(value) -> None:
    with pytest.raises(ValueError): DialogueLine(value, "さび助", "Text")


def test_blank_dialogue_line_rejected() -> None:
    with pytest.raises(ValueError): DialogueLine(0, "さび助", " ")


@pytest.mark.parametrize("indexes", [[1], [0, 2], [0, 0]])
def test_chapter_rejects_nonsequential_line_indexes(indexes) -> None:
    chapter = DialogueChapter(
        0, "Title", [DialogueLine(index, "さび助", "Text") for index in indexes]
    )
    with pytest.raises(ValueError): validate_dialogue_chapter(chapter, DEFAULT_DIALOGUE_CHARACTERS)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "unknown", "reordered"])
def test_dialogue_rejects_bad_chapter_coverage_or_order(kind: str) -> None:
    dialogue = local_dialogue()
    chapters = list(dialogue.chapters)
    if kind == "missing": chapters.pop(1)
    elif kind == "duplicate": chapters[2] = chapters[1]
    elif kind == "unknown": chapters[-1] = replace(chapters[-1], chapter_index=99)
    else: chapters[1], chapters[2] = chapters[2], chapters[1]
    with pytest.raises(ValueError, match="order and coverage"):
        validate_dialogue_script(
            replace(dialogue, chapters=chapters), build_youtube_dialogue_source(source_script())
        )


@pytest.mark.parametrize("speaker", ["さび助", "ハル"])
def test_dialogue_requires_both_speakers(speaker: str) -> None:
    dialogue = local_dialogue()
    opening = [replace(line, speaker=speaker) for line in dialogue.opening_lines]
    chapters = [replace(
        chapter, lines=[replace(line, speaker=speaker) for line in chapter.lines]
    ) for chapter in dialogue.chapters]
    closing = [replace(line, speaker=speaker) for line in dialogue.closing_lines]
    with pytest.raises(ValueError, match="both"):
        validate_dialogue_script(
            replace(dialogue, opening_lines=opening, chapters=chapters, closing_lines=closing),
            build_youtube_dialogue_source(source_script()),
        )


def test_opening_and_closing_are_required() -> None:
    dialogue = local_dialogue()
    with pytest.raises(ValueError): replace(dialogue, opening_lines=[])
    with pytest.raises(ValueError): replace(dialogue, closing_lines=[])


def test_dialogue_duration_reuses_script_estimator() -> None:
    dialogue = local_dialogue()
    assert estimate_script_minutes(dialogue_text(dialogue)) == pytest.approx(
        dialogue.target_minutes, rel=0.25
    )


@pytest.mark.parametrize("word_count", [300, 6000])
def test_dialogue_rejects_extremely_short_or_long_runtime(word_count: int) -> None:
    dialogue = local_dialogue()
    replacement_text = "word " * (word_count // len(all_lines(dialogue)))
    opening = [replace(line, text=replacement_text) for line in dialogue.opening_lines]
    chapters = [replace(
        chapter, lines=[replace(line, text=replacement_text) for line in chapter.lines]
    ) for chapter in dialogue.chapters]
    closing = [replace(line, text=replacement_text) for line in dialogue.closing_lines]
    with pytest.raises(ValueError, match="runtime"):
        validate_dialogue_script(
            replace(dialogue, opening_lines=opening, chapters=chapters, closing_lines=closing),
            build_youtube_dialogue_source(source_script()),
        )


def test_service_rejects_empty_focus_before_converter() -> None:
    class Never:
        def convert(self, *args, **kwargs): raise AssertionError("converter called")
    with pytest.raises(ValueError):
        convert_youtube_script_to_dialogue(source_script(), Never(), channel_focus=" ")


def test_custom_character_names_are_supported() -> None:
    characters = DialogueCharacters("Teacher", "Student")
    dialogue = convert_youtube_script_to_dialogue(
        source_script(), LocalYouTubeDialogueConverter(),
        channel_focus="AI news", characters=characters,
    )
    assert {line.speaker for line in all_lines(dialogue)} == {"Teacher", "Student"}


def test_local_converter_preserves_approximate_japanese_runtime() -> None:
    japanese_script = YouTubeScript(
        title="AIモデル解説", thumbnail_text="何が変わった", target_minutes=15,
        opening_hook="今回の変化を確認します。",
        chapters=[
            YouTubeScriptChapter(0, "背景", "背景を説明する", 450, ["背景"]),
            YouTubeScriptChapter(1, "影響", "影響を説明する", 450, ["影響"]),
        ],
        narration_sections=[
            YouTubeNarrationSection(0, "背景を説明します。" * 232),
            YouTubeNarrationSection(1, "影響を説明します。" * 232),
        ],
        closing="重要な点を振り返ります。", seo_keywords=["AI", "モデル"],
    )
    dialogue = convert_youtube_script_to_dialogue(
        japanese_script, LocalYouTubeDialogueConverter(), channel_focus="AIニュース"
    )
    assert estimate_script_minutes(dialogue_text(dialogue)) == pytest.approx(15, rel=0.25)

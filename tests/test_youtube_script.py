import math

import pytest

from app.youtube_ideas import YouTubeIdea
from app.youtube_packaging import YouTubePackagingCandidate
from app.youtube_potential import RankedYouTubeIdea, YouTubePotentialResult
from app.youtube_script import (
    LocalYouTubeOutlineGenerator,
    LocalYouTubeScriptGenerator,
    YouTubeNarrationSection,
    YouTubeScript,
    YouTubeScriptChapter,
    YouTubeScriptSource,
    build_youtube_script_source,
    estimate_script_minutes,
    generate_youtube_script,
    validate_outline,
    validate_script,
    validate_target_minutes,
)


def idea() -> YouTubeIdea:
    return YouTubeIdea(
        source_article_ids=[10, 11], title="AI model release",
        hook="A new AI model changes the technical landscape.",
        angle="Explain the release and its industry implications.",
        target_audience="AI industry viewers", estimated_length_minutes=15,
        thumbnail_text="NEW MODEL", chapters=["Background", "Release", "Impact"],
        seo_keywords=["AI model", "AI news"],
    )


def potential() -> YouTubePotentialResult:
    return YouTubePotentialResult(
        idea_index=2, youtube_potential_score=12.345, topic_appeal_score=50,
        clarity_score=50, surprise_score=50, searchability_score=50,
        visual_explainability_score=50, reason="Valid",
    )


def packaging() -> YouTubePackagingCandidate:
    return YouTubePackagingCandidate(
        candidate_index=0, title="The AI Release Explained", thumbnail_text="WHAT CHANGED",
        rationale="Clear packaging", packaging_score=67.891, clarity_score=70,
        curiosity_score=70, specificity_score=70, truthfulness_score=70,
        thumbnail_synergy_score=70, evaluation_reason="Valid",
    )


def source() -> YouTubeScriptSource:
    return build_youtube_script_source(idea(), potential(), packaging())


def chapter(index: int, seconds: int = 300) -> YouTubeScriptChapter:
    return YouTubeScriptChapter(
        chapter_index=index, title=f"Chapter {index}", objective="Explain the context",
        estimated_seconds=seconds, key_points=["Known context", "Implications"],
    )


def script_with_sections(sections, text: str = "word ") -> YouTubeScript:
    chapters = [chapter(0, 450), chapter(1, 450)]
    return YouTubeScript(
        title="Title", thumbnail_text="Thumb", target_minutes=15,
        opening_hook="word", chapters=chapters, narration_sections=sections,
        closing="word", seo_keywords=["keyword"],
    )


def test_source_builder_copies_selected_packaging_and_scores_exactly() -> None:
    built = build_youtube_script_source(RankedYouTubeIdea(idea(), potential()), packaging())
    assert built.idea_index == 2
    assert built.source_article_ids == [10, 11]
    assert built.selected_title == packaging().title
    assert built.selected_thumbnail_text == packaging().thumbnail_text
    assert built.youtube_potential_score == 12.345
    assert built.packaging_score == 67.891


@pytest.mark.parametrize("value", [5, 15, 30])
def test_target_minutes_accepts_supported_range(value: int) -> None:
    assert validate_target_minutes(value) == value


@pytest.mark.parametrize("value", [0, 4, 31, True, 1.5])
def test_target_minutes_rejects_invalid_values(value) -> None:
    with pytest.raises(ValueError): validate_target_minutes(value)


@pytest.mark.parametrize("field,value", [
    ("idea_index", -1), ("idea_index", True), ("source_article_ids", []),
    ("source_article_ids", [1, 1]), ("source_article_ids", [True]),
    ("selected_title", " "), ("selected_thumbnail_text", ""), ("hook", "\n"),
    ("angle", ""), ("target_audience", " "), ("estimated_length_minutes", 0),
    ("estimated_length_minutes", True), ("original_chapters", []),
    ("original_chapters", [" "]), ("seo_keywords", []), ("seo_keywords", [""]),
    ("youtube_potential_score", -1), ("youtube_potential_score", 101),
    ("youtube_potential_score", True), ("packaging_score", -1),
    ("packaging_score", 101), ("packaging_score", float("nan")),
])
def test_source_rejects_invalid_fields(field: str, value) -> None:
    values = source().__dict__.copy()
    values[field] = value
    with pytest.raises(ValueError): YouTubeScriptSource(**values)


@pytest.mark.parametrize("field,value", [
    ("chapter_index", -1), ("chapter_index", True), ("title", " "),
    ("objective", ""), ("estimated_seconds", 0), ("estimated_seconds", True),
    ("key_points", []), ("key_points", [" "]),
])
def test_chapter_rejects_invalid_fields(field: str, value) -> None:
    values = chapter(0).__dict__.copy()
    values[field] = value
    with pytest.raises(ValueError): YouTubeScriptChapter(**values)


def test_local_outline_is_deterministic_sequential_and_on_duration() -> None:
    generator = LocalYouTubeOutlineGenerator()
    first = generator.generate_outline(source(), channel_focus="AI news", target_minutes=15)
    second = generator.generate_outline(source(), channel_focus="AI news", target_minutes=15)
    assert first == second
    assert len(first) == 8
    assert [item.chapter_index for item in first] == list(range(8))
    assert all(item.estimated_seconds > 0 for item in first)
    assert sum(item.estimated_seconds for item in first) == 900


def test_outline_accepts_provider_order_and_normalizes_it() -> None:
    result = validate_outline([chapter(2), chapter(0), chapter(1)], 15)
    assert [item.chapter_index for item in result] == [0, 1, 2]


@pytest.mark.parametrize("chapters", [
    [chapter(0), chapter(2), chapter(3)],
    [chapter(0), chapter(1), chapter(1)],
    [chapter(0, 150), chapter(1, 150)],
])
def test_outline_rejects_missing_duplicate_or_bad_duration(chapters) -> None:
    with pytest.raises(ValueError): validate_outline(chapters, 15)


def test_local_full_script_is_deterministic_and_covers_every_chapter() -> None:
    outline = LocalYouTubeOutlineGenerator().generate_outline(
        source(), channel_focus="AI news", target_minutes=15
    )
    generator = LocalYouTubeScriptGenerator()
    first = generator.generate_script(
        source(), outline, channel_focus="AI news", target_minutes=15
    )
    second = generator.generate_script(
        source(), outline, channel_focus="AI news", target_minutes=15
    )
    assert first == second
    assert [x.chapter_index for x in first.narration_sections] == list(range(8))
    assert first.seo_keywords == source().seo_keywords
    assert validate_script(first) == first


@pytest.mark.parametrize("indexes", [[0], [0, 0], [0, 1, 2]])
def test_script_rejects_missing_duplicate_or_unknown_narration(indexes) -> None:
    words = "word " * 1124
    sections = [YouTubeNarrationSection(index, words) for index in indexes]
    with pytest.raises(ValueError): validate_script(script_with_sections(sections))


def test_blank_narration_rejected() -> None:
    with pytest.raises(ValueError): YouTubeNarrationSection(0, " ")


def test_runtime_estimator_uses_documented_japanese_and_english_rates() -> None:
    assert estimate_script_minutes("あ" * 280) == 1
    assert estimate_script_minutes("word " * 150) == 1


@pytest.mark.parametrize("word_count", [300, 6000])
def test_script_rejects_extremely_short_or_long_text(word_count: int) -> None:
    words = "word " * (word_count // 2)
    script = script_with_sections([
        YouTubeNarrationSection(0, words), YouTubeNarrationSection(1, words)
    ])
    with pytest.raises(ValueError, match="runtime"): validate_script(script)


def test_service_rejects_blank_focus_before_provider_work() -> None:
    class Never:
        def generate_outline(self, *args, **kwargs): raise AssertionError("called")
    with pytest.raises(ValueError):
        generate_youtube_script(source(), Never(), Never(), channel_focus=" ")


@pytest.mark.parametrize("target", [5, 15, 30])
def test_end_to_end_local_generation_for_supported_durations(target: int) -> None:
    script = generate_youtube_script(
        source(), LocalYouTubeOutlineGenerator(), LocalYouTubeScriptGenerator(),
        channel_focus="AI news", target_minutes=target,
    )
    assert script.target_minutes == target
    assert math.isclose(estimate_script_minutes(" ".join(
        [script.opening_hook, *(x.narration for x in script.narration_sections), script.closing]
    )), target, rel_tol=0.20)

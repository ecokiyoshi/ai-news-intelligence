import pytest

from app.youtube_ideas import YouTubeIdea
from app.youtube_packaging import (
    LocalYouTubePackagingEvaluator,
    LocalYouTubePackagingGenerator,
    YouTubePackagingCandidate,
    YouTubePackagingDimensions,
    YouTubePackagingDraft,
    YouTubePackagingSource,
    YouTubePackagingWeights,
    build_youtube_packaging_source,
    calculate_youtube_packaging_score,
    generate_youtube_packaging,
    rank_youtube_packaging_candidates,
    select_top_youtube_packaging,
    validate_packaging_dimensions,
    validate_packaging_drafts,
)
from app.youtube_potential import RankedYouTubeIdea, YouTubePotentialResult


def idea() -> YouTubeIdea:
    return YouTubeIdea(
        source_article_ids=[1], title="AI launch explained", hook="What changed",
        angle="Technical and industry impact", target_audience="AI viewers",
        estimated_length_minutes=8, thumbnail_text="NEW MODEL",
        chapters=["Launch", "Impact"], seo_keywords=["AI", "model"],
    )


def potential(score: float = 12.345) -> YouTubePotentialResult:
    return YouTubePotentialResult(
        idea_index=0, youtube_potential_score=score, topic_appeal_score=50,
        clarity_score=50, surprise_score=50, searchability_score=50,
        visual_explainability_score=50, reason="Valid potential",
    )


def source() -> YouTubePackagingSource:
    return build_youtube_packaging_source(idea(), potential())


def draft(index: int, title: str | None = None, thumbnail: str | None = None):
    return YouTubePackagingDraft(index, title or f"Title {index}", thumbnail or f"Thumb {index}", f"Rationale {index}")


def dimension(index: int, **overrides):
    values = dict(candidate_index=index, clarity_score=80, curiosity_score=80,
                  specificity_score=80, truthfulness_score=80,
                  thumbnail_synergy_score=80, reason="Clear and truthful")
    values.update(overrides)
    return YouTubePackagingDimensions(**values)


def candidate(index: int, score: float, **overrides):
    values = dict(candidate_index=index, title=f"Title {index}", thumbnail_text=f"Thumb {index}", rationale="Valid rationale",
                  packaging_score=score, clarity_score=80, curiosity_score=80,
                  specificity_score=80, truthfulness_score=80,
                  thumbnail_synergy_score=80, evaluation_reason="Valid")
    values.update(overrides)
    return YouTubePackagingCandidate(**values)


def test_source_builder_copies_existing_potential_score_exactly() -> None:
    built = build_youtube_packaging_source(RankedYouTubeIdea(idea(), potential()))
    assert built.youtube_potential_score == 12.345
    assert built.title == idea().title
    assert built.chapters == idea().chapters


@pytest.mark.parametrize("count", [1, 3, 5, 10])
def test_local_generation_is_deterministic_distinct_and_exact(count: int) -> None:
    generator = LocalYouTubePackagingGenerator()
    first = generator.generate(source(), channel_focus="AI news", candidate_count=count)
    second = generator.generate(source(), channel_focus="AI news", candidate_count=count)
    assert first == second
    assert [item.candidate_index for item in first] == list(range(count))
    assert len({(item.title, item.thumbnail_text) for item in first}) == count


@pytest.mark.parametrize("count", [0, -1, True, 1.5, 11])
def test_service_rejects_invalid_candidate_count_before_provider(count) -> None:
    class Never:
        def generate(self, *args, **kwargs): raise AssertionError("provider called")
    with pytest.raises(ValueError):
        generate_youtube_packaging(source(), Never(), Never(), channel_focus="AI", candidate_count=count)


def test_draft_validation_normalizes_order_and_rejects_bad_coverage() -> None:
    assert [item.candidate_index for item in validate_packaging_drafts([draft(2), draft(0), draft(1)], 3)] == [0, 1, 2]
    for drafts in ([draft(0), draft(0)], [draft(0), draft(2)], [draft(0)]):
        with pytest.raises(ValueError): validate_packaging_drafts(drafts, 2)


def test_draft_validation_rejects_normalized_duplicate_pair() -> None:
    with pytest.raises(ValueError, match="duplicate title"):
        validate_packaging_drafts([
            draft(0, "  Big   NEWS ", "WATCH  NOW"),
            draft(1, "big news", " watch now "),
        ], 2)


@pytest.mark.parametrize("field", ["title", "thumbnail_text", "rationale"])
def test_draft_rejects_blank_copy(field: str) -> None:
    values = dict(candidate_index=0, title="Title", thumbnail_text="Thumb", rationale="Why")
    values[field] = "  "
    with pytest.raises(ValueError): YouTubePackagingDraft(**values)


def test_dimension_validation_normalizes_provider_order() -> None:
    result = validate_packaging_dimensions([dimension(2), dimension(0), dimension(1)], 3)
    assert [item.candidate_index for item in result] == [0, 1, 2]


@pytest.mark.parametrize("field", ["clarity_score", "curiosity_score", "specificity_score", "truthfulness_score", "thumbnail_synergy_score"])
@pytest.mark.parametrize("value", [-1, 101, True, 1.5])
def test_dimensions_reject_invalid_scores(field: str, value) -> None:
    with pytest.raises(ValueError): dimension(0, **{field: value})


def test_default_and_custom_formula() -> None:
    assert calculate_youtube_packaging_score(100, 100, 100, 100, 100) == 100
    assert calculate_youtube_packaging_score(0, 0, 0, 0, 0) == 0
    assert calculate_youtube_packaging_score(50, 60, 70, 80, 90) == pytest.approx(68)
    weights = YouTubePackagingWeights(1, 0, 0, 0, 0)
    assert calculate_youtube_packaging_score(42, 100, 100, 100, 100, weights) == 42


@pytest.mark.parametrize("weights", [
    dict(clarity=True), dict(clarity=float("nan")), dict(clarity=float("inf")),
    dict(clarity=-0.1), dict(clarity=1.1), dict(clarity=0.1),
])
def test_invalid_weights_rejected(weights) -> None:
    with pytest.raises(ValueError): YouTubePackagingWeights(**weights)


def test_service_builds_scores_and_ranks() -> None:
    results = generate_youtube_packaging(
        source(), LocalYouTubePackagingGenerator(), LocalYouTubePackagingEvaluator(),
        channel_focus="AI news", candidate_count=5,
    )
    assert len(results) == 5
    assert all(0 <= item.packaging_score <= 100 for item in results)
    assert results == rank_youtube_packaging_candidates(results)


def test_ranking_tie_breakers_are_deterministic() -> None:
    items = [
        candidate(3, 90, truthfulness_score=90, curiosity_score=80, clarity_score=80),
        candidate(2, 90, truthfulness_score=90, curiosity_score=90, clarity_score=70),
        candidate(1, 90, truthfulness_score=90, curiosity_score=90, clarity_score=80),
        candidate(0, 91, truthfulness_score=0, curiosity_score=0, clarity_score=0),
    ]
    assert [x.candidate_index for x in rank_youtube_packaging_candidates(items)] == [0, 1, 2, 3]


def test_select_top_applies_threshold_and_limit() -> None:
    ranked = rank_youtube_packaging_candidates([candidate(0, 70), candidate(1, 90), candidate(2, 80)])
    assert [x.packaging_score for x in select_top_youtube_packaging(ranked, 2, 80)] == [90, 80]


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_select_top_rejects_invalid_limit(limit) -> None:
    with pytest.raises(ValueError): select_top_youtube_packaging([], limit)


@pytest.mark.parametrize("minimum", [-1, 101, True, float("nan"), float("inf")])
def test_select_top_rejects_invalid_threshold(minimum) -> None:
    with pytest.raises(ValueError): select_top_youtube_packaging([], 1, minimum)


def test_local_evaluator_deterministic() -> None:
    drafts = [draft(0), draft(1)]
    evaluator = LocalYouTubePackagingEvaluator()
    assert evaluator.evaluate(source(), drafts, channel_focus="AI") == evaluator.evaluate(source(), drafts, channel_focus="AI")

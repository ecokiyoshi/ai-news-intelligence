import pytest

from app.youtube_ideas import YouTubeIdea
from app.youtube_potential import (
    LocalYouTubePotentialScorer,
    RankedYouTubeIdea,
    YouTubePotentialDimensions,
    YouTubePotentialResult,
    YouTubePotentialWeights,
    calculate_youtube_potential_score,
    rank_youtube_ideas,
    score_youtube_ideas,
    select_top_youtube_ideas,
)


def idea(number: int = 1) -> YouTubeIdea:
    return YouTubeIdea(
        source_article_ids=[number],
        title=f"Idea {number}",
        hook=f"Hook {number}",
        angle=f"Angle {number}",
        target_audience="Technology viewers",
        estimated_length_minutes=8,
        thumbnail_text=f"IDEA {number}",
        chapters=["Introduction", "Explanation"],
        seo_keywords=["technology", f"idea {number}"],
    )


def dimensions(index: int, **overrides) -> YouTubePotentialDimensions:
    values = {
        "idea_index": index,
        "topic_appeal_score": 80,
        "clarity_score": 70,
        "surprise_score": 60,
        "searchability_score": 50,
        "visual_explainability_score": 40,
        "reason": " Clear potential. ",
    }
    values.update(overrides)
    return YouTubePotentialDimensions(**values)


def potential(index: int, score: float, **overrides) -> YouTubePotentialResult:
    values = {
        "idea_index": index,
        "youtube_potential_score": score,
        "topic_appeal_score": 80,
        "clarity_score": 70,
        "surprise_score": 60,
        "searchability_score": 50,
        "visual_explainability_score": 40,
        "reason": "Potential reason",
    }
    values.update(overrides)
    return YouTubePotentialResult(**values)


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        ((100, 100, 100, 100, 100), 100.0),
        ((0, 0, 0, 0, 0), 0.0),
        ((100, 50, 50, 0, 0), 50.0),
    ],
)
def test_default_formula(scores, expected) -> None:
    assert calculate_youtube_potential_score(*scores) == expected


def test_custom_weights_use_core_formula() -> None:
    weights = YouTubePotentialWeights(1.0, 0.0, 0.0, 0.0, 0.0)
    assert calculate_youtube_potential_score(73, 1, 2, 3, 4, weights) == 73.0


@pytest.mark.parametrize(
    "values",
    [
        (-0.1, 0.2, 0.2, 0.3, 0.4),
        (1.1, 0, 0, 0, 0),
        (float("nan"), 0.2, 0.2, 0.3, 0.3),
        (float("inf"), 0, 0, 0, 0),
        (True, 0, 0, 0, 0),
        (0.2, 0.2, 0.2, 0.2, 0.1),
    ],
)
def test_invalid_weights_are_rejected(values) -> None:
    with pytest.raises(ValueError):
        YouTubePotentialWeights(*values)


@pytest.mark.parametrize(
    "field",
    [
        "topic_appeal_score",
        "clarity_score",
        "surprise_score",
        "searchability_score",
        "visual_explainability_score",
    ],
)
@pytest.mark.parametrize("value", [-1, 101, True, 1.5])
def test_invalid_dimension_scores_are_rejected(field, value) -> None:
    with pytest.raises(ValueError):
        dimensions(0, **{field: value})


@pytest.mark.parametrize("index", [-1, True, 1.5])
def test_invalid_idea_index_is_rejected(index) -> None:
    with pytest.raises(ValueError):
        dimensions(index)


def test_local_scorer_is_deterministic() -> None:
    ideas = [idea(1), idea(2)]
    scorer = LocalYouTubePotentialScorer()
    first = scorer.score(ideas, channel_focus="AI news")
    second = scorer.score(ideas, channel_focus="AI news")
    assert first == second
    assert [result.idea_index for result in first] == [0, 1]


@pytest.mark.parametrize(
    ("ideas", "focus", "weights"),
    [
        ([], "AI news", YouTubePotentialWeights()),
        ([idea()], "", YouTubePotentialWeights()),
        ([idea()], "   ", YouTubePotentialWeights()),
    ],
)
def test_invalid_request_fails_before_scorer(ideas, focus, weights) -> None:
    class UnexpectedScorer:
        def score(self, ideas, *, channel_focus):
            raise AssertionError("scorer must not be called")

    with pytest.raises(ValueError):
        score_youtube_ideas(
            ideas, UnexpectedScorer(), channel_focus=focus, weights=weights
        )


def test_tampered_invalid_weights_fail_before_scorer() -> None:
    class UnexpectedScorer:
        def score(self, ideas, *, channel_focus):
            raise AssertionError("scorer must not be called")

    weights = YouTubePotentialWeights()
    object.__setattr__(weights, "topic_appeal", 2.0)
    with pytest.raises(ValueError):
        score_youtube_ideas(
            [idea()], UnexpectedScorer(), channel_focus="AI news", weights=weights
        )


def test_service_orders_results_and_calculates_final_score_in_core() -> None:
    class ReorderedScorer:
        def score(self, ideas, *, channel_focus):
            return [
                dimensions(2, topic_appeal_score=30),
                dimensions(0, topic_appeal_score=100, clarity_score=50, surprise_score=50,
                           searchability_score=0, visual_explainability_score=0),
                dimensions(1, topic_appeal_score=20),
            ]

    results = score_youtube_ideas(
        [idea(1), idea(2), idea(3)],
        ReorderedScorer(),
        channel_focus="AI news",
    )
    assert [result.idea_index for result in results] == [0, 1, 2]
    assert results[0].youtube_potential_score == 50.0


@pytest.mark.parametrize(
    "returned",
    [
        [dimensions(0), dimensions(0), dimensions(2)],
        [dimensions(0), dimensions(2)],
        [dimensions(0), dimensions(1), dimensions(3)],
        [dimensions(0), dimensions(1), dimensions(2), dimensions(3)],
    ],
)
def test_service_rejects_invalid_result_index_mapping(returned) -> None:
    class InvalidScorer:
        def score(self, ideas, *, channel_focus):
            return returned

    with pytest.raises(ValueError):
        score_youtube_ideas(
            [idea(1), idea(2), idea(3)], InvalidScorer(), channel_focus="AI news"
        )


def test_rank_youtube_ideas_sorts_by_score_and_tie_breakers() -> None:
    ideas = [idea(index + 1) for index in range(5)]
    results = [
        potential(0, 70, topic_appeal_score=80),
        potential(1, 90),
        potential(2, 70, topic_appeal_score=90, surprise_score=40),
        potential(3, 70, topic_appeal_score=90, surprise_score=70, searchability_score=40),
        potential(4, 70, topic_appeal_score=90, surprise_score=70, searchability_score=60),
    ]
    ranked = rank_youtube_ideas(ideas, results)
    assert [item.potential.idea_index for item in ranked] == [1, 4, 3, 2, 0]


def test_rank_tie_uses_original_order_last() -> None:
    ideas = [idea(1), idea(2)]
    results = [potential(0, 80), potential(1, 80)]
    assert [item.potential.idea_index for item in rank_youtube_ideas(ideas, results)] == [0, 1]


def test_select_top_applies_threshold_then_limit() -> None:
    ranked = [
        RankedYouTubeIdea(idea(index + 1), potential(index, score))
        for index, score in enumerate([95, 90, 85, 75, 60])
    ]
    selected = select_top_youtube_ideas(
        ranked, limit=3, minimum_potential_score=80
    )
    assert [item.potential.youtube_potential_score for item in selected] == [95, 90, 85]


@pytest.mark.parametrize("limit", [0, -1, True])
def test_invalid_selection_limit_is_rejected(limit) -> None:
    with pytest.raises(ValueError):
        select_top_youtube_ideas([], limit=limit)


@pytest.mark.parametrize("minimum", [-1, 101, True, float("nan")])
def test_invalid_selection_minimum_is_rejected(minimum) -> None:
    with pytest.raises(ValueError):
        select_top_youtube_ideas([], minimum_potential_score=minimum)

"""Provider-independent YouTube potential scoring and idea ranking."""

import math
from dataclasses import dataclass
from typing import Protocol

from app.youtube_ideas import YouTubeIdea, validate_youtube_idea


def _number(name: str, value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _dimension_score(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not 0 <= value <= 100:
        raise ValueError(f"{name} must be between 0 and 100")
    return value


def _idea_index(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("idea_index must be a non-negative integer")
    return value


def _reason(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("reason must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class YouTubePotentialWeights:
    """Immutable weights used by the core potential-score formula."""

    topic_appeal: float = 0.30
    clarity: float = 0.20
    surprise: float = 0.20
    searchability: float = 0.15
    visual_explainability: float = 0.15

    def __post_init__(self) -> None:
        values = []
        for name in (
            "topic_appeal",
            "clarity",
            "surprise",
            "searchability",
            "visual_explainability",
        ):
            value = _number(name, getattr(self, name))
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
            object.__setattr__(self, name, value)
            values.append(value)
        if not math.isclose(sum(values), 1.0):
            raise ValueError("YouTube potential weights must sum to 1.0")


DEFAULT_YOUTUBE_POTENTIAL_WEIGHTS = YouTubePotentialWeights()


@dataclass(frozen=True)
class YouTubePotentialDimensions:
    """Provider-returned dimension scores without an overall score."""

    idea_index: int
    topic_appeal_score: int
    clarity_score: int
    surprise_score: int
    searchability_score: int
    visual_explainability_score: int
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "idea_index", _idea_index(self.idea_index))
        for name in (
            "topic_appeal_score",
            "clarity_score",
            "surprise_score",
            "searchability_score",
            "visual_explainability_score",
        ):
            object.__setattr__(self, name, _dimension_score(name, getattr(self, name)))
        object.__setattr__(self, "reason", _reason(self.reason))


@dataclass(frozen=True)
class YouTubePotentialResult:
    """Core-calculated overall score with its validated dimensions."""

    idea_index: int
    youtube_potential_score: float
    topic_appeal_score: int
    clarity_score: int
    surprise_score: int
    searchability_score: int
    visual_explainability_score: int
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "idea_index", _idea_index(self.idea_index))
        potential = _number("youtube_potential_score", self.youtube_potential_score)
        if not 0 <= potential <= 100:
            raise ValueError("youtube_potential_score must be between 0 and 100")
        object.__setattr__(self, "youtube_potential_score", potential)
        for name in (
            "topic_appeal_score",
            "clarity_score",
            "surprise_score",
            "searchability_score",
            "visual_explainability_score",
        ):
            object.__setattr__(self, name, _dimension_score(name, getattr(self, name)))
        object.__setattr__(self, "reason", _reason(self.reason))


class YouTubePotentialScorer(Protocol):
    """Interface implemented by YouTube potential providers."""

    def score(
        self, ideas: list[YouTubeIdea], *, channel_focus: str
    ) -> list[YouTubePotentialDimensions]:
        """Return dimension scores for every supplied idea."""


def validate_potential_weights(
    weights: YouTubePotentialWeights,
) -> YouTubePotentialWeights:
    """Defensively validate an immutable weight configuration."""

    if not isinstance(weights, YouTubePotentialWeights):
        raise ValueError("weights must be YouTubePotentialWeights")
    return YouTubePotentialWeights(**weights.__dict__)


def validate_scoring_request(
    ideas: list[YouTubeIdea], channel_focus: str, weights: YouTubePotentialWeights
) -> tuple[list[YouTubeIdea], str, YouTubePotentialWeights]:
    """Validate ideas, focus, and weights before provider work starts."""

    if not isinstance(ideas, list) or not ideas:
        raise ValueError("ideas must be a non-empty list")
    validated_ideas = [validate_youtube_idea(idea) for idea in ideas]
    if not isinstance(channel_focus, str) or not channel_focus.strip():
        raise ValueError("channel_focus must be a non-empty string")
    return validated_ideas, channel_focus.strip(), validate_potential_weights(weights)


def validate_dimension_results(
    dimensions: list[YouTubePotentialDimensions], idea_count: int
) -> list[YouTubePotentialDimensions]:
    """Require exactly one validated result for each input idea index."""

    if not isinstance(dimensions, list) or len(dimensions) != idea_count:
        raise ValueError("scorer must return exactly one result per idea")
    by_index: dict[int, YouTubePotentialDimensions] = {}
    for dimension in dimensions:
        if not isinstance(dimension, YouTubePotentialDimensions):
            raise ValueError("scorer must return YouTubePotentialDimensions values")
        validated = YouTubePotentialDimensions(**dimension.__dict__)
        if validated.idea_index >= idea_count:
            raise ValueError("provider returned an out-of-range idea_index")
        if validated.idea_index in by_index:
            raise ValueError("provider returned a duplicate idea_index")
        by_index[validated.idea_index] = validated
    expected = set(range(idea_count))
    if set(by_index) != expected:
        raise ValueError("provider results are missing an idea_index")
    return [by_index[index] for index in range(idea_count)]


def calculate_youtube_potential_score(
    topic_appeal_score: int,
    clarity_score: int,
    surprise_score: int,
    searchability_score: int,
    visual_explainability_score: int,
    weights: YouTubePotentialWeights = DEFAULT_YOUTUBE_POTENTIAL_WEIGHTS,
) -> float:
    """Calculate the final weighted score in provider-independent core logic."""

    weights = validate_potential_weights(weights)
    scores = {
        "topic_appeal_score": _dimension_score("topic_appeal_score", topic_appeal_score),
        "clarity_score": _dimension_score("clarity_score", clarity_score),
        "surprise_score": _dimension_score("surprise_score", surprise_score),
        "searchability_score": _dimension_score("searchability_score", searchability_score),
        "visual_explainability_score": _dimension_score(
            "visual_explainability_score", visual_explainability_score
        ),
    }
    return (
        scores["topic_appeal_score"] * weights.topic_appeal
        + scores["clarity_score"] * weights.clarity
        + scores["surprise_score"] * weights.surprise
        + scores["searchability_score"] * weights.searchability
        + scores["visual_explainability_score"] * weights.visual_explainability
    )


class LocalYouTubePotentialScorer:
    """Deterministic dimension scorer requiring no network or analytics data."""

    def score(
        self, ideas: list[YouTubeIdea], *, channel_focus: str
    ) -> list[YouTubePotentialDimensions]:
        ideas, focus, _ = validate_scoring_request(
            ideas, channel_focus, DEFAULT_YOUTUBE_POTENTIAL_WEIGHTS
        )
        results = []
        for index, idea in enumerate(ideas):
            seed = sum(map(ord, f"{idea.title}\0{idea.hook}\0{focus}"))
            results.append(
                YouTubePotentialDimensions(
                    idea_index=index,
                    topic_appeal_score=seed % 101,
                    clarity_score=(seed + len(idea.chapters) * 11) % 101,
                    surprise_score=(seed + len(idea.hook) * 7) % 101,
                    searchability_score=(seed + len(idea.seo_keywords) * 13) % 101,
                    visual_explainability_score=(seed + len(idea.angle) * 5) % 101,
                    reason="Deterministic local heuristic for development and testing.",
                )
            )
        return results


def score_youtube_ideas(
    ideas: list[YouTubeIdea],
    scorer: YouTubePotentialScorer,
    *,
    channel_focus: str,
    weights: YouTubePotentialWeights = DEFAULT_YOUTUBE_POTENTIAL_WEIGHTS,
) -> list[YouTubePotentialResult]:
    """Validate provider dimensions and calculate final scores in input order."""

    ideas, focus, weights = validate_scoring_request(ideas, channel_focus, weights)
    dimensions = validate_dimension_results(
        scorer.score(ideas, channel_focus=focus), len(ideas)
    )
    return [
        YouTubePotentialResult(
            idea_index=dimension.idea_index,
            youtube_potential_score=calculate_youtube_potential_score(
                dimension.topic_appeal_score,
                dimension.clarity_score,
                dimension.surprise_score,
                dimension.searchability_score,
                dimension.visual_explainability_score,
                weights,
            ),
            topic_appeal_score=dimension.topic_appeal_score,
            clarity_score=dimension.clarity_score,
            surprise_score=dimension.surprise_score,
            searchability_score=dimension.searchability_score,
            visual_explainability_score=dimension.visual_explainability_score,
            reason=dimension.reason,
        )
        for dimension in dimensions
    ]


@dataclass(frozen=True)
class RankedYouTubeIdea:
    """A validated idea paired with its potential result."""

    idea: YouTubeIdea
    potential: YouTubePotentialResult


def rank_youtube_ideas(
    ideas: list[YouTubeIdea], potential_results: list[YouTubePotentialResult]
) -> list[RankedYouTubeIdea]:
    """Rank ideas deterministically without changing news ranking behavior."""

    if not isinstance(ideas, list) or not isinstance(potential_results, list):
        raise ValueError("ideas and potential_results must be lists")
    validated_ideas = [validate_youtube_idea(idea) for idea in ideas]
    if len(validated_ideas) != len(potential_results):
        raise ValueError("each idea must have one potential result")
    by_index: dict[int, YouTubePotentialResult] = {}
    for result in potential_results:
        if not isinstance(result, YouTubePotentialResult):
            raise ValueError("potential_results must contain YouTubePotentialResult values")
        validated = YouTubePotentialResult(**result.__dict__)
        if validated.idea_index >= len(validated_ideas):
            raise ValueError("potential result has an out-of-range idea_index")
        if validated.idea_index in by_index:
            raise ValueError("potential results contain a duplicate idea_index")
        by_index[validated.idea_index] = validated
    if set(by_index) != set(range(len(validated_ideas))):
        raise ValueError("potential results are missing an idea_index")

    ranked = [
        RankedYouTubeIdea(idea=idea, potential=by_index[index])
        for index, idea in enumerate(validated_ideas)
    ]
    ranked.sort(
        key=lambda item: (
            -item.potential.youtube_potential_score,
            -item.potential.topic_appeal_score,
            -item.potential.surprise_score,
            -item.potential.searchability_score,
            item.potential.idea_index,
        )
    )
    return ranked


def select_top_youtube_ideas(
    ranked_ideas: list[RankedYouTubeIdea],
    limit: int = 3,
    minimum_potential_score: float | None = None,
) -> list[RankedYouTubeIdea]:
    """Apply a minimum threshold and limit to already ranked ideas."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    if minimum_potential_score is not None:
        minimum = _number("minimum_potential_score", minimum_potential_score)
        if not 0 <= minimum <= 100:
            raise ValueError("minimum_potential_score must be between 0 and 100")
    else:
        minimum = None
    for item in ranked_ideas:
        if not isinstance(item, RankedYouTubeIdea):
            raise ValueError("ranked_ideas must contain RankedYouTubeIdea values")
    eligible = [
        item
        for item in ranked_ideas
        if minimum is None or item.potential.youtube_potential_score >= minimum
    ]
    return eligible[:limit]

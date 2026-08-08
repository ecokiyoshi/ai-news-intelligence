"""Provider-independent YouTube title and thumbnail packaging."""

import math
from dataclasses import dataclass
from typing import Protocol

from app.youtube_ideas import YouTubeIdea, validate_youtube_idea
from app.youtube_potential import RankedYouTubeIdea, YouTubePotentialResult

MAX_PACKAGING_CANDIDATES = 10


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _index(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("candidate_index must be a non-negative integer")
    return value


def _positive_integer(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _score(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not 0 <= value <= 100:
        raise ValueError(f"{name} must be between 0 and 100")
    return value


def _finite_score(name: str, value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 100
    ):
        raise ValueError(f"{name} must be a finite number between 0 and 100")
    return float(value)


def _text_list(name: str, values: list[str]) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{name} must be a non-empty list")
    return [_required_text(f"{name} item", value) for value in values]


@dataclass(frozen=True)
class YouTubePackagingSource:
    """Validated idea and existing potential context for packaging providers."""

    idea_index: int
    source_article_ids: list[int]
    title: str
    hook: str
    angle: str
    target_audience: str
    estimated_length_minutes: int
    current_thumbnail_text: str
    chapters: list[str]
    seo_keywords: list[str]
    youtube_potential_score: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "idea_index", _index(self.idea_index))
        if not isinstance(self.source_article_ids, list) or not self.source_article_ids:
            raise ValueError("source_article_ids must be a non-empty list")
        article_ids = [_positive_integer("source article ID", value) for value in self.source_article_ids]
        if len(set(article_ids)) != len(article_ids):
            raise ValueError("source_article_ids must not contain duplicates")
        object.__setattr__(self, "source_article_ids", article_ids)
        for name in ("title", "hook", "angle", "target_audience", "current_thumbnail_text"):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
        object.__setattr__(
            self,
            "estimated_length_minutes",
            _positive_integer("estimated_length_minutes", self.estimated_length_minutes),
        )
        object.__setattr__(self, "chapters", _text_list("chapters", self.chapters))
        object.__setattr__(
            self, "seo_keywords", _text_list("seo_keywords", self.seo_keywords)
        )
        object.__setattr__(
            self,
            "youtube_potential_score",
            _finite_score("youtube_potential_score", self.youtube_potential_score),
        )


@dataclass(frozen=True)
class YouTubePackagingWeights:
    """Immutable core weights for final packaging scores."""

    clarity: float = 0.20
    curiosity: float = 0.25
    specificity: float = 0.20
    truthfulness: float = 0.25
    thumbnail_synergy: float = 0.10

    def __post_init__(self) -> None:
        values = []
        for name in (
            "clarity",
            "curiosity",
            "specificity",
            "truthfulness",
            "thumbnail_synergy",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError(f"{name} must be a finite number between 0 and 1")
            value = float(value)
            object.__setattr__(self, name, value)
            values.append(value)
        if not math.isclose(sum(values), 1.0):
            raise ValueError("YouTube packaging weights must sum to 1.0")


DEFAULT_YOUTUBE_PACKAGING_WEIGHTS = YouTubePackagingWeights()
DEFAULT_PACKAGING_WEIGHTS = DEFAULT_YOUTUBE_PACKAGING_WEIGHTS


@dataclass(frozen=True)
class YouTubePackagingDraft:
    """One provider-generated title and thumbnail-copy pair."""

    candidate_index: int
    title: str
    thumbnail_text: str
    rationale: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_index", _index(self.candidate_index))
        object.__setattr__(self, "title", _required_text("title", self.title))
        object.__setattr__(
            self, "thumbnail_text", _required_text("thumbnail_text", self.thumbnail_text)
        )
        object.__setattr__(self, "rationale", _required_text("rationale", self.rationale))


@dataclass(frozen=True)
class YouTubePackagingDimensions:
    """Provider-returned packaging dimensions without an overall score."""

    candidate_index: int
    clarity_score: int
    curiosity_score: int
    specificity_score: int
    truthfulness_score: int
    thumbnail_synergy_score: int
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_index", _index(self.candidate_index))
        for name in (
            "clarity_score",
            "curiosity_score",
            "specificity_score",
            "truthfulness_score",
            "thumbnail_synergy_score",
        ):
            object.__setattr__(self, name, _score(name, getattr(self, name)))
        object.__setattr__(self, "reason", _required_text("reason", self.reason))


@dataclass(frozen=True)
class YouTubePackagingCandidate:
    """A validated draft with dimensions and its core-calculated score."""

    candidate_index: int
    title: str
    thumbnail_text: str
    rationale: str
    packaging_score: float
    clarity_score: int
    curiosity_score: int
    specificity_score: int
    truthfulness_score: int
    thumbnail_synergy_score: int
    evaluation_reason: str

    def __post_init__(self) -> None:
        draft = YouTubePackagingDraft(
            candidate_index=self.candidate_index,
            title=self.title,
            thumbnail_text=self.thumbnail_text,
            rationale=self.rationale,
        )
        dimensions = YouTubePackagingDimensions(
            candidate_index=self.candidate_index,
            clarity_score=self.clarity_score,
            curiosity_score=self.curiosity_score,
            specificity_score=self.specificity_score,
            truthfulness_score=self.truthfulness_score,
            thumbnail_synergy_score=self.thumbnail_synergy_score,
            reason=self.evaluation_reason,
        )
        object.__setattr__(self, "candidate_index", draft.candidate_index)
        object.__setattr__(self, "title", draft.title)
        object.__setattr__(self, "thumbnail_text", draft.thumbnail_text)
        object.__setattr__(self, "rationale", draft.rationale)
        object.__setattr__(
            self, "packaging_score", _finite_score("packaging_score", self.packaging_score)
        )
        for name in (
            "clarity_score",
            "curiosity_score",
            "specificity_score",
            "truthfulness_score",
            "thumbnail_synergy_score",
            "reason",
        ):
            target = "evaluation_reason" if name == "reason" else name
            object.__setattr__(self, target, getattr(dimensions, name))


class YouTubePackagingGenerator(Protocol):
    def generate(
        self,
        source: YouTubePackagingSource,
        *,
        channel_focus: str,
        candidate_count: int,
    ) -> list[YouTubePackagingDraft]: ...


class YouTubePackagingEvaluator(Protocol):
    def evaluate(
        self,
        source: YouTubePackagingSource,
        drafts: list[YouTubePackagingDraft],
        *,
        channel_focus: str,
    ) -> list[YouTubePackagingDimensions]: ...


def validate_packaging_source(source: YouTubePackagingSource) -> YouTubePackagingSource:
    if not isinstance(source, YouTubePackagingSource):
        raise ValueError("source must be YouTubePackagingSource")
    return YouTubePackagingSource(**source.__dict__)


def validate_packaging_weights(weights: YouTubePackagingWeights) -> YouTubePackagingWeights:
    if not isinstance(weights, YouTubePackagingWeights):
        raise ValueError("weights must be YouTubePackagingWeights")
    return YouTubePackagingWeights(**weights.__dict__)


def validate_packaging_request(
    source: YouTubePackagingSource,
    channel_focus: str,
    candidate_count: int,
    weights: YouTubePackagingWeights,
) -> tuple[YouTubePackagingSource, str, int, YouTubePackagingWeights]:
    source = validate_packaging_source(source)
    focus = _required_text("channel_focus", channel_focus)
    count = _positive_integer("candidate_count", candidate_count)
    if count > MAX_PACKAGING_CANDIDATES:
        raise ValueError(f"candidate_count must not exceed {MAX_PACKAGING_CANDIDATES}")
    return source, focus, count, validate_packaging_weights(weights)


def validate_packaging_drafts(
    drafts: list[YouTubePackagingDraft], candidate_count: int
) -> list[YouTubePackagingDraft]:
    if not isinstance(drafts, list) or len(drafts) != candidate_count:
        raise ValueError("generator must return exactly the requested number of drafts")
    by_index: dict[int, YouTubePackagingDraft] = {}
    normalized_pairs: set[tuple[str, str]] = set()
    for draft in drafts:
        if not isinstance(draft, YouTubePackagingDraft):
            raise ValueError("generator must return YouTubePackagingDraft values")
        validated = YouTubePackagingDraft(**draft.__dict__)
        if validated.candidate_index >= candidate_count:
            raise ValueError("provider returned an out-of-range candidate_index")
        if validated.candidate_index in by_index:
            raise ValueError("provider returned a duplicate candidate_index")
        pair = (
            " ".join(validated.title.casefold().split()),
            " ".join(validated.thumbnail_text.casefold().split()),
        )
        if pair in normalized_pairs:
            raise ValueError("generator returned a duplicate title and thumbnail pair")
        normalized_pairs.add(pair)
        by_index[validated.candidate_index] = validated
    if set(by_index) != set(range(candidate_count)):
        raise ValueError("provider drafts are missing a candidate_index")
    return [by_index[index] for index in range(candidate_count)]


def validate_packaging_dimensions(
    dimensions: list[YouTubePackagingDimensions], candidate_count: int
) -> list[YouTubePackagingDimensions]:
    if not isinstance(dimensions, list) or len(dimensions) != candidate_count:
        raise ValueError("evaluator must return exactly one result per draft")
    by_index: dict[int, YouTubePackagingDimensions] = {}
    for dimension in dimensions:
        if not isinstance(dimension, YouTubePackagingDimensions):
            raise ValueError("evaluator must return YouTubePackagingDimensions values")
        validated = YouTubePackagingDimensions(**dimension.__dict__)
        if validated.candidate_index >= candidate_count:
            raise ValueError("provider returned an out-of-range candidate_index")
        if validated.candidate_index in by_index:
            raise ValueError("provider returned a duplicate candidate_index")
        by_index[validated.candidate_index] = validated
    if set(by_index) != set(range(candidate_count)):
        raise ValueError("provider evaluations are missing a candidate_index")
    return [by_index[index] for index in range(candidate_count)]


def build_youtube_packaging_source(
    idea: YouTubeIdea | RankedYouTubeIdea,
    potential: YouTubePotentialResult | None = None,
) -> YouTubePackagingSource:
    """Build provider context while copying the existing potential score exactly."""

    if isinstance(idea, RankedYouTubeIdea):
        if potential is not None:
            raise ValueError("potential must be omitted for RankedYouTubeIdea")
        potential = idea.potential
        idea = idea.idea
    idea = validate_youtube_idea(idea)
    if not isinstance(potential, YouTubePotentialResult):
        raise ValueError("potential must be YouTubePotentialResult")
    potential = YouTubePotentialResult(**potential.__dict__)
    return YouTubePackagingSource(
        idea_index=potential.idea_index,
        source_article_ids=idea.source_article_ids,
        title=idea.title,
        hook=idea.hook,
        angle=idea.angle,
        target_audience=idea.target_audience,
        estimated_length_minutes=idea.estimated_length_minutes,
        current_thumbnail_text=idea.thumbnail_text,
        chapters=idea.chapters,
        seo_keywords=idea.seo_keywords,
        youtube_potential_score=potential.youtube_potential_score,
    )


def calculate_packaging_score(
    clarity_score: int,
    curiosity_score: int,
    specificity_score: int,
    truthfulness_score: int,
    thumbnail_synergy_score: int,
    weights: YouTubePackagingWeights = DEFAULT_YOUTUBE_PACKAGING_WEIGHTS,
) -> float:
    weights = validate_packaging_weights(weights)
    return (
        _score("clarity_score", clarity_score) * weights.clarity
        + _score("curiosity_score", curiosity_score) * weights.curiosity
        + _score("specificity_score", specificity_score) * weights.specificity
        + _score("truthfulness_score", truthfulness_score) * weights.truthfulness
        + _score("thumbnail_synergy_score", thumbnail_synergy_score)
        * weights.thumbnail_synergy
    )


class LocalYouTubePackagingGenerator:
    """Deterministic local title/thumbnail generator for development and tests."""

    def generate(
        self,
        source: YouTubePackagingSource,
        *,
        channel_focus: str,
        candidate_count: int,
    ) -> list[YouTubePackagingDraft]:
        source, focus, count, _ = validate_packaging_request(
            source, channel_focus, candidate_count, DEFAULT_YOUTUBE_PACKAGING_WEIGHTS
        )
        return [
            YouTubePackagingDraft(
                candidate_index=index,
                title=f"{source.title}: {focus} explained ({index + 1})",
                thumbnail_text=f"{source.current_thumbnail_text} {index + 1}",
                rationale=f"Deterministic variation {index + 1} for {focus}.",
            )
            for index in range(count)
        ]


class LocalYouTubePackagingEvaluator:
    """Deterministic local heuristic; it uses no audience or performance data."""

    def evaluate(
        self,
        source: YouTubePackagingSource,
        drafts: list[YouTubePackagingDraft],
        *,
        channel_focus: str,
    ) -> list[YouTubePackagingDimensions]:
        source = validate_packaging_source(source)
        focus = _required_text("channel_focus", channel_focus)
        drafts = validate_packaging_drafts(drafts, len(drafts))
        results = []
        for draft in drafts:
            seed = sum(map(ord, f"{source.title}\0{draft.title}\0{draft.thumbnail_text}\0{focus}"))
            results.append(
                YouTubePackagingDimensions(
                    candidate_index=draft.candidate_index,
                    clarity_score=(seed + 7) % 101,
                    curiosity_score=(seed + 17) % 101,
                    specificity_score=(seed + 29) % 101,
                    truthfulness_score=(seed + 43) % 101,
                    thumbnail_synergy_score=(seed + 59) % 101,
                    reason="Deterministic local heuristic for development and testing.",
                )
            )
        return results


def rank_youtube_packaging_candidates(
    candidates: list[YouTubePackagingCandidate],
) -> list[YouTubePackagingCandidate]:
    if not isinstance(candidates, list):
        raise ValueError("candidates must be a list")
    validated = []
    for candidate in candidates:
        if not isinstance(candidate, YouTubePackagingCandidate):
            raise ValueError("candidates must contain YouTubePackagingCandidate values")
        validated.append(YouTubePackagingCandidate(**candidate.__dict__))
    return sorted(
        validated,
        key=lambda item: (
            -item.packaging_score,
            -item.truthfulness_score,
            -item.curiosity_score,
            -item.clarity_score,
            item.candidate_index,
        ),
    )


def generate_youtube_packaging(
    source: YouTubePackagingSource,
    generator: YouTubePackagingGenerator,
    evaluator: YouTubePackagingEvaluator,
    *,
    channel_focus: str,
    candidate_count: int = 5,
    weights: YouTubePackagingWeights = DEFAULT_YOUTUBE_PACKAGING_WEIGHTS,
) -> list[YouTubePackagingCandidate]:
    """Generate, evaluate, core-score, and deterministically rank packaging options."""

    source, focus, count, weights = validate_packaging_request(
        source, channel_focus, candidate_count, weights
    )
    drafts = validate_packaging_drafts(
        generator.generate(source, channel_focus=focus, candidate_count=count), count
    )
    dimensions = validate_packaging_dimensions(
        evaluator.evaluate(source, drafts, channel_focus=focus), count
    )
    candidates = []
    for draft, dimension in zip(drafts, dimensions, strict=True):
        candidates.append(
            YouTubePackagingCandidate(
                candidate_index=draft.candidate_index,
                title=draft.title,
                thumbnail_text=draft.thumbnail_text,
                rationale=draft.rationale,
                packaging_score=calculate_packaging_score(
                    dimension.clarity_score,
                    dimension.curiosity_score,
                    dimension.specificity_score,
                    dimension.truthfulness_score,
                    dimension.thumbnail_synergy_score,
                    weights,
                ),
                clarity_score=dimension.clarity_score,
                curiosity_score=dimension.curiosity_score,
                specificity_score=dimension.specificity_score,
                truthfulness_score=dimension.truthfulness_score,
                thumbnail_synergy_score=dimension.thumbnail_synergy_score,
                evaluation_reason=dimension.reason,
            )
        )
    return rank_youtube_packaging_candidates(candidates)


def select_top_packaging_candidates(
    ranked_candidates: list[YouTubePackagingCandidate],
    limit: int = 3,
    minimum_packaging_score: float | None = None,
) -> list[YouTubePackagingCandidate]:
    """Apply an optional threshold and limit to already-ranked candidates."""

    limit = _positive_integer("limit", limit)
    minimum = (
        None
        if minimum_packaging_score is None
        else _finite_score("minimum_packaging_score", minimum_packaging_score)
    )
    validated = rank_youtube_packaging_candidates(ranked_candidates)
    eligible = [
        candidate
        for candidate in validated
        if minimum is None or candidate.packaging_score >= minimum
    ]
    return eligible[:limit]


# Descriptive compatibility aliases for callers using the module-qualified names.
calculate_youtube_packaging_score = calculate_packaging_score
rank_packaging_candidates = rank_youtube_packaging_candidates
select_top_youtube_packaging = select_top_packaging_candidates

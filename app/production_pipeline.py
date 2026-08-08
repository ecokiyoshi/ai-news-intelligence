"""Provider-independent orchestration from RSS collection through scene images."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NewsArticle
from app.pipeline import ArticleTextProvider, PipelineResult, run_pipeline
from app.ranking import select_priority_articles
from app.scoring import Scorer
from app.summarization import Summarizer
from app.youtube_dialogue import (
    YouTubeDialogueConverter,
    YouTubeDialogueScript,
    convert_youtube_script_to_dialogue,
)
from app.youtube_ideas import (
    MAX_IDEA_COUNT,
    YouTubeIdea,
    YouTubeIdeaGenerator,
    build_youtube_idea_sources,
    generate_youtube_ideas,
)
from app.youtube_image_generation import (
    SceneImageGenerator,
    YouTubeImageGenerationResult,
    generate_youtube_scene_images,
    validate_image_size,
)
from app.youtube_packaging import (
    MAX_PACKAGING_CANDIDATES,
    YouTubePackagingCandidate,
    YouTubePackagingEvaluator,
    YouTubePackagingGenerator,
    build_youtube_packaging_source,
    generate_youtube_packaging,
)
from app.youtube_potential import (
    YouTubePotentialResult,
    YouTubePotentialScorer,
    rank_youtube_ideas,
    score_youtube_ideas,
)
from app.youtube_script import (
    YouTubeOutlineGenerator,
    YouTubeScript,
    YouTubeScriptGenerator,
    build_youtube_script_source,
    generate_youtube_script,
    validate_target_minutes,
)
from app.youtube_visuals import (
    YouTubeVisualPlan,
    YouTubeVisualPlanner,
    generate_youtube_visual_plan,
)

RUN_METADATA_FILENAME = "run.json"


class NoPriorityNewsError(RuntimeError):
    """Raised before YouTube providers run when ranking selects no news."""


@dataclass(frozen=True)
class ProductionProviders:
    summarizer: Summarizer
    news_scorer: Scorer
    text_provider: ArticleTextProvider
    idea_generator: YouTubeIdeaGenerator
    potential_scorer: YouTubePotentialScorer
    packaging_generator: YouTubePackagingGenerator
    packaging_evaluator: YouTubePackagingEvaluator
    outline_generator: YouTubeOutlineGenerator
    script_generator: YouTubeScriptGenerator
    dialogue_converter: YouTubeDialogueConverter
    visual_planner: YouTubeVisualPlanner
    image_generator: SceneImageGenerator


@dataclass(frozen=True)
class ProductionPipelineResult:
    run_id: str
    output_directory: str
    news: PipelineResult
    selected_idea: YouTubeIdea
    potential: YouTubePotentialResult
    selected_packaging: YouTubePackagingCandidate
    script: YouTubeScript
    dialogue: YouTubeDialogueScript
    visual_plan: YouTubeVisualPlan
    images: YouTubeImageGenerationResult


def _run_id(created_at: datetime) -> str:
    return f"{created_at.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def _provider_metadata(providers: ProductionProviders) -> dict[str, dict[str, str | None]]:
    return {
        name: {
            "provider": type(provider).__name__,
            "model": getattr(provider, "model", None),
        }
        for name, provider in providers.__dict__.items()
        if name != "text_provider"
    }


def _atomic_json(path: Path, content: dict[str, Any]) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            json.dump(_json_value(content), file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def run_production_pipeline(
    feed_urls: Iterable[str],
    relevance_target: str,
    channel_focus: str,
    providers: ProductionProviders,
    session: Session,
    *,
    output_root: Path,
    news_limit: int = 10,
    idea_count: int = 3,
    packaging_count: int = 5,
    target_minutes: int = 15,
    scene_limit: int = 50,
    image_size: str = "1792x1024",
    feed_parser: Callable[[str], Any] = feedparser.parse,
) -> ProductionPipelineResult:
    """Run existing validated services in production order and persist run artifacts."""

    if not isinstance(channel_focus, str) or not channel_focus.strip():
        raise ValueError("channel_focus must be a non-empty string")
    if not isinstance(output_root, Path):
        raise ValueError("output_root must be pathlib.Path")
    select_priority_articles([], limit=news_limit)
    if (
        isinstance(idea_count, bool)
        or not isinstance(idea_count, int)
        or not 0 < idea_count <= MAX_IDEA_COUNT
    ):
        raise ValueError(f"idea_count must be between 1 and {MAX_IDEA_COUNT}")
    if (
        isinstance(packaging_count, bool)
        or not isinstance(packaging_count, int)
        or not 0 < packaging_count <= MAX_PACKAGING_CANDIDATES
    ):
        raise ValueError(
            f"packaging_count must be between 1 and {MAX_PACKAGING_CANDIDATES}"
        )
    validate_target_minutes(target_minutes)
    if (
        isinstance(scene_limit, bool)
        or not isinstance(scene_limit, int)
        or scene_limit <= 0
    ):
        raise ValueError("scene_limit must be a positive integer")
    validate_image_size(image_size)

    news = run_pipeline(
        feed_urls,
        relevance_target,
        providers.summarizer,
        providers.news_scorer,
        providers.text_provider,
        session,
        limit=news_limit,
        feed_parser=feed_parser,
    )
    if not news.priority_articles:
        raise NoPriorityNewsError("news ranking selected no priority articles")

    article_ids = [result.article_id for result in news.priority_articles]
    articles = list(
        session.scalars(select(NewsArticle).where(NewsArticle.id.in_(article_ids)))
    )
    sources = build_youtube_idea_sources(news.priority_articles, articles)
    ideas = generate_youtube_ideas(
        sources,
        providers.idea_generator,
        channel_focus=channel_focus,
        idea_count=idea_count,
    )
    potentials = score_youtube_ideas(
        ideas, providers.potential_scorer, channel_focus=channel_focus
    )
    selected = rank_youtube_ideas(ideas, potentials)[0]
    packaging = generate_youtube_packaging(
        build_youtube_packaging_source(selected),
        providers.packaging_generator,
        providers.packaging_evaluator,
        channel_focus=channel_focus,
        candidate_count=packaging_count,
    )[0]
    script = generate_youtube_script(
        build_youtube_script_source(selected, packaging),
        providers.outline_generator,
        providers.script_generator,
        channel_focus=channel_focus,
        target_minutes=target_minutes,
    )
    dialogue = convert_youtube_script_to_dialogue(
        script, providers.dialogue_converter, channel_focus=channel_focus
    )
    visual_plan = generate_youtube_visual_plan(
        dialogue,
        providers.visual_planner,
        channel_focus=channel_focus,
        scene_limit=scene_limit,
    )

    created_at = datetime.now(timezone.utc)
    output_root.mkdir(parents=True, exist_ok=True)
    while True:
        run_id = _run_id(created_at)
        output_directory = output_root / run_id
        try:
            output_directory.mkdir()
            break
        except FileExistsError:
            created_at = datetime.now(timezone.utc)

    images = generate_youtube_scene_images(
        visual_plan,
        providers.image_generator,
        output_directory=output_directory,
        size=image_size,
        scene_limit=scene_limit,
    )
    result = ProductionPipelineResult(
        run_id=run_id,
        output_directory=str(output_directory),
        news=news,
        selected_idea=selected.idea,
        potential=selected.potential,
        selected_packaging=packaging,
        script=script,
        dialogue=dialogue,
        visual_plan=visual_plan,
        images=images,
    )
    _atomic_json(
        output_directory / RUN_METADATA_FILENAME,
        {
            "run_id": run_id,
            "created_at": created_at,
            "channel_focus": channel_focus.strip(),
            "news_pipeline": news,
            "priority_news": [
                {
                    **asdict(ranking),
                    "title": source.title,
                    "source": source.source,
                    "summary": source.summary,
                }
                for ranking, source in zip(news.priority_articles, sources, strict=True)
            ],
            "source_article_ids": selected.idea.source_article_ids,
            "selected_youtube_idea": selected.idea,
            "youtube_potential": selected.potential,
            "selected_packaging": packaging,
            "script": script,
            "dialogue": dialogue,
            "visual_plan": visual_plan,
            "generated_images": images.assets,
            "output_files": [
                *(Path(asset.file_path).name for asset in images.assets),
                "manifest.json",
                RUN_METADATA_FILENAME,
            ],
            "providers": _provider_metadata(providers),
        },
    )
    return result

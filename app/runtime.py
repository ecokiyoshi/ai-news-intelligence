"""Foreground runtime for the existing news pipeline scheduler."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import signal
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from types import FrameType
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from anthropic import Anthropic
from apscheduler.schedulers.background import BackgroundScheduler
from openai import OpenAI
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.anthropic_scorer import AnthropicScorer
from app.anthropic_summarizer import AnthropicSummarizer
from app.anthropic_youtube_dialogue import AnthropicYouTubeDialogueConverter
from app.anthropic_youtube_ideas import AnthropicYouTubeIdeaGenerator
from app.anthropic_youtube_packaging import (
    AnthropicYouTubePackagingEvaluator,
    AnthropicYouTubePackagingGenerator,
)
from app.anthropic_youtube_potential import AnthropicYouTubePotentialScorer
from app.anthropic_youtube_script import (
    AnthropicYouTubeOutlineGenerator,
    AnthropicYouTubeScriptGenerator,
)
from app.anthropic_youtube_visuals import AnthropicYouTubeVisualPlanner
from app.database import create_db_engine, init_db
from app.openai_scorer import OpenAIScorer
from app.openai_summarizer import OpenAISummarizer
from app.openai_youtube_dialogue import OpenAIYouTubeDialogueConverter
from app.openai_youtube_ideas import OpenAIYouTubeIdeaGenerator
from app.openai_youtube_image_generation import (
    OPENAI_SUPPORTED_IMAGE_SIZES,
    OpenAISceneImageGenerator,
)
from app.openai_youtube_packaging import (
    OpenAIYouTubePackagingEvaluator,
    OpenAIYouTubePackagingGenerator,
)
from app.openai_youtube_potential import OpenAIYouTubePotentialScorer
from app.openai_youtube_script import (
    OpenAIYouTubeOutlineGenerator,
    OpenAIYouTubeScriptGenerator,
)
from app.openai_youtube_visuals import OpenAIYouTubeVisualPlanner
from app.pipeline import MetadataTextProvider
from app.production_pipeline import (
    EditorialReviewResult,
    ProductionPipelineResult,
    ProductionProviders,
    run_production_pipeline,
)
from app.scheduler import NewsPipelineScheduler, build_pipeline_runner
from app.scoring import LocalScorer
from app.summarization import LocalSummarizer
from app.youtube_dialogue import LocalYouTubeDialogueConverter
from app.youtube_ideas import MAX_IDEA_COUNT, LocalYouTubeIdeaGenerator
from app.youtube_image_generation import LocalSceneImageGenerator, validate_image_size
from app.youtube_packaging import (
    MAX_PACKAGING_CANDIDATES,
    LocalYouTubePackagingEvaluator,
    LocalYouTubePackagingGenerator,
)
from app.youtube_potential import LocalYouTubePotentialScorer
from app.youtube_script import (
    LocalYouTubeOutlineGenerator,
    LocalYouTubeScriptGenerator,
    validate_target_minutes,
)
from app.youtube_visuals import LocalYouTubeVisualPlanner

logger = logging.getLogger(__name__)
RUNTIME_MARKER = "scheduler-runtime.json"


@dataclass(frozen=True)
class RuntimeConfig:
    """Validated environment configuration for one scheduler process."""

    data_dir: Path
    output_dir: Path
    database_url: str
    timezone: str
    feed_urls: tuple[str, ...]
    relevance_target: str
    interval_seconds: float
    provider: str
    pipeline_mode: str
    require_editorial_review: bool
    news_limit: int
    channel_focus: str
    idea_count: int
    packaging_count: int
    target_minutes: int
    scene_limit: int
    image_size: str

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        require_pipeline: bool = True,
    ) -> RuntimeConfig:
        values = os.environ if environ is None else environ
        data_dir = Path(values.get("APP_DATA_DIR", "runtime-data")).expanduser().resolve()
        output_dir = Path(values.get("OUTPUT_DIR", "generated-outputs")).expanduser().resolve()
        database_url = values.get("DATABASE_URL", "").strip()
        if not database_url:
            database_url = f"sqlite:///{data_dir / 'ai_news.db'}"

        timezone_name = values.get("TZ", "UTC").strip() or "UTC"
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"unknown TZ value: {timezone_name}") from error

        raw_urls = values.get("SCHEDULER_FEED_URLS", "")
        feed_urls = tuple(url.strip() for url in raw_urls.split(",") if url.strip())
        if any(
            urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc
            for url in feed_urls
        ):
            raise ValueError("SCHEDULER_FEED_URLS must contain valid HTTP(S) URLs")
        relevance_target = values.get("SCHEDULER_RELEVANCE_TARGET", "").strip()
        try:
            interval_seconds = float(values.get("SCHEDULER_INTERVAL_SECONDS", "3600"))
        except ValueError as error:
            raise ValueError("SCHEDULER_INTERVAL_SECONDS must be a number") from error
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("SCHEDULER_INTERVAL_SECONDS must be positive and finite")

        provider = values.get("SCHEDULER_PROVIDER", "local").strip().lower()
        if provider not in {"local", "openai", "anthropic"}:
            raise ValueError("SCHEDULER_PROVIDER must be 'local', 'openai', or 'anthropic'")
        if (
            require_pipeline
            and provider == "openai"
            and not values.get("OPENAI_API_KEY", "").strip()
        ):
            raise ValueError("OPENAI_API_KEY is required when SCHEDULER_PROVIDER=openai")
        if (
            require_pipeline
            and provider == "anthropic"
            and not values.get("ANTHROPIC_API_KEY", "").strip()
        ):
            raise ValueError("ANTHROPIC_API_KEY is required when SCHEDULER_PROVIDER=anthropic")

        pipeline_mode = values.get("PIPELINE_MODE", "news").strip().lower()
        if pipeline_mode not in {"news", "end_to_end"}:
            raise ValueError("PIPELINE_MODE must be 'news' or 'end_to_end'")
        editorial_review = values.get("EDITORIAL_REVIEW_REQUIRED", "false").strip().lower()
        if editorial_review not in {"true", "false"}:
            raise ValueError("EDITORIAL_REVIEW_REQUIRED must be 'true' or 'false'")
        require_editorial_review = editorial_review == "true"

        def positive_integer(name: str, default: str) -> int:
            raw = values.get(name, default)
            try:
                parsed = int(raw)
            except (TypeError, ValueError) as error:
                raise ValueError(f"{name} must be a positive integer") from error
            if isinstance(raw, bool) or parsed <= 0 or str(parsed) != str(raw).strip():
                raise ValueError(f"{name} must be a positive integer")
            return parsed

        news_limit = positive_integer("PIPELINE_NEWS_LIMIT", "10")
        idea_count = positive_integer("YOUTUBE_IDEA_COUNT", "3")
        if idea_count > MAX_IDEA_COUNT:
            raise ValueError(f"YOUTUBE_IDEA_COUNT must not exceed {MAX_IDEA_COUNT}")
        packaging_count = positive_integer("YOUTUBE_PACKAGING_COUNT", "5")
        if packaging_count > MAX_PACKAGING_CANDIDATES:
            raise ValueError(
                f"YOUTUBE_PACKAGING_COUNT must not exceed {MAX_PACKAGING_CANDIDATES}"
            )
        target_minutes = validate_target_minutes(
            positive_integer("YOUTUBE_TARGET_MINUTES", "15")
        )
        scene_limit = positive_integer("YOUTUBE_SCENE_LIMIT", "50")
        image_size = validate_image_size(
            values.get("YOUTUBE_IMAGE_SIZE", "1792x1024")
        )
        if provider == "openai" and image_size not in OPENAI_SUPPORTED_IMAGE_SIZES:
            raise ValueError("YOUTUBE_IMAGE_SIZE is not supported by the OpenAI image provider")
        channel_focus = values.get("YOUTUBE_CHANNEL_FOCUS", "").strip()
        if require_pipeline:
            if not feed_urls:
                raise ValueError("SCHEDULER_FEED_URLS must contain at least one URL")
            if not relevance_target:
                raise ValueError("SCHEDULER_RELEVANCE_TARGET must not be empty")
            if pipeline_mode == "end_to_end" and not channel_focus:
                raise ValueError("YOUTUBE_CHANNEL_FOCUS is required for end_to_end mode")

        return cls(
            data_dir=data_dir,
            output_dir=output_dir,
            database_url=database_url,
            timezone=timezone_name,
            feed_urls=feed_urls,
            relevance_target=relevance_target,
            interval_seconds=interval_seconds,
            provider=provider,
            pipeline_mode=pipeline_mode,
            require_editorial_review=require_editorial_review,
            news_limit=news_limit,
            channel_focus=channel_focus,
            idea_count=idea_count,
            packaging_count=packaging_count,
            target_minutes=target_minutes,
            scene_limit=scene_limit,
            image_size=image_size,
        )


def _check_writable_directory(path: Path) -> None:
    if not path.is_dir():
        raise RuntimeError(f"required directory is missing: {path}")
    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".health-", delete=True):
            pass
    except OSError as error:
        raise RuntimeError(f"required directory is not writable: {path}") from error


def prepare_runtime(config: RuntimeConfig) -> None:
    """Create persistent directories and initialize the configured database."""

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    _check_writable_directory(config.data_dir)
    _check_writable_directory(config.output_dir)
    engine = create_db_engine(config.database_url)
    try:
        init_db(engine)
    finally:
        engine.dispose()


def build_runtime_runner(config: RuntimeConfig) -> Callable[[], object]:
    """Build the configured one-run callable shared by scheduled and one-shot execution."""

    engine = create_db_engine(config.database_url)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    if config.provider == "openai":
        client = OpenAI()
        providers = ProductionProviders(
            summarizer=OpenAISummarizer(client=client),
            news_scorer=OpenAIScorer(client=client),
            text_provider=MetadataTextProvider(),
            idea_generator=OpenAIYouTubeIdeaGenerator(client=client),
            potential_scorer=OpenAIYouTubePotentialScorer(client=client),
            packaging_generator=OpenAIYouTubePackagingGenerator(client=client),
            packaging_evaluator=OpenAIYouTubePackagingEvaluator(client=client),
            outline_generator=OpenAIYouTubeOutlineGenerator(client=client),
            script_generator=OpenAIYouTubeScriptGenerator(client=client),
            dialogue_converter=OpenAIYouTubeDialogueConverter(client=client),
            visual_planner=OpenAIYouTubeVisualPlanner(client=client),
            image_generator=OpenAISceneImageGenerator(client=client),
        )
    elif config.provider == "anthropic":
        # Anthropic has no Images API equivalent, so scene image generation falls back to
        # the deterministic local generator even though every text stage uses Claude.
        client = Anthropic()
        providers = ProductionProviders(
            summarizer=AnthropicSummarizer(client=client),
            news_scorer=AnthropicScorer(client=client),
            text_provider=MetadataTextProvider(),
            idea_generator=AnthropicYouTubeIdeaGenerator(client=client),
            potential_scorer=AnthropicYouTubePotentialScorer(client=client),
            packaging_generator=AnthropicYouTubePackagingGenerator(client=client),
            packaging_evaluator=AnthropicYouTubePackagingEvaluator(client=client),
            outline_generator=AnthropicYouTubeOutlineGenerator(client=client),
            script_generator=AnthropicYouTubeScriptGenerator(client=client),
            dialogue_converter=AnthropicYouTubeDialogueConverter(client=client),
            visual_planner=AnthropicYouTubeVisualPlanner(client=client),
            image_generator=LocalSceneImageGenerator(),
        )
    else:
        providers = ProductionProviders(
            summarizer=LocalSummarizer(),
            news_scorer=LocalScorer(),
            text_provider=MetadataTextProvider(),
            idea_generator=LocalYouTubeIdeaGenerator(),
            potential_scorer=LocalYouTubePotentialScorer(),
            packaging_generator=LocalYouTubePackagingGenerator(),
            packaging_evaluator=LocalYouTubePackagingEvaluator(),
            outline_generator=LocalYouTubeOutlineGenerator(),
            script_generator=LocalYouTubeScriptGenerator(),
            dialogue_converter=LocalYouTubeDialogueConverter(),
            visual_planner=LocalYouTubeVisualPlanner(),
            image_generator=LocalSceneImageGenerator(),
        )
    if config.pipeline_mode == "news":
        return build_pipeline_runner(
            sessions,
            config.feed_urls,
            config.relevance_target,
            providers.summarizer,
            providers.news_scorer,
            providers.text_provider,
            limit=config.news_limit,
        )

    def production_runner() -> ProductionPipelineResult | EditorialReviewResult:
        with sessions() as session:
            return run_production_pipeline(
                config.feed_urls,
                config.relevance_target,
                config.channel_focus,
                providers,
                session,
                output_root=config.output_dir,
                news_limit=config.news_limit,
                idea_count=config.idea_count,
                packaging_count=config.packaging_count,
                target_minutes=config.target_minutes,
                scene_limit=config.scene_limit,
                image_size=config.image_size,
                require_editorial_review=config.require_editorial_review,
            )

    return production_runner


def build_runtime_scheduler(
    config: RuntimeConfig, runner: Callable[[], object] | None = None
) -> NewsPipelineScheduler:
    """Wrap the shared configured runner in the existing scheduler."""

    return NewsPipelineScheduler(
        runner or build_runtime_runner(config),  # type: ignore[arg-type]
        interval_seconds=config.interval_seconds,
        scheduler_backend=BackgroundScheduler(timezone=ZoneInfo(config.timezone)),
    )


def _write_runtime_marker(config: RuntimeConfig) -> Path:
    marker = config.data_dir / RUNTIME_MARKER
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    temporary.replace(marker)
    return marker


def request_shutdown(stop_event: Event) -> None:
    """Request a graceful scheduler shutdown."""

    stop_event.set()


def serve_scheduler(
    scheduler: NewsPipelineScheduler,
    config: RuntimeConfig,
    *,
    stop_event: Event | None = None,
    install_signal_handlers: bool = True,
) -> None:
    """Run the existing background scheduler while this foreground process waits."""

    event = stop_event or Event()

    def handle_signal(_signum: int, _frame: FrameType | None) -> None:
        logger.info("Scheduler shutdown requested")
        request_shutdown(event)

    if install_signal_handlers:
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

    marker = _write_runtime_marker(config)
    try:
        scheduler.start()
        logger.info(
            "Scheduler runtime started mode=%s timezone=%s provider=%s cadence_seconds=%s "
            "output_root=%s",
            config.pipeline_mode,
            config.timezone,
            config.provider,
            config.interval_seconds,
            config.output_dir,
        )
        event.wait()
    finally:
        scheduler.shutdown()
        marker.unlink(missing_ok=True)


def healthcheck(config: RuntimeConfig) -> None:
    """Validate local runtime state without calling feeds or paid APIs."""

    _check_writable_directory(config.data_dir)
    _check_writable_directory(config.output_dir)
    marker = config.data_dir / RUNTIME_MARKER
    try:
        pid = int(json.loads(marker.read_text(encoding="utf-8"))["pid"])
        os.kill(pid, 0)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("scheduler runtime marker is invalid or stale") from error

    database = make_url(config.database_url).database
    if config.database_url.startswith("sqlite") and database not in {None, "", ":memory:"}:
        if not Path(database).is_file():
            raise RuntimeError("configured SQLite database file is missing")

    engine = create_db_engine(config.database_url)
    try:
        with Session(engine) as session:
            session.execute(text("SELECT 1"))
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", nargs="?", choices=("run", "run-once", "health"), default="run"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        config = RuntimeConfig.from_env(require_pipeline=args.command != "health")
        if args.command == "health":
            healthcheck(config)
            return 0
        prepare_runtime(config)
        runner = build_runtime_runner(config)
        if args.command == "run-once":
            runner()
            logger.info("One-shot pipeline completed mode=%s", config.pipeline_mode)
            return 0
        serve_scheduler(build_runtime_scheduler(config, runner), config)
        return 0
    except Exception as error:
        logger.error("Runtime failed (%s): %s", type(error).__name__, error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

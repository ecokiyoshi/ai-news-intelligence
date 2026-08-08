"""Foreground runtime for the existing news pipeline scheduler."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import signal
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from types import FrameType
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.database import create_db_engine, init_db
from app.openai_scorer import OpenAIScorer
from app.openai_summarizer import OpenAISummarizer
from app.pipeline import MetadataTextProvider
from app.scheduler import NewsPipelineScheduler, build_pipeline_runner
from app.scoring import LocalScorer
from app.summarization import LocalSummarizer

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
        if provider not in {"local", "openai"}:
            raise ValueError("SCHEDULER_PROVIDER must be 'local' or 'openai'")
        if provider == "openai" and not values.get("OPENAI_API_KEY", "").strip():
            raise ValueError("OPENAI_API_KEY is required when SCHEDULER_PROVIDER=openai")
        if require_pipeline:
            if not feed_urls:
                raise ValueError("SCHEDULER_FEED_URLS must contain at least one URL")
            if not relevance_target:
                raise ValueError("SCHEDULER_RELEVANCE_TARGET must not be empty")

        return cls(
            data_dir=data_dir,
            output_dir=output_dir,
            database_url=database_url,
            timezone=timezone_name,
            feed_urls=feed_urls,
            relevance_target=relevance_target,
            interval_seconds=interval_seconds,
            provider=provider,
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


def build_runtime_scheduler(config: RuntimeConfig) -> NewsPipelineScheduler:
    """Compose the existing pipeline and scheduler with configured providers."""

    engine = create_db_engine(config.database_url)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    if config.provider == "openai":
        summarizer = OpenAISummarizer()
        scorer = OpenAIScorer()
    else:
        summarizer = LocalSummarizer()
        scorer = LocalScorer()
    runner = build_pipeline_runner(
        sessions,
        config.feed_urls,
        config.relevance_target,
        summarizer,
        scorer,
        MetadataTextProvider(),
    )
    return NewsPipelineScheduler(
        runner,
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
            "Scheduler runtime started timezone=%s provider=%s cadence_seconds=%s output_root=%s",
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
    parser.add_argument("command", nargs="?", choices=("run", "health"), default="run")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        config = RuntimeConfig.from_env(require_pipeline=args.command == "run")
        if args.command == "health":
            healthcheck(config)
            return 0
        prepare_runtime(config)
        serve_scheduler(build_runtime_scheduler(config), config)
        return 0
    except Exception as error:
        logger.error("Runtime failed (%s): %s", type(error).__name__, error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

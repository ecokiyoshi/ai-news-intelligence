"""Explicitly started, single-process scheduling for the news pipeline."""

import logging
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Protocol

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session, sessionmaker

from app.pipeline import ArticleTextProvider, PipelineResult, run_pipeline
from app.ranking import DEFAULT_IMPORTANCE_WEIGHT, DEFAULT_RELEVANCE_WEIGHT
from app.scoring import Scorer
from app.summarization import Summarizer

DEFAULT_INTERVAL_SECONDS = 3600.0
NEWS_PIPELINE_JOB_ID = "news-pipeline"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledRunResult:
    """Outcome and UTC timing information for one scheduled run attempt."""

    started_at: datetime
    finished_at: datetime
    success: bool
    pipeline_result: PipelineResult | None
    error: str | None
    skipped: bool = False


class SchedulerBackend(Protocol):
    """Minimal scheduler backend surface used by the service."""

    def add_job(self, func: Callable[[], Any], trigger: str, **kwargs: Any) -> Any:
        """Register a scheduled callable."""

    def start(self) -> None:
        """Start background scheduling."""

    def shutdown(self, wait: bool = True) -> None:
        """Stop background scheduling and release resources."""


def _validate_interval(interval_seconds: float) -> float:
    if (
        isinstance(interval_seconds, bool)
        or not isinstance(interval_seconds, (int, float))
        or not math.isfinite(interval_seconds)
        or interval_seconds <= 0
    ):
        raise ValueError("interval_seconds must be a positive finite number")
    return float(interval_seconds)


class NewsPipelineScheduler:
    """Run an injected pipeline on an interval with single-process overlap protection.

    The application lock prevents overlap only within this Python process. It is not a
    distributed lock across processes or containers. Construction and module import do
    not register jobs or start scheduler threads; callers must invoke ``start()``.
    """

    def __init__(
        self,
        pipeline_runner: Callable[[], PipelineResult],
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        *,
        scheduler_backend: SchedulerBackend | None = None,
    ) -> None:
        if not callable(pipeline_runner):
            raise ValueError("pipeline_runner must be callable")
        self.pipeline_runner = pipeline_runner
        self.interval_seconds = _validate_interval(interval_seconds)
        self._scheduler = scheduler_backend or BackgroundScheduler(timezone=timezone.utc)
        self._run_lock = Lock()
        self._state_lock = Lock()
        self._last_run_lock = Lock()
        self._started = False
        self._last_run: ScheduledRunResult | None = None

    @property
    def last_run(self) -> ScheduledRunResult | None:
        """Return the most recent run or overlap attempt."""

        with self._last_run_lock:
            return self._last_run

    def _record(self, result: ScheduledRunResult) -> ScheduledRunResult:
        with self._last_run_lock:
            self._last_run = result
        return result

    def run_once(self) -> ScheduledRunResult:
        """Run immediately without starting the interval scheduler."""

        started_at = datetime.now(timezone.utc)
        if not self._run_lock.acquire(blocking=False):
            logger.info("News pipeline run skipped because another run is in progress")
            return self._record(
                ScheduledRunResult(
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    success=False,
                    pipeline_result=None,
                    error="pipeline run already in progress",
                    skipped=True,
                )
            )

        try:
            logger.info("News pipeline run started")
            try:
                pipeline_result = self.pipeline_runner()
            except Exception as error:
                logger.error(
                    "News pipeline run failed (%s)", type(error).__name__
                )
                result = ScheduledRunResult(
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    success=False,
                    pipeline_result=None,
                    error=f"{type(error).__name__}: pipeline run failed",
                )
            else:
                logger.info("News pipeline run completed")
                result = ScheduledRunResult(
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    success=True,
                    pipeline_result=pipeline_result,
                    error=None,
                )
            return self._record(result)
        finally:
            self._run_lock.release()

    def start(self) -> None:
        """Register exactly one interval job and start scheduling; repeated calls are no-ops."""

        with self._state_lock:
            if self._started:
                return
            self._scheduler.add_job(
                self.run_once,
                "interval",
                seconds=self.interval_seconds,
                id=NEWS_PIPELINE_JOB_ID,
                replace_existing=False,
                max_instances=1,
                coalesce=True,
            )
            self._scheduler.start()
            self._started = True
            logger.info("News pipeline scheduler started")

    def shutdown(self) -> None:
        """Stop scheduler resources safely; repeated or pre-start calls are no-ops."""

        with self._state_lock:
            if not self._started:
                return
            self._scheduler.shutdown(wait=True)
            self._started = False
            logger.info("News pipeline scheduler stopped")


def build_pipeline_runner(
    session_factory: sessionmaker[Session],
    feed_urls: Iterable[str],
    relevance_target: str,
    summarizer: Summarizer,
    scorer: Scorer,
    text_provider: ArticleTextProvider,
    *,
    limit: int = 10,
    minimum_priority_score: float | None = None,
    max_per_source: int | None = None,
    importance_weight: float = DEFAULT_IMPORTANCE_WEIGHT,
    relevance_weight: float = DEFAULT_RELEVANCE_WEIGHT,
    force_resummarize: bool = False,
    force_rescore: bool = False,
    feed_parser: Callable[[str], Any] | None = None,
    pipeline_function: Callable[..., PipelineResult] = run_pipeline,
) -> Callable[[], PipelineResult]:
    """Build a runner that creates and closes a fresh SQLAlchemy session per run."""

    urls = tuple(feed_urls)

    def pipeline_runner() -> PipelineResult:
        options: dict[str, Any] = {
            "limit": limit,
            "minimum_priority_score": minimum_priority_score,
            "max_per_source": max_per_source,
            "importance_weight": importance_weight,
            "relevance_weight": relevance_weight,
            "force_resummarize": force_resummarize,
            "force_rescore": force_rescore,
        }
        if feed_parser is not None:
            options["feed_parser"] = feed_parser
        with session_factory() as session:
            return pipeline_function(
                urls,
                relevance_target,
                summarizer,
                scorer,
                text_provider,
                session,
                **options,
            )

    return pipeline_runner

from threading import Event, Thread

import pytest

from app.pipeline import PipelineResult
from app.scheduler import NEWS_PIPELINE_JOB_ID, NewsPipelineScheduler, build_pipeline_runner


def pipeline_result() -> PipelineResult:
    return PipelineResult(1, 2, 2, 0, 2, 2, 0, [])


class FakeSchedulerBackend:
    def __init__(self) -> None:
        self.jobs: list[tuple[object, str, dict]] = []
        self.start_calls = 0
        self.shutdown_calls: list[bool] = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append((func, trigger, kwargs))

    def start(self) -> None:
        self.start_calls += 1

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)


def test_successful_run_once_returns_pipeline_result_and_utc_times() -> None:
    expected = pipeline_result()
    calls = 0

    def runner() -> PipelineResult:
        nonlocal calls
        calls += 1
        return expected

    scheduler = NewsPipelineScheduler(runner, scheduler_backend=FakeSchedulerBackend())
    result = scheduler.run_once()

    assert calls == 1
    assert result.success is True
    assert result.skipped is False
    assert result.pipeline_result is expected
    assert result.error is None
    assert result.started_at.utcoffset().total_seconds() == 0
    assert result.finished_at.utcoffset().total_seconds() == 0
    assert scheduler.last_run is result


def test_failure_is_returned_without_exposing_exception_message() -> None:
    def runner() -> PipelineResult:
        raise RuntimeError("pipeline failed with sensitive credential")

    result = NewsPipelineScheduler(
        runner, scheduler_backend=FakeSchedulerBackend()
    ).run_once()

    assert result.success is False
    assert result.pipeline_result is None
    assert result.error == "RuntimeError: pipeline run failed"
    assert "secret" not in result.error


def test_failure_releases_lock_and_next_run_succeeds() -> None:
    attempts = 0

    def runner() -> PipelineResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("pipeline failed")
        return pipeline_result()

    scheduler = NewsPipelineScheduler(runner, scheduler_backend=FakeSchedulerBackend())
    assert scheduler.run_once().success is False
    assert scheduler.run_once().success is True
    assert attempts == 2


def test_overlapping_run_is_skipped_without_second_runner_call() -> None:
    entered = Event()
    release = Event()
    call_count = 0
    first_result = []

    def runner() -> PipelineResult:
        nonlocal call_count
        call_count += 1
        entered.set()
        assert release.wait(timeout=2)
        return pipeline_result()

    scheduler = NewsPipelineScheduler(runner, scheduler_backend=FakeSchedulerBackend())
    thread = Thread(target=lambda: first_result.append(scheduler.run_once()))
    thread.start()
    assert entered.wait(timeout=2)

    overlap = scheduler.run_once()
    release.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert call_count == 1
    assert overlap.success is False
    assert overlap.skipped is True
    assert overlap.pipeline_result is None
    assert overlap.error == "pipeline run already in progress"
    assert first_result[0].success is True


def test_start_registers_one_interval_job_and_is_idempotent() -> None:
    backend = FakeSchedulerBackend()
    scheduler = NewsPipelineScheduler(
        pipeline_result, interval_seconds=60, scheduler_backend=backend
    )

    scheduler.start()
    scheduler.start()

    assert backend.start_calls == 1
    assert len(backend.jobs) == 1
    callback, trigger, options = backend.jobs[0]
    assert callback.__self__ is scheduler
    assert callback.__func__ is scheduler.run_once.__func__
    assert trigger == "interval"
    assert options == {
        "seconds": 60.0,
        "id": NEWS_PIPELINE_JOB_ID,
        "replace_existing": False,
        "max_instances": 1,
        "coalesce": True,
    }


def test_shutdown_is_safe_before_start_and_when_repeated() -> None:
    backend = FakeSchedulerBackend()
    scheduler = NewsPipelineScheduler(pipeline_result, scheduler_backend=backend)

    scheduler.shutdown()
    scheduler.start()
    scheduler.shutdown()
    scheduler.shutdown()

    assert backend.shutdown_calls == [True]


@pytest.mark.parametrize("interval", [1, 60, 3600, 0.5])
def test_valid_intervals_are_accepted(interval) -> None:
    scheduler = NewsPipelineScheduler(
        pipeline_result, interval_seconds=interval, scheduler_backend=FakeSchedulerBackend()
    )
    assert scheduler.interval_seconds == float(interval)


@pytest.mark.parametrize("interval", [0, -1, True, float("nan"), float("inf")])
def test_invalid_intervals_are_rejected(interval) -> None:
    with pytest.raises(ValueError):
        NewsPipelineScheduler(
            pipeline_result,
            interval_seconds=interval,
            scheduler_backend=FakeSchedulerBackend(),
        )


def test_construction_does_not_start_or_run_any_work() -> None:
    backend = FakeSchedulerBackend()
    calls = 0

    def runner() -> PipelineResult:
        nonlocal calls
        calls += 1
        return pipeline_result()

    NewsPipelineScheduler(runner, scheduler_backend=backend)
    assert calls == 0
    assert backend.jobs == []
    assert backend.start_calls == 0


def test_pipeline_runner_uses_fresh_session_and_closes_on_success_and_failure() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.closed = True

    sessions: list[FakeSession] = []

    def session_factory() -> FakeSession:
        session = FakeSession()
        sessions.append(session)
        return session

    seen_sessions: list[FakeSession] = []
    should_fail = False

    def fake_pipeline(*args, **kwargs) -> PipelineResult:
        seen_sessions.append(args[5])
        if should_fail:
            raise RuntimeError("pipeline failed")
        return pipeline_result()

    runner = build_pipeline_runner(
        session_factory,
        ["feed"],
        "AI industry",
        object(),
        object(),
        object(),
        pipeline_function=fake_pipeline,
    )

    assert runner() == pipeline_result()
    assert runner() == pipeline_result()
    should_fail = True
    with pytest.raises(RuntimeError):
        runner()

    assert len({id(session) for session in seen_sessions}) == 3
    assert all(session.closed for session in sessions)

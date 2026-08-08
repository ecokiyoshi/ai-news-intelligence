import json
import os
from pathlib import Path
from threading import Event

import pytest

from app.runtime import (
    RUNTIME_MARKER,
    RuntimeConfig,
    healthcheck,
    prepare_runtime,
    request_shutdown,
    serve_scheduler,
)


def environment(tmp_path: Path, **overrides: str) -> dict[str, str]:
    values = {
        "APP_DATA_DIR": str(tmp_path / "state"),
        "OUTPUT_DIR": str(tmp_path / "outputs"),
        "SCHEDULER_FEED_URLS": "https://example.com/feed.xml, https://example.org/feed.xml",
        "SCHEDULER_RELEVANCE_TARGET": "AI industry",
        "SCHEDULER_INTERVAL_SECONDS": "60",
        "SCHEDULER_PROVIDER": "local",
        "TZ": "Asia/Tokyo",
    }
    values.update(overrides)
    return values


def test_runtime_config_derives_database_url_and_parses_values(tmp_path: Path) -> None:
    config = RuntimeConfig.from_env(environment(tmp_path))

    assert config.database_url == f"sqlite:///{tmp_path / 'state' / 'ai_news.db'}"
    assert config.feed_urls == (
        "https://example.com/feed.xml",
        "https://example.org/feed.xml",
    )
    assert config.relevance_target == "AI industry"
    assert config.interval_seconds == 60
    assert config.provider == "local"
    assert config.timezone == "Asia/Tokyo"


def test_explicit_database_url_takes_precedence(tmp_path: Path) -> None:
    expected = f"sqlite:///{tmp_path / 'explicit.db'}"
    config = RuntimeConfig.from_env(environment(tmp_path, DATABASE_URL=expected))
    assert config.database_url == expected


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("SCHEDULER_FEED_URLS", "", "at least one URL"),
        ("SCHEDULER_FEED_URLS", "not-a-url", "valid HTTP"),
        ("SCHEDULER_RELEVANCE_TARGET", "  ", "must not be empty"),
        ("SCHEDULER_INTERVAL_SECONDS", "0", "must be positive"),
        ("SCHEDULER_INTERVAL_SECONDS", "nan", "must be positive"),
        ("SCHEDULER_INTERVAL_SECONDS", "never", "must be a number"),
        ("SCHEDULER_PROVIDER", "unknown", "must be 'local' or 'openai'"),
        ("TZ", "Not/A-Timezone", "unknown TZ"),
    ],
)
def test_invalid_runtime_configuration_is_rejected(
    tmp_path: Path, name: str, value: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        RuntimeConfig.from_env(environment(tmp_path, **{name: value}))


def test_openai_provider_requires_api_key_but_local_provider_does_not(
    tmp_path: Path,
) -> None:
    RuntimeConfig.from_env(environment(tmp_path, SCHEDULER_PROVIDER="local"))
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        RuntimeConfig.from_env(environment(tmp_path, SCHEDULER_PROVIDER="openai"))


def test_prepare_runtime_creates_persistent_paths_and_database(tmp_path: Path) -> None:
    config = RuntimeConfig.from_env(environment(tmp_path))
    prepare_runtime(config)

    assert config.data_dir.is_dir()
    assert config.output_dir.is_dir()
    assert (config.data_dir / "ai_news.db").is_file()


def test_healthcheck_accepts_live_marker_database_and_writable_paths(
    tmp_path: Path,
) -> None:
    config = RuntimeConfig.from_env(environment(tmp_path))
    prepare_runtime(config)
    (config.data_dir / RUNTIME_MARKER).write_text(
        json.dumps({"pid": os.getpid()}), encoding="utf-8"
    )

    healthcheck(config)


def test_healthcheck_rejects_missing_runtime_marker(tmp_path: Path) -> None:
    config = RuntimeConfig.from_env(environment(tmp_path))
    prepare_runtime(config)
    with pytest.raises(RuntimeError, match="marker"):
        healthcheck(config)


def test_healthcheck_rejects_missing_sqlite_database(tmp_path: Path) -> None:
    config = RuntimeConfig.from_env(environment(tmp_path))
    prepare_runtime(config)
    (config.data_dir / RUNTIME_MARKER).write_text(
        json.dumps({"pid": os.getpid()}), encoding="utf-8"
    )
    (config.data_dir / "ai_news.db").unlink()
    with pytest.raises(RuntimeError, match="database file is missing"):
        healthcheck(config)


def test_healthcheck_rejects_invalid_output_path(tmp_path: Path) -> None:
    config = RuntimeConfig.from_env(environment(tmp_path))
    config.data_dir.mkdir()
    config.output_dir.write_text("not a directory", encoding="utf-8")
    with pytest.raises(RuntimeError, match="required directory"):
        healthcheck(config)


def test_foreground_runtime_starts_and_gracefully_stops_existing_scheduler(
    tmp_path: Path,
) -> None:
    class FakeScheduler:
        def __init__(self) -> None:
            self.start_calls = 0
            self.shutdown_calls = 0

        def start(self) -> None:
            self.start_calls += 1

        def shutdown(self) -> None:
            self.shutdown_calls += 1

    config = RuntimeConfig.from_env(environment(tmp_path))
    prepare_runtime(config)
    scheduler = FakeScheduler()
    stop_event = Event()
    request_shutdown(stop_event)

    serve_scheduler(
        scheduler,  # type: ignore[arg-type]
        config,
        stop_event=stop_event,
        install_signal_handlers=False,
    )

    assert scheduler.start_calls == 1
    assert scheduler.shutdown_calls == 1
    assert not (config.data_dir / RUNTIME_MARKER).exists()

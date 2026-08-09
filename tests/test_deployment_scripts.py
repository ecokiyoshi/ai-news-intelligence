from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


IMAGE_SHA = "0123456789abcdef0123456789abcdef01234567"
IMAGE = f"ghcr.io/ecokiyoshi/ai-news-intelligence:sha-{IMAGE_SHA}"


def test_lightsail_bootstrap_has_valid_bash_syntax() -> None:
    repository = Path(__file__).resolve().parents[1]
    script = repository / "deploy/lightsail-bootstrap.sh"

    result = subprocess.run(
        ["bash", "-n", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert os.access(script, os.X_OK)


def _deployment_fixture(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    repository = Path(__file__).resolve().parents[1]
    deployment = tmp_path / "deployment"
    (deployment / "deploy").mkdir(parents=True)
    (deployment / "scripts").mkdir()
    shutil.copy2(repository / "compose.production.yaml", deployment)
    shutil.copy2(repository / "deploy/Caddyfile", deployment / "deploy")
    shutil.copy2(repository / "scripts/deploy.sh", deployment / "scripts")
    shutil.copy2(repository / "scripts/rollback.sh", deployment / "scripts")
    (deployment / ".env").write_text(
        "APP_DOMAIN=news.example.com\n"
        "APP_IMAGE=ghcr.io/ecokiyoshi/ai-news-intelligence:latest\n"
        "OPENAI_API_KEY=do-not-print-or-replace\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "docker-commands.log"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import sys

arguments = sys.argv[1:]
with pathlib.Path(os.environ["FAKE_DOCKER_LOG"]).open("a", encoding="utf-8") as log:
    log.write(" ".join(arguments) + "\\n")

if arguments[:1] == ["inspect"]:
    template = arguments[arguments.index("--format") + 1]
    if ".State.Status" in template:
        print("running")
    elif ".State.Health" in template:
        print("healthy")
    elif ".Config.Image" in template:
        env_file = pathlib.Path(os.environ["FAKE_DEPLOY_DIR"]) / ".env"
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("APP_IMAGE="):
                print(line.split("=", 1)[1])
                break
elif "ps" in arguments and "-q" in arguments:
    print(f"{arguments[-1]}-container")
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    fake_curl = fake_bin / "curl"
    fake_curl.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_curl.chmod(0o755)
    fake_flock = fake_bin / "flock"
    fake_flock.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_flock.chmod(0o755)

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "FAKE_DEPLOY_DIR": str(deployment),
            "FAKE_DOCKER_LOG": str(command_log),
            "DEPLOY_HEALTH_TIMEOUT_SECONDS": "2",
        }
    )
    return deployment, environment, command_log


def test_deploy_uses_immutable_image_without_destroying_data(tmp_path: Path) -> None:
    deployment, environment, command_log = _deployment_fixture(tmp_path)

    result = subprocess.run(
        [deployment / "scripts/deploy.sh", IMAGE],
        cwd=deployment,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    env_contents = (deployment / ".env").read_text(encoding="utf-8")
    assert f"APP_IMAGE={IMAGE}" in env_contents
    assert "OPENAI_API_KEY=do-not-print-or-replace" in env_contents
    assert "do-not-print-or-replace" not in result.stdout
    assert "do-not-print-or-replace" not in result.stderr

    commands = command_log.read_text(encoding="utf-8")
    assert " pull\n" in commands
    assert " up -d --no-build --remove-orphans\n" in commands
    assert " down" not in commands
    assert " -v" not in commands


def test_rollback_rejects_moving_tag(tmp_path: Path) -> None:
    deployment, environment, command_log = _deployment_fixture(tmp_path)

    result = subprocess.run(
        [deployment / "scripts/rollback.sh", "latest"],
        cwd=deployment,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not command_log.exists()


def test_rollback_accepts_previous_immutable_sha(tmp_path: Path) -> None:
    deployment, environment, _ = _deployment_fixture(tmp_path)

    result = subprocess.run(
        [deployment / "scripts/rollback.sh", f"sha-{IMAGE_SHA}"],
        cwd=deployment,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert f"APP_IMAGE={IMAGE}" in (deployment / ".env").read_text(encoding="utf-8")

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

from support_shell import bash_command, bash_path, install_python3_shim, prepend_path


def _operations_fixture(tmp_path: Path) -> tuple[Path, dict[str, str], Path, Path, Path]:
    repository = Path(__file__).resolve().parents[1]
    deployment = tmp_path / "deployment"
    (deployment / "deploy").mkdir(parents=True)
    (deployment / "scripts").mkdir()
    shutil.copy2(repository / "compose.production.yaml", deployment)
    shutil.copy2(repository / "deploy/Caddyfile", deployment / "deploy")
    for script in (repository / "scripts").glob("*.sh"):
        shutil.copy2(script, deployment / "scripts")
    (deployment / ".env").write_text(
        "APP_DOMAIN=news.example.com\n"
        "APP_IMAGE=ghcr.io/ecokiyoshi/ai-news-intelligence:sha-"
        "0123456789abcdef0123456789abcdef01234567\n"
        "OPENAI_API_KEY=do-not-print-or-back-up\n",
        encoding="utf-8",
    )

    app_data = tmp_path / "app-data"
    outputs = tmp_path / "outputs"
    app_data.mkdir()
    outputs.mkdir()
    (app_data / "ai_news.db").write_text("original-state", encoding="utf-8")
    (outputs / "run.json").write_text('{"status":"complete"}', encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    install_python3_shim(fake_bin)
    command_log = tmp_path / "docker-commands.log"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import shutil
import sys
import tarfile

def native_path(value):
    if len(value) > 3 and value[0] == "/" and value[2] == "/":
        return value[1] + ":" + value[2:]
    return value

arguments = sys.argv[1:]
with pathlib.Path(os.environ["FAKE_DOCKER_LOG"]).open("a", encoding="utf-8") as log:
    log.write(" ".join(arguments) + "\\n")

if arguments[:1] == ["compose"]:
    if "ps" in arguments and "--quiet" in arguments:
        print(f"{arguments[-1]}-id")
    elif "ps" in arguments:
        print("scheduler running healthy")
        print("dashboard running healthy")
        print("caddy running healthy")
    elif "logs" in arguments:
        print(f"{arguments[-1]} test log")
elif arguments[:1] == ["inspect"]:
    template = arguments[arguments.index("--format") + 1]
    if ".Mounts" in template and "/data/state" in template:
        print("fake_app_data")
    elif ".Mounts" in template and "/data/outputs" in template:
        print("fake_generated_outputs")
    elif ".State.Status" in template:
        print("running")
    elif ".State.Health" in template:
        print("healthy")
elif arguments[:1] == ["system"] and arguments[1:2] == ["df"]:
    print("TYPE TOTAL ACTIVE SIZE RECLAIMABLE")
elif arguments[:1] == ["run"]:
    mounts = {}
    environment = {}
    for index, argument in enumerate(arguments):
        if argument == "--volume":
            source, target, *_ = arguments[index + 1].split(":")
            mounts[target] = native_path(source)
        elif argument == "--env":
            key, value = arguments[index + 1].split("=", 1)
            environment[key] = value

    volume_paths = {
        "fake_app_data": pathlib.Path(os.environ["FAKE_APP_DATA"]),
        "fake_generated_outputs": pathlib.Path(os.environ["FAKE_OUTPUTS"]),
    }
    if "/source" in mounts and "/backup" in mounts:
        source = volume_paths[mounts["/source"]]
        destination = pathlib.Path(mounts["/backup"]) / environment["ARCHIVE_NAME"]
        with tarfile.open(destination, "w:gz") as archive:
            archive.add(source, arcname=".")
    elif "/target" in mounts and "/backup" in mounts:
        target = volume_paths[mounts["/target"]]
        for child in target.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        archive_path = pathlib.Path(mounts["/backup"]) / environment["ARCHIVE_NAME"]
        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(target)
    elif "/source" in mounts:
        source = volume_paths[mounts["/source"]]
        size = sum(path.stat().st_size for path in source.rglob("*") if path.is_file())
        print(f"{size} /source")
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    fake_curl = fake_bin / "curl"
    fake_curl.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_curl.chmod(0o755)
    fake_flock = fake_bin / "flock"
    fake_flock.write_text(
        '#!/usr/bin/env bash\n[[ "${FAKE_FLOCK_FAIL:-0}" == "1" ]] && exit 1\nexit 0\n',
        encoding="utf-8",
    )
    fake_flock.chmod(0o755)
    fake_install = fake_bin / "install"
    fake_install.write_text(
        '#!/usr/bin/env bash\n[[ "$1" == "-d" ]] || exit 2\ntarget="${@: -1}"\n[[ -d "$target" ]] || mkdir "$target"\n',
        encoding="utf-8",
        newline="\n",
    )
    fake_install.chmod(0o755)
    fake_sha256sum = fake_bin / "sha256sum"
    fake_sha256sum.write_text(
        "#!/usr/bin/env bash\n/usr/bin/sha256sum \"$@\" | sed 's/ \\*/  /'\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_sha256sum.chmod(0o755)

    backup_root = tmp_path / "backups"
    environment = os.environ.copy()
    environment.update(
        {
            "FAKE_DOCKER_LOG": str(command_log),
            "FAKE_APP_DATA": str(app_data),
            "FAKE_OUTPUTS": str(outputs),
            "BACKUP_DIR": str(backup_root),
            "_TEST_BACKUP_DIR": str(backup_root),
            "BACKUP_HEALTH_TIMEOUT_SECONDS": "2",
            "RESTORE_HEALTH_TIMEOUT_SECONDS": "2",
        }
    )
    prepend_path(environment, fake_bin)
    if os.name == "nt":
        environment["BACKUP_DIR"] = bash_path(backup_root)
    return deployment, environment, command_log, app_data, outputs


def _run(script: Path, *arguments: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        bash_command(script, *arguments),
        cwd=script.parents[1],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_status_and_healthcheck_do_not_print_secrets(tmp_path: Path) -> None:
    deployment, environment, _, _, _ = _operations_fixture(tmp_path)

    health = _run(deployment / "scripts/healthcheck.sh", environment=environment)
    status = _run(deployment / "scripts/status.sh", environment=environment)

    assert health.returncode == 0, health.stderr
    assert status.returncode == 0, status.stderr
    assert "scheduler: healthy" in health.stdout
    assert "Docker disk usage" in status.stdout
    assert "do-not-print-or-back-up" not in health.stdout + health.stderr
    assert "do-not-print-or-back-up" not in status.stdout + status.stderr


def test_backup_and_forced_restore_round_trip(tmp_path: Path) -> None:
    deployment, environment, command_log, app_data, outputs = _operations_fixture(tmp_path)

    backup = _run(deployment / "scripts/backup.sh", environment=environment)
    assert backup.returncode == 0, backup.stderr

    backup_directories = [path for path in Path(environment["_TEST_BACKUP_DIR"]).iterdir() if not path.name.startswith(".")]
    assert len(backup_directories) == 1
    backup_directory = backup_directories[0]
    assert (backup_directory / "manifest.txt").is_file()
    assert (backup_directory / "SHA256SUMS").is_file()
    assert not any("do-not-print-or-back-up" in path.read_text(errors="ignore") for path in backup_directory.iterdir())

    (app_data / "ai_news.db").write_text("changed-state", encoding="utf-8")
    (outputs / "run.json").unlink()
    (outputs / "partial.tmp").write_text("partial", encoding="utf-8")

    restore = _run(
        deployment / "scripts/restore.sh",
        "--force",
        bash_path(backup_directory) if os.name == "nt" else str(backup_directory),
        environment=environment,
    )

    assert restore.returncode == 0, restore.stderr
    assert (app_data / "ai_news.db").read_text(encoding="utf-8") == "original-state"
    assert (outputs / "run.json").read_text(encoding="utf-8") == '{"status":"complete"}'
    assert not (outputs / "partial.tmp").exists()

    commands = command_log.read_text(encoding="utf-8")
    assert " stop --timeout 60 scheduler\n" in commands
    assert " stop --timeout 60 scheduler dashboard\n" in commands
    assert " down" not in commands
    assert "volume rm" not in commands
    assert "do-not-print-or-back-up" not in commands


def test_restore_rejects_tampered_backup_before_stopping_services(tmp_path: Path) -> None:
    deployment, environment, command_log, _, _ = _operations_fixture(tmp_path)
    backup = _run(deployment / "scripts/backup.sh", environment=environment)
    assert backup.returncode == 0, backup.stderr
    backup_directory = next(Path(environment["_TEST_BACKUP_DIR"]).glob("20*-*Z"))
    with (backup_directory / "app_data.tar.gz").open("ab") as archive:
        archive.write(b"tampered")
    command_log.write_text("", encoding="utf-8")

    restore = _run(
        deployment / "scripts/restore.sh",
        "--force",
        bash_path(backup_directory) if os.name == "nt" else str(backup_directory),
        environment=environment,
    )

    assert restore.returncode != 0
    assert "checksum mismatch" in restore.stderr
    assert " stop " not in command_log.read_text(encoding="utf-8")


def test_retention_is_confined_to_timestamped_backup_children(tmp_path: Path) -> None:
    deployment, environment, _, _, _ = _operations_fixture(tmp_path)
    backup_root = Path(environment["_TEST_BACKUP_DIR"])
    backup_root.mkdir()
    expired = backup_root / "2026-01-01_000000Z"
    preserved = backup_root / "manual-preserve"
    outside = tmp_path / "2026-01-01_000001Z"
    expired.mkdir()
    preserved.mkdir()
    outside.mkdir()
    old_time = time.time() - 10 * 24 * 60 * 60
    for path in (expired, preserved, outside):
        os.utime(path, (old_time, old_time))
    environment["BACKUP_RETENTION_DAYS"] = "1"

    result = _run(deployment / "scripts/backup.sh", environment=environment)

    assert result.returncode == 0, result.stderr
    assert not expired.exists()
    assert preserved.exists()
    assert outside.exists()


def test_backup_lock_contention_does_not_stop_scheduler(tmp_path: Path) -> None:
    deployment, environment, command_log, _, _ = _operations_fixture(tmp_path)
    environment["FAKE_FLOCK_FAIL"] = "1"

    result = _run(deployment / "scripts/backup.sh", environment=environment)

    assert result.returncode != 0
    assert "already running" in result.stderr
    assert " stop " not in command_log.read_text(encoding="utf-8")


def test_operational_scripts_have_valid_bash_syntax_and_are_executable() -> None:
    repository = Path(__file__).resolve().parents[1]
    scripts = sorted((repository / "scripts").glob("*.sh"))

    result = subprocess.run(
        bash_command("-n", *scripts),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert all(os.access(script, os.X_OK) for script in scripts)


def test_production_compose_rotates_logs_for_every_service() -> None:
    repository = Path(__file__).resolve().parents[1]
    compose = (repository / "compose.production.yaml").read_text(encoding="utf-8")

    assert compose.count("logging: *default-logging") == 3
    assert "max-size: ${DOCKER_LOG_MAX_SIZE:-10m}" in compose
    assert "max-file: ${DOCKER_LOG_MAX_FILES:-5}" in compose


def test_deploy_workflow_uploads_all_operational_scripts() -> None:
    repository = Path(__file__).resolve().parents[1]
    workflow = (repository / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    for script_name in (
        "backup.sh",
        "healthcheck.sh",
        "operations-common.sh",
        "restore.sh",
        "status.sh",
    ):
        assert f"scripts/{script_name}" in workflow
    assert ".incoming-${RELEASE_ID}" in workflow
    assert "OPERATIONS_LOCK_FD=9" in workflow

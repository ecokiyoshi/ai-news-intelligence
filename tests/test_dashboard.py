import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _write_run(root: Path) -> str:
    run_id = "20260809T010203000000Z-test1234"
    directory = root / run_id
    directory.mkdir()
    (directory / "scene_000.png").write_bytes(b"fake-png")
    (directory / "run.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "created_at": "2026-08-09T01:02:03+00:00",
                "channel_focus": "AI news",
                "selected_youtube_idea": {"title": "AI News Project"},
                "youtube_potential": {"score": 91},
                "selected_packaging": {"title": "Best AI News Title"},
                "script": {"text": "15 minute script body"},
                "dialogue": {"text": "さび助: テスト\nハル: テスト"},
                "visual_plan": {"scenes": [{"scene_id": "scene_000"}]},
                "generated_images": [
                    {"scene_id": "scene_000", "file_path": str(directory / "scene_000.png")}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return run_id


def test_dashboard_lists_runs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    run_id = _write_run(tmp_path)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Best AI News Title" in response.text
    assert run_id in response.text


def test_dashboard_run_shows_script_dialogue_visuals_and_image(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    run_id = _write_run(tmp_path)

    response = client.get(f"/dashboard/runs/{run_id}")

    assert response.status_code == 200
    assert "15 minute script body" in response.text
    assert "さび助" in response.text
    assert "scene_000" in response.text
    assert f"/dashboard/runs/{run_id}/images/scene_000.png" in response.text


def test_runs_api_returns_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    run_id = _write_run(tmp_path)

    response = client.get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    assert response.json()["selected_packaging"]["title"] == "Best AI News Title"


def test_dashboard_rejects_missing_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))

    assert client.get("/dashboard/runs/missing").status_code == 404

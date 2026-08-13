import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_dashboard_authentication_protects_html_and_api(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_REQUIRED", "true")
    monkeypatch.setenv("DASHBOARD_USERNAME", "editor")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "a-long-test-password")

    for path in ("/dashboard", "/api/runs", "/docs"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Basic ")

    assert client.get("/dashboard", auth=("editor", "wrong")).status_code == 401
    assert client.get("/dashboard", auth=("editor", "a-long-test-password")).status_code == 200
    assert client.get("/health").status_code == 200


def test_required_authentication_rejects_incomplete_configuration(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_REQUIRED", "true")
    monkeypatch.setenv("DASHBOARD_USERNAME", "editor")
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)

    assert client.get("/dashboard").status_code == 503


def sample_run(run_id: str, created_at: str = "2026-08-09T12:00:00+00:00") -> dict:
    return {
        "run_id": run_id,
        "created_at": created_at,
        "channel_focus": "日本のAIニュース",
        "priority_news": [{"article_id": 1}],
        "selected_youtube_idea": {"title": "AIの現在地", "hook": "何が変わった？"},
        "youtube_potential": {"youtube_potential_score": 87},
        "selected_packaging": {"title": "AIの現在地を15分で", "thumbnail_text": "大転換"},
        "script": {
            "opening_hook": "始めましょう。",
            "chapters": [{"chapter_index": 0, "title": "変化", "objective": "理解する"}],
            "narration_sections": [{"chapter_index": 0, "narration": "完全な台本文です。"}],
            "closing": "ご視聴ありがとうございました。",
        },
        "dialogue": {
            "opening_lines": [{"line_index": 0, "speaker": "さび助", "text": "解説するよ。"}],
            "chapters": [{"chapter_index": 0, "title": "変化", "lines": [{"line_index": 0, "speaker": "ハル", "text": "教えて！"}]}],
            "closing_lines": [],
        },
        "visual_plan": {"scenes": [{"scene_index": 0, "purpose": "導入", "image_prompt": "未来の東京"}]},
        "generated_images": [{"scene_index": 0, "file_path": "/data/outputs/x/scene_000.png", "prompt_used": "未来の東京"}],
        "providers": {"script_generator": {"provider": "Local", "model": None}},
    }


def write_run(root: Path, data: dict) -> Path:
    run_dir = root / data["run_id"]
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return run_dir


def review_run(run_id: str) -> dict:
    data = sample_run(run_id)
    data["selected_youtube_idea"].update(
        {
            "source_article_ids": [1], "angle": "angle", "target_audience": "viewers",
            "estimated_length_minutes": 10, "thumbnail_text": "AI", "chapters": ["chapter"],
            "seo_keywords": ["AI"],
        }
    )
    data["script"].update(
        {"title": "AI", "thumbnail_text": "AI", "target_minutes": 10,
         "seo_keywords": ["AI"], "chapters": [{"chapter_index": 0, "title": "chapter",
         "objective": "explain", "estimated_seconds": 300, "key_points": ["fact"]}]}
    )
    data["dialogue"].update(
        {"title": "AI", "thumbnail_text": "AI", "target_minutes": 10,
         "seo_keywords": ["AI"], "closing_lines": [{"line_index": 0, "speaker": "ハル", "text": "完了"}]}
    )
    data.update(
        {"editorial": {"schema": "ai-news-intelligence/editorial-workflow", "schema_version": 1,
         "status": "in_review", "revision": 1, "generated_at": "2026-08-14T00:00:00Z",
         "updated_at": "2026-08-14T00:00:00Z", "approved_at": None},
         "visual_plan": None, "generated_images": [], "output_files": ["run.json"]}
    )
    return data


def test_empty_dashboard(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "missing"))
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "No projects yet" in response.text


def test_runs_are_newest_first_and_malformed_run_is_skipped(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    write_run(tmp_path, sample_run("older", "2026-08-08T12:00:00Z"))
    write_run(tmp_path, sample_run("newer", "2026-08-09T12:00:00Z"))
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "run.json").write_text("{broken", encoding="utf-8")

    response = client.get("/api/runs")
    assert response.status_code == 200
    assert [run["run_id"] for run in response.json()] == ["newer", "older"]
    assert response.json()[0]["scene_count"] == 1
    assert response.json()[0]["image_count"] == 1
    assert response.json()[0]["editorial_status"] == "completed"


def test_run_api_detail_and_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    write_run(tmp_path, sample_run("run-one"))
    assert client.get("/api/runs/run-one").json()["channel_focus"] == "日本のAIニュース"
    assert client.get("/api/runs/not-found").status_code == 404


def test_editorial_edit_save_conflict_and_approval(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    data = review_run("review-one")
    write_run(tmp_path, data)

    edit = client.get("/dashboard/runs/review-one/edit")
    assert edit.status_code == 200
    assert "企画・台本・対話" in edit.text
    content = {key: data[key] for key in ("selected_youtube_idea", "script", "dialogue")}
    content["selected_youtube_idea"]["title"] = "Edited title"
    saved = client.put(
        "/api/runs/review-one/editorial",
        json={"expected_revision": 1, "content": content},
    )
    assert saved.status_code == 200
    assert saved.json()["editorial"]["revision"] == 2
    assert client.put(
        "/api/runs/review-one/editorial",
        json={"expected_revision": 1, "content": content},
    ).status_code == 409

    approved = client.post(
        "/api/runs/review-one/approve", json={"expected_revision": 2}
    )
    assert approved.status_code == 200
    assert approved.json()["editorial"]["status"] == "approved"
    assert client.get("/dashboard/runs/review-one/edit").status_code == 409


def test_dashboard_detail_contains_complete_content(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    write_run(tmp_path, sample_run("run-one"))
    response = client.get("/dashboard/runs/run-one")
    assert response.status_code == 200
    assert "完全な台本文です。" in response.text
    assert "教えて！" in response.text
    assert "未来の東京" in response.text
    assert "Raw JSON" in response.text


def test_image_serving_and_traversal_protection(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    run_dir = write_run(tmp_path, sample_run("run-one"))
    image = b"\x89PNG\r\n\x1a\nimage"
    (run_dir / "scene_000.png").write_bytes(image)
    (tmp_path / "secret.png").write_bytes(b"secret")

    response = client.get("/dashboard/runs/run-one/images/scene_000.png")
    assert response.status_code == 200
    assert response.content == image
    for path in ("../secret.png", "../../secret.png", "%2e%2e/secret.png"):
        assert client.get(f"/dashboard/runs/run-one/images/{path}").status_code in {404, 422}


def test_html_escapes_run_content(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    data = sample_run("safe")
    data["script"]["closing"] = '<script>alert("x")</script>'
    write_run(tmp_path, data)
    response = client.get("/dashboard/runs/safe")
    assert '<script>alert("x")</script>' not in response.text
    assert "&lt;script&gt;" in response.text

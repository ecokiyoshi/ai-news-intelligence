"""Read-only browser dashboard for generated YouTube production runs."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter()


def output_root() -> Path:
    return Path(os.getenv("OUTPUT_DIR", "/data/outputs"))


def _run_path(run_id: str) -> Path:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise HTTPException(status_code=404, detail="Run not found")
    path = output_root() / run_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Run not found")
    return path


def load_run(run_id: str) -> dict[str, Any]:
    metadata = _run_path(run_id) / "run.json"
    if not metadata.is_file():
        raise HTTPException(status_code=404, detail="run.json not found")
    try:
        value = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Unable to read run metadata") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=500, detail="Invalid run metadata")
    return value


def list_runs() -> list[dict[str, Any]]:
    root = output_root()
    if not root.is_dir():
        return []
    runs: list[dict[str, Any]] = []
    for directory in root.iterdir():
        metadata = directory / "run.json"
        if not directory.is_dir() or not metadata.is_file():
            continue
        try:
            data = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        idea = data.get("selected_youtube_idea") or {}
        packaging = data.get("selected_packaging") or {}
        runs.append(
            {
                "run_id": data.get("run_id", directory.name),
                "created_at": data.get("created_at", ""),
                "title": packaging.get("title") or idea.get("title") or directory.name,
                "channel_focus": data.get("channel_focus", ""),
                "image_count": len(data.get("generated_images") or []),
            }
        )
    return sorted(runs, key=lambda item: str(item["created_at"]), reverse=True)


def _pretty(value: Any) -> str:
    return html.escape(json.dumps(value, ensure_ascii=False, indent=2))


def _script_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("script", "full_script", "body", "text", "content"):
            if isinstance(value.get(key), str):
                return value[key]
    return json.dumps(value, ensure_ascii=False, indent=2)


def _layout(title: str, body: str) -> HTMLResponse:
    css = """
    :root{color-scheme:dark;--bg:#0b1020;--panel:#151d33;--muted:#9ba8c7;--accent:#67e8f9;--line:#26324f}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:#edf2ff;font:15px/1.65 system-ui,-apple-system,sans-serif}
    header{position:sticky;top:0;background:#0b1020e8;backdrop-filter:blur(12px);border-bottom:1px solid var(--line);padding:18px 5vw;z-index:2}
    header a{color:var(--accent);text-decoration:none}main{max-width:1280px;margin:auto;padding:32px 5vw 64px}
    h1,h2{line-height:1.2}h2{margin-top:36px}.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
    .card{display:block;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;color:inherit;text-decoration:none}.card:hover{border-color:var(--accent)}
    pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0f1629;border:1px solid var(--line);padding:18px;border-radius:12px;max-height:720px;overflow:auto}
    .images{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}.images figure{margin:0;background:var(--panel);padding:10px;border-radius:12px}.images img{width:100%;aspect-ratio:16/9;object-fit:contain;background:#080b12;border-radius:8px}.images figcaption{padding:8px 4px;color:var(--muted)}
    nav{display:flex;gap:14px;flex-wrap:wrap;margin:20px 0}nav a{color:var(--accent)}
    """
    return HTMLResponse(f"<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>{css}</style></head><body><header><a href='/dashboard'><strong>AI News Intelligence</strong></a> <span class='muted'>/ YouTube Dashboard</span></header><main>{body}</main></body></html>")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_index() -> HTMLResponse:
    runs = list_runs()
    cards = "".join(
        f"<a class='card' href='/dashboard/runs/{quote(str(run['run_id']))}'><h2>{html.escape(str(run['title']))}</h2><div class='muted'>{html.escape(str(run['created_at']))}</div><p>{html.escape(str(run['channel_focus']))}</p><strong>{run['image_count']} images</strong></a>"
        for run in runs
    ) or "<div class='card'><h2>No generated runs yet</h2><p class='muted'>Run the end-to-end pipeline and refresh this page.</p></div>"
    return _layout("YouTube Projects", f"<h1>Generated YouTube Projects</h1><p class='muted'>{len(runs)} production run(s)</p><div class='grid'>{cards}</div>")


@router.get("/dashboard/runs/{run_id}", response_class=HTMLResponse)
def dashboard_run(run_id: str) -> HTMLResponse:
    data = load_run(run_id)
    title = (data.get("selected_packaging") or {}).get("title") or (data.get("selected_youtube_idea") or {}).get("title") or run_id
    assets = data.get("generated_images") or []
    figures = []
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            continue
        filename = Path(str(asset.get("file_path", ""))).name
        if not filename:
            continue
        label = asset.get("scene_id") or asset.get("title") or filename
        figures.append(f"<figure><a href='/dashboard/runs/{quote(run_id)}/images/{quote(filename)}' target='_blank'><img loading='lazy' src='/dashboard/runs/{quote(run_id)}/images/{quote(filename)}' alt='{html.escape(str(label))}'></a><figcaption>{index + 1}. {html.escape(str(label))}</figcaption></figure>")
    body = f"""
    <a href='/dashboard'>← All projects</a><h1>{html.escape(str(title))}</h1><p class='muted'>Run: {html.escape(run_id)} · {html.escape(str(data.get('created_at','')))}</p>
    <nav><a href='#project'>Project</a><a href='#script'>15-minute Script</a><a href='#dialogue'>さび助 × ハル</a><a href='#visuals'>Visual Plan</a><a href='#images'>Images</a><a href='/api/runs/{quote(run_id)}'>Raw JSON</a></nav>
    <h2 id='project'>YouTube Project</h2><pre>{_pretty({'idea': data.get('selected_youtube_idea'), 'potential': data.get('youtube_potential'), 'packaging': data.get('selected_packaging')})}</pre>
    <h2 id='script'>15-minute Script</h2><pre>{html.escape(_script_text(data.get('script')))}</pre>
    <h2 id='dialogue'>さび助 × ハル Dialogue</h2><pre>{html.escape(_script_text(data.get('dialogue')))}</pre>
    <h2 id='visuals'>Visual Plan</h2><pre>{_pretty(data.get('visual_plan'))}</pre>
    <h2 id='images'>Generated Images</h2><div class='images'>{''.join(figures) or '<p class="muted">No generated images.</p>'}</div>
    """
    return _layout(str(title), body)


@router.get("/dashboard/runs/{run_id}/images/{filename}")
def dashboard_image(run_id: str, filename: str) -> FileResponse:
    if Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="Image not found")
    path = _run_path(run_id) / filename
    if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)


@router.get("/api/runs")
def api_runs() -> list[dict[str, Any]]:
    return list_runs()


@router.get("/api/runs/{run_id}")
def api_run(run_id: str) -> dict[str, Any]:
    return load_run(run_id)

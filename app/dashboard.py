"""Read-only browser dashboard for completed YouTube production runs."""

from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.editorial_workflow import editorial_status

router = APIRouter()

RUN_FILENAME = "run.json"
IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}


def output_directory() -> Path:
    """Return the configured output root without creating or changing it."""

    return Path(os.environ.get("OUTPUT_DIR", "generated-outputs")).expanduser()


def _run_directory(run_id: str, *, must_exist: bool = True) -> Path:
    """Resolve a direct child of OUTPUT_DIR, rejecting traversal and symlinks out."""

    root = output_directory().resolve()
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise HTTPException(status_code=404, detail="Run not found")
    candidate = (root / run_id).resolve()
    if candidate.parent != root or (must_exist and not candidate.is_dir()):
        raise HTTPException(status_code=404, detail="Run not found")
    return candidate


def load_run(run_id: str) -> dict[str, Any]:
    """Load one run document and map absent or corrupt documents to HTTP errors."""

    path = _run_directory(run_id) / RUN_FILENAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Run not found") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail="Run metadata is invalid") from error
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="Run metadata is invalid")
    return value


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    return fallback


def _image_filenames(run_id: str, data: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for asset in _list(data.get("generated_images")):
        file_path = _dict(asset).get("file_path")
        if isinstance(file_path, str):
            name = Path(file_path).name
            if name and Path(name).suffix.lower() in IMAGE_SUFFIXES:
                names.append(name)
    try:
        run_dir = _run_directory(run_id)
        names.extend(
            path.name
            for path in run_dir.iterdir()
            if path.is_file()
            and path.name.startswith("scene_")
            and path.suffix.lower() in IMAGE_SUFFIXES
        )
    except (HTTPException, OSError):
        pass
    return sorted(set(names))


def summarize_run(run_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Build the stable public summary while tolerating optional schema changes."""

    script = _dict(data.get("script"))
    dialogue = _dict(data.get("dialogue"))
    visual_plan = _dict(data.get("visual_plan"))
    packaging = _dict(data.get("selected_packaging"))
    idea = _dict(data.get("selected_youtube_idea"))
    potential = _dict(data.get("youtube_potential"))
    dialogue_count = len(_list(dialogue.get("opening_lines"))) + len(
        _list(dialogue.get("closing_lines"))
    ) + sum(len(_list(_dict(chapter).get("lines"))) for chapter in _list(dialogue.get("chapters")))
    return {
        "run_id": _text(data.get("run_id"), run_id),
        "created_at": data.get("created_at"),
        "channel_focus": data.get("channel_focus"),
        "title": packaging.get("title") or idea.get("title") or script.get("title"),
        "potential_score": potential.get("youtube_potential_score"),
        "chapter_count": len(_list(script.get("chapters"))),
        "dialogue_count": dialogue_count,
        "scene_count": len(_list(visual_plan.get("scenes"))),
        "image_count": len(_image_filenames(run_id, data)),
        "editorial_status": editorial_status(data),
    }


def _sort_timestamp(data: dict[str, Any], path: Path) -> float:
    value = data.get("created_at")
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            pass
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def discover_runs() -> list[dict[str, Any]]:
    """Return valid run summaries newest-first; one bad run never blocks others."""

    root = output_directory()
    if not root.is_dir():
        return []
    discovered: list[tuple[float, dict[str, Any]]] = []
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    for child in children:
        if not child.is_dir() or not (child / RUN_FILENAME).is_file():
            continue
        try:
            data = load_run(child.name)
        except HTTPException:
            continue
        discovered.append((_sort_timestamp(data, child / RUN_FILENAME), summarize_run(child.name, data)))
    discovered.sort(key=lambda item: item[0], reverse=True)
    return [summary for _, summary in discovered]


def _e(value: Any, fallback: str = "—") -> str:
    return html.escape(_text(value, fallback), quote=True)


def _numbered_label(value: Any, fallback: int) -> str:
    """Render zero-based indexes without trusting evolving JSON field types."""

    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return f"{value + 1:02d}"
    return f"{fallback + 1:02d}"


def _score_text(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.1f}".rstrip("0").rstrip(".")
    return _text(value)


def _label(key: str) -> str:
    labels = {
        "angle": "Angle", "chapters": "Chapters", "description": "Description",
        "estimated_length_minutes": "Estimated Length", "evaluation_reason": "Evaluation",
        "hook": "Hook", "packaging_score": "Packaging Score", "premise": "Premise",
        "rationale": "Rationale", "seo_keywords": "SEO Keywords",
        "source_article_ids": "Source Article IDs", "target_audience": "Target Audience",
        "thumbnail_text": "Thumbnail Text", "title": "Title",
    }
    return labels.get(key, key.replace("_", " ").title())


def _structured_fields(value: Any, *, omit: set[str] | None = None) -> str:
    data = _dict(value)
    omitted = omit or set()
    fields: list[str] = []
    for key, item in data.items():
        if key in omitted or item is None or item == "" or item == [] or item == {}:
            continue
        label = html.escape(_label(str(key)))
        if isinstance(item, list):
            rendered = "".join(
                f"<li>{html.escape(json.dumps(entry, ensure_ascii=False)) if isinstance(entry, (dict, list)) else _e(entry)}</li>"
                for entry in item
            )
            content = f'<ul class="compact-list">{rendered}</ul>'
        elif isinstance(item, dict):
            content = f'<pre class="mini-json">{html.escape(json.dumps(item, ensure_ascii=False, indent=2))}</pre>'
        else:
            content = f"<p>{_e(item)}</p>"
        fields.append(f'<div class="field"><dt>{label}</dt><dd>{content}</dd></div>')
    return f'<dl class="fields">{"".join(fields)}</dl>' if fields else '<p class="muted">No data available.</p>'


CSS = """
:root{color-scheme:dark;--bg:#090d16;--panel:#111827;--panel2:#172033;--line:#27334b;--text:#e8edf7;--muted:#96a3b8;--brand:#65d6c6;--accent:#8aa7ff;--warn:#f5c66c}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(circle at 75% 0,#15213b 0,transparent 34rem),var(--bg);color:var(--text);font-family:Inter,"Noto Sans JP",system-ui,-apple-system,sans-serif;line-height:1.65}a{color:inherit}.shell{width:min(1180px,calc(100% - 40px));margin:auto}.topbar{border-bottom:1px solid var(--line);background:#090d16dc;backdrop-filter:blur(16px);position:sticky;top:0;z-index:10}.topbar .shell{height:72px;display:flex;align-items:center;justify-content:space-between}.brand{font-weight:800;letter-spacing:-.02em;text-decoration:none}.brand small{display:block;color:var(--muted);font-size:.7rem;letter-spacing:.12em;text-transform:uppercase}.nav{color:var(--muted);text-decoration:none}.hero{padding:64px 0 30px}.eyebrow{color:var(--brand);font-size:.76rem;font-weight:800;letter-spacing:.15em;text-transform:uppercase}.hero h1{font-size:clamp(2rem,5vw,3.6rem);line-height:1.08;letter-spacing:-.045em;margin:.35rem 0}.hero p{color:var(--muted);max-width:700px}.grid{display:grid;gap:16px}.project{padding:24px;background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:18px;text-decoration:none;transition:.18s transform,.18s border}.project:hover{transform:translateY(-2px);border-color:#52698f}.project-top{display:flex;gap:18px;justify-content:space-between}.project h2{font-size:1.18rem;margin:.3rem 0}.date,.muted{color:var(--muted)}.score{white-space:nowrap;color:var(--brand);font-weight:800}.stats{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px}.pill{font-size:.78rem;border:1px solid var(--line);border-radius:999px;padding:4px 10px;color:#cbd5e4}.empty{padding:70px 24px;text-align:center;border:1px dashed var(--line);border-radius:18px;color:var(--muted)}.detail-layout{display:grid;grid-template-columns:210px minmax(0,1fr);gap:28px;padding:32px 0 70px}.side{position:sticky;top:100px;align-self:start}.side a{display:block;padding:7px 0;color:var(--muted);text-decoration:none;font-size:.9rem}.side a:hover{color:var(--brand)}.content{min-width:0}.title{font-size:clamp(1.8rem,4vw,3rem);line-height:1.18;letter-spacing:-.035em;margin:8px 0 28px}.section{scroll-margin-top:95px;background:#101725d9;border:1px solid var(--line);border-radius:18px;padding:clamp(20px,4vw,34px);margin-bottom:18px}.section h2{margin:0 0 24px;font-size:1.28rem}.overview{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.metric{background:#0b111d;border:1px solid #202b40;border-radius:12px;padding:14px}.metric span{display:block;color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.08em}.metric strong{display:block;margin-top:3px;overflow-wrap:anywhere}.fields{display:grid;gap:0;margin:0}.field{display:grid;grid-template-columns:180px 1fr;gap:20px;border-top:1px solid var(--line);padding:15px 0}.field:first-child{border:0;padding-top:0}.field dt{color:var(--muted);font-size:.82rem;font-weight:700}.field dd,.field p{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.compact-list{margin:0;padding-left:20px}.chapter{border-top:1px solid var(--line);padding:24px 0}.chapter:first-of-type{border:0;padding-top:0}.chapter h3{margin:0 0 4px}.objective{color:var(--muted);font-size:.88rem}.narration{white-space:pre-wrap;font-family:ui-serif,"Noto Serif JP",serif;font-size:1.02rem;line-height:1.9;margin-top:18px}.copy{float:right;background:#1d2940;color:var(--text);border:1px solid #34445f;border-radius:9px;padding:7px 12px;cursor:pointer}.dialogue-group{border-top:1px solid var(--line);padding:20px 0}.dialogue-group:first-of-type{border:0}.line{display:grid;grid-template-columns:72px 1fr;gap:14px;margin:13px 0}.speaker{font-weight:800;color:var(--brand)}.speaker.haru{color:var(--accent)}.line p{margin:0;white-space:pre-wrap}.scenes{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.scene{background:#0b111d;border:1px solid #202b40;border-radius:13px;padding:18px}.scene h3{margin:0 0 12px}.gallery{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.image-card{margin:0;background:#0b111d;border:1px solid #202b40;border-radius:13px;overflow:hidden}.image-card img{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;background:#05080e}.image-card figcaption{padding:13px;font-size:.82rem;color:var(--muted);overflow-wrap:anywhere}.image-card figcaption strong{color:var(--text);display:block}.raw{max-height:680px;overflow:auto;background:#070b12;border:1px solid #202b40;padding:18px;border-radius:12px;white-space:pre-wrap;word-break:break-word;font-size:.79rem}.mini-json{white-space:pre-wrap;overflow-wrap:anywhere;margin:0}.providers{margin-top:18px}footer{color:var(--muted);padding:0 0 45px;text-align:center;font-size:.8rem}@media(max-width:760px){.shell{width:min(100% - 24px,1180px)}.topbar .shell{height:60px}.detail-layout{display:block}.side{display:none}.hero{padding-top:38px}.overview{grid-template-columns:repeat(2,1fr)}.field{display:block}.field dt{margin-bottom:6px}.scenes,.gallery{grid-template-columns:1fr}.project-top{display:block}.score{margin-top:8px}.line{grid-template-columns:58px 1fr}.section{border-radius:14px}}
"""


def _page(title: str, body: str) -> HTMLResponse:
    document = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} · AI News Intelligence</title><style>{CSS}</style></head><body><header class="topbar"><div class="shell"><a class="brand" href="/dashboard">AI News Intelligence<small>YouTube Production</small></a><a class="nav" href="/dashboard">Projects</a></div></header>{body}<footer class="shell">Read-only production artifact viewer</footer><script>document.addEventListener('click',async(e)=>{{const b=e.target.closest('[data-copy]');if(!b)return;const source=document.querySelector(b.dataset.copy);if(!source)return;await navigator.clipboard.writeText(source.innerText);const old=b.textContent;b.textContent='Copied';setTimeout(()=>b.textContent=old,1400)}})</script></body></html>"""
    return HTMLResponse(document)


def _project_card(run: dict[str, Any]) -> str:
    run_id = _text(run.get("run_id"))
    encoded = quote(run_id, safe="")
    title = _text(run.get("title"), "Untitled YouTube project")
    score = _score_text(run.get("potential_score"))
    return f"""<a class="project" href="/dashboard/runs/{encoded}"><div class="project-top"><div><div class="date">{_e(run.get('created_at'))}</div><h2>{_e(title)}</h2><div class="muted">{_e(run.get('channel_focus'))} · {_e(run_id)}</div></div><div class="score">Potential {html.escape(score)}</div></div><div class="stats"><span class="pill">{_e(run.get('editorial_status'))}</span><span class="pill">{_e(run.get('chapter_count'))} chapters</span><span class="pill">{_e(run.get('dialogue_count'))} dialogue lines</span><span class="pill">{_e(run.get('scene_count'))} scenes</span><span class="pill">{_e(run.get('image_count'))} images</span></div></a>"""


@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/dashboard", status_code=307)


@router.get("/api/runs")
def runs_api() -> list[dict[str, Any]]:
    return discover_runs()


@router.get("/api/runs/{run_id}")
def run_api(run_id: str) -> dict[str, Any]:
    return load_run(run_id)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    runs = discover_runs()
    projects = "".join(_project_card(run) for run in runs)
    if not projects:
        projects = '<div class="empty"><h2>No projects yet</h2><p>Completed production runs will appear here.</p></div>'
    body = f'<main class="shell"><section class="hero"><div class="eyebrow">Production archive</div><h1>YouTube Projects</h1><p>生成済みの企画、台本、対話、ビジュアルとシーン画像を閲覧できます。</p></section><div class="grid">{projects}</div></main>'
    return _page("Dashboard", body)


def _overview(run_id: str, data: dict[str, Any], summary: dict[str, Any]) -> str:
    source_count = len(_list(data.get("source_article_ids"))) or len(_list(data.get("priority_news")))
    items = [
        ("Run ID", data.get("run_id") or run_id), ("Created At", data.get("created_at")),
        ("Channel Focus", data.get("channel_focus")), ("Source Articles", source_count),
        ("Potential Score", _score_text(summary.get("potential_score"))), ("Generated Scenes", summary.get("scene_count")),
        ("Images", summary.get("image_count")), ("Editorial Status", summary.get("editorial_status")),
    ]
    metrics = "".join(f'<div class="metric"><span>{html.escape(label)}</span><strong>{_e(value)}</strong></div>' for label, value in items)
    providers = _structured_fields(data.get("providers"))
    return f'<div class="overview">{metrics}</div><div class="providers"><h3>Providers &amp; Models</h3>{providers}</div>'


def _script_html(value: Any) -> str:
    data = _dict(value)
    chapters = _list(data.get("chapters"))
    narrations = {_dict(item).get("chapter_index"): _dict(item).get("narration") for item in _list(data.get("narration_sections"))}
    parts = []
    if data.get("opening_hook"):
        parts.append(f'<article class="chapter"><h3>Opening Hook</h3><div class="narration">{_e(data["opening_hook"])}</div></article>')
    for position, raw in enumerate(chapters):
        chapter = _dict(raw)
        index = chapter.get("chapter_index", position)
        title = chapter.get("title") or f"Chapter {position + 1}"
        narration = narrations.get(index, chapter.get("narration") or chapter.get("body"))
        objective = chapter.get("objective")
        parts.append(f'<article class="chapter"><h3>Chapter {position + 1} · {_e(title)}</h3>{f"<div class=\"objective\">{_e(objective)}</div>" if objective else ""}<div class="narration">{_e(narration)}</div></article>')
    if data.get("closing"):
        parts.append(f'<article class="chapter"><h3>Closing</h3><div class="narration">{_e(data["closing"])}</div></article>')
    if not parts:
        return _structured_fields(value)
    return f'<button class="copy" data-copy="#script-text">Copy script</button><div id="script-text">{"".join(parts)}</div>'


def _dialogue_lines(lines: Any) -> str:
    rendered = []
    for raw in _list(lines):
        line = _dict(raw)
        speaker = _text(line.get("speaker"), "Speaker")
        css = " haru" if speaker == "ハル" else ""
        rendered.append(f'<div class="line"><div class="speaker{css}">{_e(speaker)}</div><p>「{_e(line.get("text"))}」</p></div>')
    return "".join(rendered)


def _dialogue_html(value: Any) -> str:
    data = _dict(value)
    groups = []
    opening = _dialogue_lines(data.get("opening_lines"))
    if opening:
        groups.append(f'<div class="dialogue-group"><h3>Opening</h3>{opening}</div>')
    for position, raw in enumerate(_list(data.get("chapters"))):
        chapter = _dict(raw)
        groups.append(f'<div class="dialogue-group"><h3>Chapter {position + 1} · {_e(chapter.get("title"))}</h3>{_dialogue_lines(chapter.get("lines"))}</div>')
    closing = _dialogue_lines(data.get("closing_lines"))
    if closing:
        groups.append(f'<div class="dialogue-group"><h3>Closing</h3>{closing}</div>')
    return "".join(groups) or _structured_fields(value)


def _visual_html(value: Any) -> str:
    data = _dict(value)
    cards = []
    for position, raw in enumerate(_list(data.get("scenes"))):
        scene = _dict(raw)
        index = scene.get("scene_index", position)
        cards.append(f'<article class="scene"><h3>Scene {_numbered_label(index, position)}</h3>{_structured_fields(scene, omit={"scene_index"})}</article>')
    return f'<div class="scenes">{"".join(cards)}</div>' if cards else _structured_fields(value)


def _gallery_html(run_id: str, data: dict[str, Any]) -> str:
    assets = {Path(str(asset.get("file_path"))).name: asset for asset in map(_dict, _list(data.get("generated_images"))) if asset.get("file_path")}
    scenes = {_dict(scene).get("scene_index"): _dict(scene) for scene in _list(_dict(data.get("visual_plan")).get("scenes"))}
    cards = []
    for position, filename in enumerate(_image_filenames(run_id, data)):
        asset = assets.get(filename, {})
        index = asset.get("scene_index", position)
        scene = scenes.get(index, {})
        url = f'/dashboard/runs/{quote(run_id, safe="")}/images/{quote(filename, safe="")}'
        prompt = scene.get("image_prompt") or asset.get("prompt_used")
        cards.append(f'<figure class="image-card"><a href="{url}" target="_blank" rel="noopener"><img src="{url}" alt="Scene {_e(index)}" loading="lazy"></a><figcaption><strong>Scene {_numbered_label(index, position)} · {_e(filename)}</strong>{_e(prompt)}</figcaption></figure>')
    return f'<div class="gallery">{"".join(cards)}</div>' if cards else '<p class="muted">No generated images are available.</p>'


def _audio_html(run_id: str) -> str:
    audio_dir = _run_directory(run_id) / "audio"
    try:
        manifest = json.loads((audio_dir / "manifest.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        manifest = {}
    players = []
    for item in map(_dict, _list(manifest.get("segments"))):
        filename = item.get("file")
        if isinstance(filename, str) and (audio_dir / filename).is_file():
            url = f'/api/projects/{quote(run_id, safe="")}/audio/{quote(filename, safe="")}'
            players.append(f'<div class="line"><div class="speaker">{_e(item.get("display_name"))}</div><audio controls preload="none" src="{url}"></audio></div>')
    merged = manifest.get("merged_audio")
    merged_player = ""
    if isinstance(merged, str) and (audio_dir / merged).is_file():
        url = f'/api/projects/{quote(run_id, safe="")}/audio/{quote(merged, safe="")}'
        merged_player = f'<h3>Complete dialogue</h3><audio controls preload="metadata" src="{url}"></audio>'
    status = _e(manifest.get("status"), "not generated")
    return f'''<p>Status: <strong id="audio-status">{status}</strong></p><button class="copy" style="float:none" onclick="generateAudio()">音声生成</button>{merged_player}{"".join(players)}<script>async function generateAudio(){{const s=document.getElementById("audio-status");s.textContent="generating";try{{const r=await fetch("/api/projects/{quote(run_id, safe='')}/audio/generate",{{method:"POST",headers:{{"content-type":"application/json"}},body:JSON.stringify({{force:false,merge:true}})}});const j=await r.json();if(!r.ok)throw new Error(j.detail||"generation failed");location.reload()}}catch(e){{s.textContent="failed: "+e.message}}}}</script>'''


@router.get("/dashboard/runs/{run_id}", response_class=HTMLResponse)
def run_detail(run_id: str) -> HTMLResponse:
    data = load_run(run_id)
    summary = summarize_run(run_id, data)
    sections = [
        ("overview", "Overview", _overview(run_id, data, summary)),
        ("idea", "YouTube Idea", _structured_fields(data.get("selected_youtube_idea"))),
        ("packaging", "Packaging", _structured_fields(data.get("selected_packaging"))),
        ("script", "15-Minute YouTube Script", _script_html(data.get("script"))),
        ("dialogue", "さび助 × ハル Dialogue", _dialogue_html(data.get("dialogue"))),
        ("audio", "Dialogue Audio", _audio_html(run_id)),
        ("visuals", "Visual Plan", _visual_html(data.get("visual_plan"))),
        ("images", "Generated Images", _gallery_html(run_id, data)),
        ("json", "Raw JSON", f'<pre class="raw">{html.escape(json.dumps(data, ensure_ascii=False, indent=2))}</pre>'),
    ]
    side = "".join(f'<a href="#{anchor}">{html.escape(title)}</a>' for anchor, title, _ in sections)
    content = "".join(f'<section class="section" id="{anchor}"><h2>{html.escape(title)}</h2>{markup}</section>' for anchor, title, markup in sections)
    body = f'<main class="shell"><section class="hero"><div class="eyebrow">Project detail</div><h1 class="title">{_e(summary.get("title"), "Untitled project")}</h1></section><div class="detail-layout"><aside class="side">{side}</aside><div class="content">{content}</div></div></main>'
    return _page(_text(summary.get("title"), "Project"), body)


@router.get("/dashboard/runs/{run_id}/images/{filename:path}", response_class=FileResponse)
def run_image(run_id: str, filename: str) -> FileResponse:
    run_dir = _run_directory(run_id)
    if not filename or Path(filename).name != filename or Path(filename).suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=404, detail="Image not found")
    path = (run_dir / filename).resolve()
    if path.parent != run_dir or not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)

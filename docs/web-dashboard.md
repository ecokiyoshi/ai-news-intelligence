# Web Dashboard

The dashboard provides a read-only browser view of generated end-to-end YouTube production runs. It reads each `/data/outputs/<run-id>/run.json` and serves the generated scene images from the same run directory.

## Start with Docker Compose

```bash
docker compose up -d --build
```

Open:

```text
http://localhost:8000/dashboard
```

To use a different host port:

```bash
DASHBOARD_PORT=8080 docker compose up -d --build
```

Then open `http://localhost:8080/dashboard`.

The dashboard container mounts the existing `generated_outputs` volume read-only, so browsing projects cannot modify pipeline artifacts.

## What you can view

- production run list, newest first
- selected YouTube idea and potential score
- selected title/thumbnail packaging metadata
- generated 15-minute script
- `さび助 × ハル` dialogue script
- scene-by-scene Visual Plan
- generated 16:9 scene images
- raw `run.json` through the JSON API

## JSON API

```text
GET /api/runs
GET /api/runs/{run_id}
```

## Run locally without Docker

Set `OUTPUT_DIR` to the directory containing generated run folders and start FastAPI:

```bash
OUTPUT_DIR=./outputs uvicorn app.main:app --reload
```

Then open `http://localhost:8000/dashboard`.

## Health check

```text
GET /health
```

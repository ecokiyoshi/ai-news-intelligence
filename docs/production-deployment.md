# Production deployment

This document covers the production runtime for a single Ubuntu VPS and the image published by
GitHub Actions. Automated VPS deployment, backup, and monitoring are handled by later phases.

## Architecture

```text
Internet
  │ HTTPS :443 (and HTTP :80 for redirects/certificate issuance)
  ▼
Caddy ── internal Docker network ── dashboard:8000

scheduler ── outbound-only application network ── RSS/OpenAI
  ├── app_data:/data/state
  └── generated_outputs:/data/outputs

dashboard
  └── generated_outputs:/data/outputs:ro
```

Only Caddy publishes host ports. The dashboard is reachable only from Caddy on the internal
Docker network, and the scheduler publishes no port. Caddy obtains and renews HTTPS certificates
automatically after DNS points the configured domain to the VPS and ports 80/443 are reachable.

The deployment runs exactly one scheduler container. **Never use `--scale scheduler=...` or start
a second production Compose project.** The scheduler lock is process-local, so multiple instances
can duplicate paid OpenAI text and image generation.

## Services and persistent data

- `scheduler` runs `python -m app.runtime run`, handles SIGTERM/SIGINT, and uses the existing local
  database/writability health check. The check never contacts RSS or OpenAI.
- `dashboard` runs the existing FastAPI application on container port 8000. Its `/health` endpoint
  performs no paid work, and its generated output mount is read-only.
- `caddy` terminates HTTPS and proxies requests to `dashboard:8000`.
- `app_data` stores SQLite and scheduler state.
- `generated_outputs` stores images, `manifest.json`, and `run.json`.
- `caddy_data` and `caddy_config` retain Caddy certificates and runtime configuration.

Normal `docker compose down` preserves all named volumes. Do not use `down -v` in production.

## Configuration

Create the untracked production environment file and restrict its permissions:

```bash
cp .env.example .env
chmod 600 .env
```

Set every empty value required by the selected pipeline, especially:

- `APP_DOMAIN`: public DNS name, without `https://` or a path.
- `APP_IMAGE`: GHCR image tag used by both application services. Use an immutable SHA tag in
  production rather than relying only on `latest`.
- `OPENAI_API_KEY`: required when `SCHEDULER_PROVIDER=openai`; keep it only in `.env` on the VPS.
- `SCHEDULER_FEED_URLS`: comma-separated HTTP(S) RSS/Atom URLs.
- `SCHEDULER_RELEVANCE_TARGET`: scoring target used by the news pipeline.
- `YOUTUBE_CHANNEL_FOCUS`: required for `PIPELINE_MODE=end_to_end`.

The example defaults production to `SCHEDULER_PROVIDER=openai` and `PIPELINE_MODE=end_to_end`.
`YOUTUBE_SCENE_LIMIT=50` is a hard pre-request cost guard; lower it before the first production run
if desired. `OPENAI_MODEL`, `OPENAI_IMAGE_MODEL`, item counts, interval, image size, and `TZ` can be
changed in `.env`. Never commit `.env` or paste it into logs.

For a deterministic no-cost validation environment, override `SCHEDULER_PROVIDER=local`. Health
checks themselves never call providers or download feeds.

## Published images

After the `CI` workflow succeeds for a push to `main`, the `Publish production image` workflow
builds the validated commit once and publishes both of these tags:

```text
ghcr.io/ecokiyoshi/ai-news-intelligence:latest
ghcr.io/ecokiyoshi/ai-news-intelligence:sha-<40-character-commit-sha>
```

The workflow authenticates with the repository-scoped `GITHUB_TOKEN`; no personal access token is
required. Its permissions are limited to reading repository contents and writing packages. Pull
request workflows never publish an image.

The package may be private depending on its GHCR visibility. Authenticate Docker with a token that
can read the package before pulling a private image. Do not store that token in this repository.

Pin the VPS to the immutable tag recorded by the successful workflow:

```dotenv
APP_IMAGE=ghcr.io/ecokiyoshi/ai-news-intelligence:sha-0123456789abcdef0123456789abcdef01234567
```

`latest` is convenient for discovery but can move after every successful `main` build. An
immutable SHA tag makes the deployed application version auditable and allows rollback by changing
`APP_IMAGE` to a previously published SHA tag. Automated deployment and rollback commands are
added in the next phase.

## Validate and operate

Validate interpolation and the rendered Compose model before starting:

```bash
docker compose --env-file .env -f compose.production.yaml config --quiet
```

Pull the selected image, then start all three services:

```bash
docker compose --env-file .env -f compose.production.yaml pull scheduler dashboard
docker compose --env-file .env -f compose.production.yaml up -d
docker compose --env-file .env -f compose.production.yaml ps
```

For local production-stack development, the retained `build` configuration still supports:

```bash
docker compose --env-file .env -f compose.production.yaml up -d --build
```

Stop, restart, or remove containers while retaining data:

```bash
docker compose --env-file .env -f compose.production.yaml stop
docker compose --env-file .env -f compose.production.yaml restart
docker compose --env-file .env -f compose.production.yaml down
```

Inspect status and logs without printing environment variables:

```bash
docker compose --env-file .env -f compose.production.yaml ps
docker compose --env-file .env -f compose.production.yaml logs --tail=200 scheduler
docker compose --env-file .env -f compose.production.yaml logs --tail=200 dashboard
docker compose --env-file .env -f compose.production.yaml logs --tail=200 caddy
```

Check health locally on the VPS and through the public route:

```bash
docker compose --env-file .env -f compose.production.yaml exec dashboard \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
curl --fail --show-error "https://${APP_DOMAIN}/health"
```

The public response is `{"status":"ok"}`. A health check never starts a scheduled or one-shot
pipeline. Use `docker compose ... ps` and the recent service logs when a container is unhealthy.

Production does not expose port 8000, SQLite, scheduler endpoints, or the Docker socket; it also
uses no privileged containers or arbitrary host-directory mounts. The only bind mount is the
read-only tracked Caddyfile.

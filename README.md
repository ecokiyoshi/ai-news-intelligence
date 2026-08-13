# AI News Intelligence

## Run the full test suite on Windows

Git for Windows includes the Bash runtime needed by the deployment and operations tests. From
PowerShell, run:

```powershell
.\scripts\test-windows.ps1 -Python C:\path\to\python.exe
```

The selected Python must have the project development dependencies installed (for example,
`python -m pip install -e ".[dev]"`). The runner locates Git Bash under the standard Git for Windows
installation path, exports `AI_NEWS_BASH`, and runs the same pytest suite used on Linux. Override a
non-standard Bash installation with `AI_NEWS_BASH=C:\path\to\bash.exe`. No WSL, Docker Desktop, or
administrator privileges are required for these offline tests.

## Adobe Premiere Pro UXP timeline builder (Phase 1)

Completed production artifacts can be converted into a provider-neutral
`premiere-edit-plan.json`, then loaded by the UXP plugin in `premiere-uxp/`:

```text
run.json + manifest.json + scene images + audio/manifest.json + dialogue MP3s
→ premiere-edit-plan.json
→ Premiere Pro UXP panel
→ editable 16:9 sequence
```

Generate and validate a plan without contacting Adobe, OpenAI, or ElevenLabs:

```bash
python -m app.premiere_edit_plan generated-outputs/RUN_ID --require-audio
```

Omit `--require-audio` when dialogue audio has not been generated yet. The plan then records
deterministic text-based duration estimates and `null` audio assets. When an audio manifest supplies
`duration_seconds`/`duration_ms`, that timing is preferred; otherwise the builder probes local media
with `ffprobe` when available and falls back to an estimate. Re-run the command after generating
audio to obtain actual-media timing.

The plan uses paths relative to its run directory, rejects traversal/out-of-run paths, maps scene
images in source order, maps さび助 and ハル to distinct logical audio roles, and includes only the
metadata required for editing. Provider configuration and environment values are never copied.

### Load the UXP plugin

Phase 1 requires Adobe Premiere Pro 25.6 or newer, UXP Developer Tool 2.2 or newer, and Manifest v5.
In UXP Developer Tool, choose **Add Plugin**, select `premiere-uxp/manifest.json`, start Premiere,
then load the plugin and open **AI News Timeline Builder**. The manifest requests user-selected local
filesystem access only; it does not request network access or unrestricted filesystem access.

1. Open the target Premiere project.
2. Select `premiere-edit-plan.json` in the panel.
3. Validate, then choose **Build timeline**.
4. If the deterministic generated sequence already exists, the normal build refuses to duplicate it.
   Use **Rebuild generated timeline** and confirm to delete/recreate only that named sequence.

The logical layout is `video.scene`, `dialogue.sabisuke`, `dialogue.haru`, and
`captions.overlay`. Additional BGM, SFX, transition, and motion-graphics roles are reserved for later
schema-compatible phases. Imported media is reused when Premiere finds an existing project item for
the same path.

### Known Phase 1 limitations and troubleshooting

- Premiere Pro UXP 25.6 documents sequence, import, and timeline insertion APIs, but no supported
  caption-creation mutation API. Subtitle and overlay entries therefore remain in the validated plan
  as safe sidecar metadata; the plugin reports their count and does not claim to create captions.
- BGM, transitions, MOGRT automation, export/render, and YouTube upload are intentionally out of scope.
- A missing-media error means an asset was moved after plan generation; restore it under the run
  directory or regenerate the plan. Absolute paths and `..` traversal are deliberately rejected.
- An unsupported schema/version error means the plugin and plan generator must be updated together.
- Automated tests cover plan generation and static UXP logic only. A real Premiere execution test
  requires Premiere Pro and must be reported separately.

Adobe references used for this implementation: [Premiere UXP APIs](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/),
[Project import/sequence APIs](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/project/),
[SequenceEditor](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequenceeditor/),
[UXP filesystem permissions](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/filesystem-operations/),
and [Manifest v5](https://developer.adobe.com/premiere-pro/uxp/plugins/concepts/manifest/).

## ElevenLabs Japanese dialogue audio

Completed `run.json` dialogue can be synthesized as separate voices for さび助 and ハル. Set `ELEVENLABS_API_KEY`, `ELEVENLABS_SABISUKE_VOICE_ID`, and `ELEVENLABS_HARU_VOICE_ID` as runtime secrets; never commit a populated `.env`. Optional settings are documented in `.env.example`.

Generate with `python -m app.audio generate --project-id RUN_ID --merge`; add `--force` to regenerate every segment. The project dashboard offers the same action and audio players. The HTTP endpoint is `POST /api/projects/{project_id}/audio/generate` with `{"force": false, "merge": true}`.

Output is stored under `OUTPUT_DIR/RUN_ID/audio/` as numbered MP3 files and `manifest.json`; optional merged output is `dialogue_full.mp3`. Matching speaker/text/model segments are reused, and completed progress remains available after a later segment fails.

Backend foundation for AI News Intelligence, built with Python and FastAPI.

## Production deployment

The production runtime uses a separate Compose file with one scheduler, the internal-only
dashboard, and Caddy automatic HTTPS. See [Production deployment](docs/production-deployment.md)
for Amazon Lightsail provisioning, secure GitHub Actions deployment, runtime configuration,
immutable image rollback, backup/restore, operational status, logs, and health checks. The existing
`compose.yaml` remains the local development configuration.

## Requirements

- Python 3.12 or newer

## Docker deployment and persistent scheduler operation

The Docker image runs the existing `NewsPipelineScheduler` as the foreground PID 1 process. Docker
Compose provides persistent named volumes for SQLite/state and generated outputs, a local-only
healthcheck, and `restart: unless-stopped`. It does not add cron or another scheduler.

Prerequisites are Docker and the Docker Compose plugin. Create local configuration first:

```bash
cp .env.example .env
```

Set `SCHEDULER_FEED_URLS` to a comma-separated list and set
`SCHEDULER_RELEVANCE_TARGET`. `SCHEDULER_PROVIDER=local` is deterministic and needs neither an API
key nor network access. Set it to `openai` only when `OPENAI_API_KEY` is configured, or to
`anthropic` only when `ANTHROPIC_API_KEY` is configured; real API calls may incur charges. Never
commit `.env` or an API key.

Start and inspect the service:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f scheduler
```

`docker compose ps` reports the health status. The healthcheck verifies that the scheduler PID
marker is live, the state/output directories are writable, and the configured database accepts a
local query. It never contacts RSS feeds, OpenAI, or another paid service.

Common operations:

```bash
docker compose restart scheduler
docker compose down
# After pulling code changes:
docker compose up -d --build
```

Normal `docker compose down` keeps the `app_data` and `generated_outputs` named volumes. Running
`docker compose down -v` deletes both volumes and their data. Stop the scheduler before a backup or
restore, and back up both volumes so the SQLite database/state, scene images, and `manifest.json`
remain consistent. `docker volume ls` can be used to identify their Compose-prefixed names.
For a persistence smoke check, create a harmless marker in each mounted path, restart the scheduler,
then perform `docker compose down` followed by `docker compose up -d`; both markers should remain.

The container paths and configuration precedence are:

- `DATABASE_URL` explicitly selects the database. If omitted, the runtime derives a SQLite URL
  from `APP_DATA_DIR` (`/data/state` in Compose).
- `OUTPUT_DIR` is `/data/outputs` in Compose. It is the persistent root available to callers of the
  existing image-generation service; that service still receives its output directory explicitly.
- `SCHEDULER_INTERVAL_SECONDS` controls the interval cadence. `TZ` defaults to `Asia/Tokyo` in the
  Compose example; interval scheduling is elapsed-time based rather than a wall-clock cron time.
- `OPENAI_MODEL` and `OPENAI_IMAGE_MODEL` retain the existing provider model overrides.
  `ANTHROPIC_MODEL` is the equivalent override for the Claude text provider.

The deployment assumes exactly one scheduler container. **Do not scale `scheduler` above one
replica:** there is no distributed lock, so multiple replicas may execute duplicate jobs.

An existing one-shot database initialization command can also run inside the image:

```bash
docker compose run --rm scheduler python -c "from app.database import init_db; init_db()"
```

The runtime handles SIGTERM/SIGINT, stops the existing scheduler gracefully, and logs to
stdout/stderr. Invalid configuration exits non-zero so the restart policy can respond. No source
tree, Docker socket, privileged mode, or host network is mounted.

### End-to-end production pipeline

Set `PIPELINE_MODE=end_to_end` to make the same scheduled runner perform the complete production
flow: RSS collection and persistence → news summarization/scoring/ranking → priority selection →
YouTube ideas → Potential scoring/ranking → title/thumbnail packaging evaluation → outline and
15-minute script → さび助×ハル dialogue → 16:9 visual plan → scene image generation. The
provider-independent orchestrator reuses the existing services and fails before YouTube providers
when no priority news is available. `PIPELINE_MODE=news` preserves the earlier news-only scheduler.

Required and bounded settings are documented in `.env.example`:

```dotenv
PIPELINE_MODE=end_to_end
PIPELINE_NEWS_LIMIT=10
YOUTUBE_CHANNEL_FOCUS=AI industry explanations
YOUTUBE_IDEA_COUNT=3
YOUTUBE_PACKAGING_COUNT=5
YOUTUBE_TARGET_MINUTES=15
YOUTUBE_SCENE_LIMIT=50
YOUTUBE_IMAGE_SIZE=1792x1024
```

`PIPELINE_NEWS_LIMIT` is a hard per-run provider-call guard as well as the final priority-news
selection limit. At most that many articles are sent through summarization and scoring in one run;
additional unprocessed articles remain stored for later runs.

`SCHEDULER_PROVIDER=local` follows the entire route deterministically, needs no API key, and writes
valid placeholder PNGs. `SCHEDULER_PROVIDER=openai` connects every existing OpenAI text adapter and
the existing Images API adapter. It requires `OPENAI_API_KEY`, respects `OPENAI_MODEL` and
`OPENAI_IMAGE_MODEL`, and generates production scene images. Text calls and every selected scene
image may incur charges. `YOUTUBE_SCENE_LIMIT` is a hard pre-call cost guard; an oversized visual
plan fails before any image request. Never commit the key or populated `.env`.

`SCHEDULER_PROVIDER=anthropic` connects every text stage (summarization, scoring, YouTube ideas,
Potential scoring, packaging, outline/script, さび助×ハル dialogue, and 16:9 visual planning) to
Claude through the Anthropic Messages API instead. It requires `ANTHROPIC_API_KEY` and respects
`ANTHROPIC_MODEL`. Anthropic has no Images API equivalent, so scene image generation still uses the
deterministic local generator (valid placeholder PNGs) even in this mode; only `openai` produces
real generated scene images today. Text calls to Claude may incur charges. Never commit the key or
a populated `.env`.

Run the configured composition once without starting the interval scheduler:

```bash
docker compose run --rm scheduler python -m app.runtime run-once
```

The command exits zero only after all selected images, `manifest.json`, and `run.json` have been
written. A provider, validation, empty-priority-news, or image failure exits non-zero. Scheduled
execution uses the exact same runner; inspect failures with `docker compose logs -f scheduler`.

Every successful run receives a collision-resistant UTC directory under the persistent
`generated_outputs` volume:

```text
/data/outputs/<run-id>/
├── scene_000.png
├── scene_001.png
├── ...
├── manifest.json
└── run.json
```

`manifest.json` is created only after all selected images succeed. `run.json` atomically records
the news counts and priority items, source article IDs, selected idea/Potential/packaging, script,
dialogue, visual plan, image/file metadata, and safe provider/model names. It never stores API keys,
authorization headers, or an environment dump. Partial image failure retains the existing retry and
partial-file behavior but produces no successful manifest or `run.json`; completed run directories
are never overwritten.

Database state and run outputs remain in the existing named volumes. The scheduler remains one
foreground PID 1 service with overlap protection and graceful shutdown. Do not scale it beyond one
replica: no distributed lock is provided, and duplicate paid pipeline runs could occur.

## Local setup

Create and activate a virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Install the application and development dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

## Run the API

Start the development server:

```bash
uvicorn app.main:app --reload
```

The health endpoint is available at <http://127.0.0.1:8000/health> and returns:

```json
{"status": "ok"}
```

## Web dashboard

The read-only dashboard displays completed YouTube production runs without changing or regenerating
their artifacts. Docker Compose starts it alongside the scheduler and mounts generated outputs
read-only:

```bash
docker compose up -d --build
```

Open <http://localhost:8000/dashboard>. The read-only JSON endpoints are
`GET /api/runs` for newest-first run summaries and `GET /api/runs/{run_id}` for complete run
metadata.

To protect the dashboard, its JSON APIs, images, audio, and API documentation with browser-native
HTTP Basic authentication, set all three values below. `/health` intentionally remains public for
container and uptime checks. Production Compose enables `DASHBOARD_AUTH_REQUIRED` by default and
returns `503` until both credentials are supplied.

```dotenv
DASHBOARD_AUTH_REQUIRED=true
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=use-a-long-unique-password
```

Credentials are protected in transit by HTTPS in the production Caddy configuration. Do not use
Basic authentication over plain HTTP outside local development.

For local development, select any output directory and start the existing FastAPI application:

```bash
OUTPUT_DIR=./outputs uvicorn app.main:app --reload
```

Missing, empty, incomplete, and malformed run directories are handled safely. The dashboard never
requires an OpenAI API key or makes provider calls.

## Database

Local development uses SQLite through SQLAlchemy 2.x. By default, the application connects to
`ai_news.db` in the project root. Local SQLite database files are ignored by Git.

Set `DATABASE_URL` to use a different database location:

```bash
export DATABASE_URL="sqlite:///./local-news.db"
```

Create the database tables from the project root:

```bash
python -c "from app.database import init_db; init_db()"
```

Tests create isolated SQLite databases in pytest-managed temporary directories and do not modify
the local development database.

## RSS collector

The RSS collector accepts one or more RSS or Atom feed URLs, normalizes valid entries, and stores
new articles in the configured database. Initialize the database first, then call the collector:

```bash
python - <<'PY'
from app.database import init_db
from app.rss import collect_feeds

init_db()
result = collect_feeds([
    "https://example.com/feed.xml",
    "https://example.org/atom.xml",
])
print(result)
PY
```

Entries without a title, URL, or usable source are skipped. The entry's source title is used when
available; otherwise the feed title is used. Duplicate URLs and unavailable or malformed feeds do
not stop the remaining entries and feeds from being processed.

## Summarization service

The provider-independent summarization service accepts an existing article, explicit article text,
an injected summarizer, and a database session. `LocalSummarizer` is a deterministic development
implementation that normalizes whitespace and returns a fixed-length prefix; it does not use an
API key or network access.

```python
from sqlalchemy.orm import Session

from app.database import engine
from app.models import NewsArticle
from app.summarization import LocalSummarizer, summarize_article

with Session(engine) as session:
    article = session.get(NewsArticle, 1)
    if article is not None:
        result = summarize_article(
            article,
            "Explicit article text to summarize.",
            LocalSummarizer(),
            session,
        )
        print(result.summary)
```

Empty or whitespace-only input raises `EmptySummaryInputError` without changing the article.
Provider failures are rolled back and re-raised. Article titles are not used as an implicit text
fallback.

### OpenAI-backed summarizer

`OpenAISummarizer` implements the same provider-independent interface using the official OpenAI
Python SDK and Responses API. The SDK reads `OPENAI_API_KEY` from the environment. Select a model
with `OPENAI_MODEL` or the constructor's `model` argument:

```bash
export OPENAI_API_KEY="<your-key>"
export OPENAI_MODEL="gpt-5.5"
```

Never commit an API key or a populated `.env` file. The tracked `.env.example` contains only empty
or non-secret placeholders.

```python
from openai import OpenAI
from sqlalchemy.orm import Session

from app.database import engine
from app.models import NewsArticle
from app.openai_summarizer import OpenAISummarizer
from app.summarization import summarize_article

client = OpenAI(timeout=30.0)
summarizer = OpenAISummarizer(client=client, model="gpt-5.5")

with Session(engine) as session:
    article = session.get(NewsArticle, 1)
    if article is not None:
        result = summarize_article(
            article,
            "Explicit article text to summarize.",
            summarizer,
            session,
        )
        print(result.summary)
```

Only real API calls incur OpenAI API charges. Automated tests inject fake clients, require no API
key, and make no network requests.

### Claude-backed summarizer

`AnthropicSummarizer` implements the same provider-independent interface using the official
`anthropic` Python SDK and Messages API. The SDK reads `ANTHROPIC_API_KEY` from the environment.
Select a model with `ANTHROPIC_MODEL` or the constructor's `model` argument:

```bash
export ANTHROPIC_API_KEY="<your-key>"
export ANTHROPIC_MODEL="claude-sonnet-4-5"
```

Never commit an API key or a populated `.env` file.

```python
from anthropic import Anthropic
from sqlalchemy.orm import Session

from app.anthropic_summarizer import AnthropicSummarizer
from app.database import engine
from app.models import NewsArticle
from app.summarization import summarize_article

client = Anthropic(timeout=30.0)
summarizer = AnthropicSummarizer(client=client, model="claude-sonnet-4-5")

with Session(engine) as session:
    article = session.get(NewsArticle, 1)
    if article is not None:
        result = summarize_article(
            article,
            "Explicit article text to summarize.",
            summarizer,
            session,
        )
        print(result.summary)
```

Only real API calls incur Anthropic API charges. Automated tests inject fake clients, require no
API key, and make no network requests.

## Article scoring

The provider-independent scoring service evaluates explicit article text with two 0–100 scores:

- `importance_score` measures general news significance, impact, novelty, consequences, and urgency.
- `relevance_score` measures relevance to a required, caller-supplied `relevance_target`; no topic is
  hard-coded.

It also stores a brief `score_reason` and a UTC `scored_at` timestamp. Blank article text, a blank
target, scores outside 0–100, and empty reasons are rejected without changing the stored article.
`LocalScorer` is deterministic and requires neither an API key nor network access:

```python
from sqlalchemy.orm import Session

from app.database import engine
from app.models import NewsArticle
from app.scoring import LocalScorer, score_article

with Session(engine) as session:
    article = session.get(NewsArticle, 1)
    if article is not None:
        result = score_article(
            article,
            "Explicit article text to score.",
            "AI industry and model releases",
            LocalScorer(),
            session,
        )
        print(result.importance_score, result.relevance_score, result.reason)
```

### OpenAI-backed scorer

`OpenAIScorer` implements the same interface with the Responses API and structured output. It uses
the same `OPENAI_API_KEY` and `OPENAI_MODEL` environment variables described above, or accepts a
model and client directly:

```python
from openai import OpenAI
from sqlalchemy.orm import Session

from app.database import engine
from app.models import NewsArticle
from app.openai_scorer import OpenAIScorer
from app.scoring import score_article

client = OpenAI(timeout=30.0)
scorer = OpenAIScorer(client=client, model="gpt-5.5")

with Session(engine) as session:
    article = session.get(NewsArticle, 1)
    if article is not None:
        result = score_article(
            article,
            "Explicit article text to score.",
            "cybersecurity vulnerabilities and attacks",
            scorer,
            session,
        )
        print(result)
```

Real OpenAI API calls may incur charges. Never commit `OPENAI_API_KEY`, a populated `.env`, or
other credentials. Tests use injected fake clients and never contact the OpenAI API.

### Claude-backed scorer

`AnthropicScorer` implements the same interface with the Anthropic Messages API, forcing a single
structured tool call and validating its result. It uses the same `ANTHROPIC_API_KEY` and
`ANTHROPIC_MODEL` environment variables described above, or accepts a model and client directly:

```python
from anthropic import Anthropic
from sqlalchemy.orm import Session

from app.anthropic_scorer import AnthropicScorer
from app.database import engine
from app.models import NewsArticle
from app.scoring import score_article

client = Anthropic(timeout=30.0)
scorer = AnthropicScorer(client=client, model="claude-sonnet-4-5")

with Session(engine) as session:
    article = session.get(NewsArticle, 1)
    if article is not None:
        result = score_article(
            article,
            "Explicit article text to score.",
            "cybersecurity vulnerabilities and attacks",
            scorer,
            session,
        )
        print(result)
```

Real Anthropic API calls may incur charges. Never commit `ANTHROPIC_API_KEY`, a populated `.env`,
or other credentials. Tests use injected fake clients and never contact the Anthropic API.

## News ranking

News ranking uses already stored scores to calculate a deterministic derived value:

```text
priority_score = importance_score × 0.6 + relevance_score × 0.4
```

The default weights are 60% importance and 40% relevance. Both weights can be configured, but each
must be between 0 and 1 and together they must equal 1. Articles missing either stored score are
excluded. The priority score is calculated when requested and is not stored in the database.

```python
from sqlalchemy.orm import Session

from app.database import engine
from app.ranking import (
    get_rankable_articles,
    rank_articles,
    select_priority_articles,
)

with Session(engine) as session:
    articles = get_rankable_articles(session)

    all_ranked = rank_articles(articles)
    priority_articles = select_priority_articles(
        articles,
        limit=10,
        minimum_priority_score=80,
        max_per_source=2,
    )
```

`minimum_priority_score` filters out lower-priority results, while `max_per_source` optionally
limits how many selected articles can come from one source. Ranking makes no additional OpenAI API
calls, so it adds no OpenAI API usage charges.

## End-to-end pipeline

The reusable single-run pipeline orchestrates the existing services in this order:

```text
RSS collection → persistence → summarization → scoring → ranking → priority selection
```

Providers and a SQLAlchemy session are injected by the caller. The required `relevance_target` is
passed to the scorer and is never hard-coded. `MetadataTextProvider` offers a deterministic local
text strategy using the stored title and, when available, the existing summary. It does not fetch,
scrape, or claim to provide the full article body.

```python
from sqlalchemy.orm import Session

from app.database import engine
from app.pipeline import MetadataTextProvider, run_pipeline
from app.scoring import LocalScorer
from app.summarization import LocalSummarizer

with Session(engine) as session:
    result = run_pipeline(
        ["https://example.com/feed.xml"],
        "AI industry and model releases",
        LocalSummarizer(),
        LocalScorer(),
        MetadataTextProvider(),
        session,
        limit=10,
        minimum_priority_score=70,
        max_per_source=2,
        importance_weight=0.6,
        relevance_weight=0.4,
    )
    print(result.priority_articles)
```

By default, articles with an existing summary are not summarized again, and articles with both
scores are not rescored. Explicit `force_resummarize=True` and `force_rescore=True` options enable
reprocessing. One article-level provider failure is counted and does not stop other articles.

The pipeline is also compatible with injected `OpenAISummarizer` and `OpenAIScorer` instances.
Construct those providers outside `run_pipeline`; real OpenAI calls may incur API charges and need
`OPENAI_API_KEY`. The pipeline itself reads no secrets. This is an on-demand, single-run workflow;
it does not add scheduling, background workers, or web scraping.

## Scheduled news pipeline

`NewsPipelineScheduler` periodically invokes an injected, existing pipeline runner in-process. The
default interval is 3,600 seconds (60 minutes); pass `interval_seconds` to change it. Importing or
constructing the scheduler does not register work, start a thread, or run the pipeline.

```python
from app.database import SessionLocal
from app.pipeline import MetadataTextProvider
from app.scheduler import NewsPipelineScheduler, build_pipeline_runner
from app.scoring import LocalScorer
from app.summarization import LocalSummarizer

runner = build_pipeline_runner(
    SessionLocal,
    ["https://example.com/feed.xml"],
    "AI industry and model releases",
    LocalSummarizer(),
    LocalScorer(),
    MetadataTextProvider(),
)
scheduler = NewsPipelineScheduler(runner, interval_seconds=3600)

# Manual execution works without starting the schedule.
result = scheduler.run_once()

# Background scheduling is always explicit.
scheduler.start()
# On application shutdown:
scheduler.shutdown()
```

`build_pipeline_runner` creates and closes a fresh SQLAlchemy session for every run; it never
shares one long-lived session with the scheduler thread. The scheduler catches failed pipeline runs
so a later interval can try again. A non-blocking application lock prevents a manual and scheduled
run from overlapping, and repeated `start()` calls do not create duplicate jobs.

The overlap lock protects only one Python process. It is not a distributed lock for multiple
processes or containers. Summarizers, scorers, feeds, relevance targets, and text providers remain
caller-injected; scheduler code constructs no OpenAI client and reads no API key. Scheduled use of
real OpenAI providers can generate recurring API charges. Never commit API keys or populated
environment files.

## YouTube idea generation

YouTube idea generation converts existing ranking output into compact, provider-independent
context and structured video concepts:

```text
priority news → YouTubeIdeaSource → generator → YouTubeIdea
```

`YouTubeIdeaSource` contains only the matched article ID, title, optional summary, source,
publication time, stored importance/relevance scores, and the existing `RankingResult` priority
score. It does not recalculate ranking. `channel_focus` is a required caller-supplied topic, while
`idea_count` defaults to 3 and accepts positive integers up to 10.

```python
from app.youtube_ideas import (
    LocalYouTubeIdeaGenerator,
    build_youtube_idea_sources,
    generate_youtube_ideas,
)

sources = build_youtube_idea_sources(priority_articles, articles)
ideas = generate_youtube_ideas(
    sources,
    LocalYouTubeIdeaGenerator(),
    channel_focus="drone technology and regulation",
    idea_count=3,
)
```

The deterministic local generator needs no network or API key. Each `YouTubeIdea` includes source
article IDs, title, hook, editorial angle, target audience, estimated length, thumbnail text,
chapters, and SEO keywords.

For OpenAI-backed generation, construct and inject `OpenAIYouTubeIdeaGenerator` in the same way as
the other OpenAI providers:

```python
from openai import OpenAI

from app.openai_youtube_ideas import OpenAIYouTubeIdeaGenerator

generator = OpenAIYouTubeIdeaGenerator(client=OpenAI(), model="gpt-5.5")
```

The OpenAI provider uses Responses API typed structured output. Real calls may incur charges;
never commit `OPENAI_API_KEY`. Ideas are not persisted yet. This feature does not add YouTube
potential scoring, news clustering, script generation, image generation, or YouTube publishing.

For Claude-backed generation, construct and inject `AnthropicYouTubeIdeaGenerator` the same way:

```python
from anthropic import Anthropic

from app.anthropic_youtube_ideas import AnthropicYouTubeIdeaGenerator

generator = AnthropicYouTubeIdeaGenerator(client=Anthropic(), model="claude-sonnet-4-5")
```

The Claude provider forces a single structured tool call on the Anthropic Messages API. Real calls
may incur charges; never commit `ANTHROPIC_API_KEY`.

## YouTube Potential Score

YouTube Potential Score evaluates whether a generated `YouTubeIdea` is promising as a video
concept. It is independent from news importance, relevance, and priority scores and does not modify
or recalculate them. Providers return five 0–100 dimensions, while provider-independent core code
calculates the final score:

```text
youtube_potential_score =
    topic_appeal × 0.30
    + clarity × 0.20
    + surprise × 0.20
    + searchability × 0.15
    + visual_explainability × 0.15
```

The dimensions measure topic appeal, explanatory clarity, truthful surprise/hook strength,
searchability, and suitability for visual explanation. Weights are configurable, but each must be
finite and between 0 and 1, and together they must equal 1.

```python
from app.youtube_potential import (
    LocalYouTubePotentialScorer,
    rank_youtube_ideas,
    score_youtube_ideas,
    select_top_youtube_ideas,
)

potential = score_youtube_ideas(
    ideas,
    LocalYouTubePotentialScorer(),
    channel_focus="drone technology and regulation",
)
ranked = rank_youtube_ideas(ideas, potential)
top = select_top_youtube_ideas(
    ranked,
    limit=3,
    minimum_potential_score=80,
)
```

`LocalYouTubePotentialScorer` is deterministic and needs no network or API key. For OpenAI-backed
evaluation, inject `OpenAIYouTubePotentialScorer`; it uses Responses API typed structured output
for dimension scores only, while the final weighted score remains calculated in core code. Real
OpenAI calls may incur charges, and API keys must never be committed.

For Claude-backed evaluation, inject `AnthropicYouTubePotentialScorer` from
`app.anthropic_youtube_potential`; it forces a structured tool call for dimension scores only, and
the final weighted score is still calculated in core code. Real Anthropic calls may incur charges,
and `ANTHROPIC_API_KEY` must never be committed.

`searchability_score` is only a heuristic based on the supplied title, topic, and SEO keywords. It
is not measured YouTube or Google search volume, Google Trends data, CTR, audience size, or a view
prediction. Potential results are not persisted, and this feature adds no trends integration,
clustering, script generation, or database schema changes.

## Similar-news clustering

Similar-news clustering groups multiple outlets' reports of substantially the same underlying
event before downstream YouTube idea generation. It does not merge stories merely because they
share a broad topic: reports about the same DJI launch can be grouped, while a DJI launch and a
separate drone regulation story remain distinct.

`build_news_cluster_sources` combines existing `RankingResult` values with matching `NewsArticle`
records. It copies the existing priority score without recalculating it. `cluster_priority_news`
then validates that every input article appears in exactly one cluster and chooses each
representative deterministically by priority, importance, relevance, publication time, and article
ID.

```python
from app.news_clustering import (
    LocalNewsClusterer,
    build_news_cluster_sources,
    cluster_priority_news,
)

sources = build_news_cluster_sources(priority_articles, articles)
clusters = cluster_priority_news(
    sources,
    LocalNewsClusterer(),
    topic_focus="drone technology and regulation",
)
```

`LocalNewsClusterer` is a deterministic development heuristic that groups titles only when they
match after lowercase, whitespace, and basic punctuation normalization. It does not provide full
semantic equivalence detection. `OpenAINewsClusterer` can be injected for model-based same-event
grouping and uses Responses API typed structured output; real calls may incur charges.

The default maximum input is 50 articles to guard against accidental large provider calls. This
implementation performs no batching and does not persist clusters. It adds no embeddings, vector
database, semantic search infrastructure, or DB schema changes. Never commit `OPENAI_API_KEY` or a
populated environment file.

`AnthropicNewsClusterer` (`app.anthropic_news_clustering`) is the equivalent Claude-backed provider;
it forces a structured tool call on the Anthropic Messages API for the same grouping schema. Real
calls may incur charges; never commit `ANTHROPIC_API_KEY`.

## YouTube title and thumbnail packaging

This layer turns an existing top-ranked `YouTubeIdea` and its `YouTubePotentialResult` into
ranked title/thumbnail-copy options without recalculating the existing potential score:

```text
top YouTube idea → packaging source → title/thumbnail drafts → dimension evaluation
→ core packaging score → ranked candidates → top packaging options
```

Each candidate must have exactly one index from `0` through `candidate_count - 1` (the default is
5 and the maximum is 10). A normalized duplicate title/thumbnail pair is rejected. Providers only
generate copy or evaluate five 0–100 dimensions; provider-independent core code calculates:

```text
packaging_score =
    clarity × 0.20
    + curiosity × 0.25
    + specificity × 0.20
    + truthfulness × 0.25
    + thumbnail_synergy × 0.10
```

Weights may be configured, but each must be finite and between 0 and 1 and their sum must be 1.
Ties are resolved deterministically by truthfulness, curiosity, clarity, then candidate index.

```python
from app.youtube_packaging import (
    LocalYouTubePackagingEvaluator,
    LocalYouTubePackagingGenerator,
    build_youtube_packaging_source,
    generate_youtube_packaging,
    select_top_packaging_candidates,
)

packaging_source = build_youtube_packaging_source(top_ranked_idea)
ranked_candidates = generate_youtube_packaging(
    packaging_source,
    LocalYouTubePackagingGenerator(),
    LocalYouTubePackagingEvaluator(),
    channel_focus="drone technology and regulation",
)
top_options = select_top_packaging_candidates(ranked_candidates, limit=3)
```

The local providers are deterministic and require no network or API key. For OpenAI-backed use,
inject the typed Responses API providers:

```python
from openai import OpenAI

from app.openai_youtube_packaging import (
    OpenAIYouTubePackagingEvaluator,
    OpenAIYouTubePackagingGenerator,
)

client = OpenAI()
generator = OpenAIYouTubePackagingGenerator(client=client, model="gpt-5.5")
evaluator = OpenAIYouTubePackagingEvaluator(client=client, model="gpt-5.5")
```

The model can also be selected with `OPENAI_MODEL`. Real calls may incur API charges; never commit
`OPENAI_API_KEY` or a populated `.env`. The providers receive only supplied idea metadata and must
not claim analytics, CTR, views, search volume, trend data, or audience behavior. Scores are
editorial heuristics, not measured performance predictions. This feature does not persist output,
generate thumbnail images or scripts, upload to YouTube, fetch analytics/trends, or change the
database schema.

For Claude-backed use, inject the equivalent structured-tool-call providers:

```python
from anthropic import Anthropic

from app.anthropic_youtube_packaging import (
    AnthropicYouTubePackagingEvaluator,
    AnthropicYouTubePackagingGenerator,
)

client = Anthropic()
generator = AnthropicYouTubePackagingGenerator(client=client, model="claude-sonnet-4-5")
evaluator = AnthropicYouTubePackagingEvaluator(client=client, model="claude-sonnet-4-5")
```

The model can also be selected with `ANTHROPIC_MODEL`. Real calls may incur API charges; never
commit `ANTHROPIC_API_KEY` or a populated `.env`.

## 15-minute YouTube outline and script

The script layer turns an already selected idea and packaging option into a structured generic
news-analysis narration:

```text
selected idea → selected packaging → chapter outline → narration → complete script
```

`YouTubeScriptSource` copies the existing idea index, source article IDs, selected title and
thumbnail text, hook, angle, audience, chapter hints, SEO keywords, YouTube Potential Score, and
packaging score. Neither score is recalculated. An outline chapter contains its sequential index,
title, objective, estimated seconds, and key points. The final `YouTubeScript` combines a required
opening hook, exactly one narration section per chapter, a concise closing, and SEO keywords.

The default target is 15 minutes and may be configured from 5 to 30 minutes. Runtime is
approximate, not guaranteed: outline seconds must be within ±10% of the target, while text runtime
uses a simple heuristic of 280 Japanese non-space characters per minute or 150 English words per
minute with ±20% tolerance. Actual delivery speed, pauses, emphasis, and editing will change the
finished duration.

```python
from app.youtube_script import (
    LocalYouTubeOutlineGenerator,
    LocalYouTubeScriptGenerator,
    build_youtube_script_source,
    generate_youtube_script,
)

script_source = build_youtube_script_source(
    selected_ranked_idea,
    selected_packaging_candidate,
)
script = generate_youtube_script(
    script_source,
    LocalYouTubeOutlineGenerator(),
    LocalYouTubeScriptGenerator(),
    channel_focus="AI industry news",
    target_minutes=15,
)
```

The local providers are deterministic, offline, and API-key free. For OpenAI-backed generation,
inject separate typed Responses API providers:

```python
from openai import OpenAI

from app.openai_youtube_script import (
    OpenAIYouTubeOutlineGenerator,
    OpenAIYouTubeScriptGenerator,
)

client = OpenAI()
outline_generator = OpenAIYouTubeOutlineGenerator(client=client, model="gpt-5.5")
script_generator = OpenAIYouTubeScriptGenerator(client=client, model="gpt-5.5")
```

The model can also be selected with `OPENAI_MODEL`. Real calls may incur charges; never commit
`OPENAI_API_KEY` or a populated `.env`. Generation is grounded only in supplied idea/news metadata:
there is no automatic web research, article scraping, or permission to fabricate quotes, sources,
statistics, facts, or unsupported claims.

This issue produces generic narration only. It does not add さび助×ハル character dialogue,
image prompts or generation, TTS, subtitles, Premiere integration, script persistence, database
schema changes, YouTube descriptions, or upload/publishing.

For Claude-backed generation, inject the equivalent providers:

```python
from anthropic import Anthropic

from app.anthropic_youtube_script import (
    AnthropicYouTubeOutlineGenerator,
    AnthropicYouTubeScriptGenerator,
)

client = Anthropic()
outline_generator = AnthropicYouTubeOutlineGenerator(client=client, model="claude-sonnet-4-5")
script_generator = AnthropicYouTubeScriptGenerator(client=client, model="claude-sonnet-4-5")
```

The model can also be selected with `ANTHROPIC_MODEL`. Real calls may incur charges; never commit
`ANTHROPIC_API_KEY` or a populated `.env`.

## さび助×ハル dialogue conversion

Dialogue conversion is a transformation layer after the completed long-form script:

```text
English source structure → fact extraction and deduplication → reorganized 7–10 minute さび助×ハル dialogue
```

さび助 and ハル speak like close friends, using casual spoken Japanese rather than default
です／ます narration, formal news-anchor phrasing, or a teacher–student hierarchy. ハル asks short
viewer-perspective questions, reacts, and naturally leads into the next question. さび助 answers
briefly like a knowledgeable friend. Technical terminology remains available, with a short casual
explanation when it first appears. Polite language is not completely banned where it is natural,
such as an opening greeting or a direct address to viewers.

`YouTubeDialogueSource` keeps the source facts and structure as input context, but that structure is
not the output contract. Source chapter count does not equal Japanese chapter count. The converter
may merge, split, reorder, or omit repetitive source chapters, and output chapters use their own
sequential indexes. A 12-chapter source can therefore become a much clearer five-chapter Japanese
video. The Japanese edition targets 7–10 minutes and caps longer source targets at 10 minutes.
Only the two configured characters may speak, and both must appear.

```python
from app.youtube_dialogue import (
    LocalYouTubeDialogueConverter,
    convert_youtube_script_to_dialogue,
)

dialogue = convert_youtube_script_to_dialogue(
    completed_script,
    LocalYouTubeDialogueConverter(),
    channel_focus="AI industry news",
)
```

The local converter remains a deterministic offline fallback. Provider-backed Japanese rewriting
uses the independent 7–10 minute target. Runtime remains approximate and is checked with the
existing Japanese-character/English-word estimator and a default ±25% tolerance.

For OpenAI-backed conversion, inject the typed Responses API provider:

```python
from openai import OpenAI

from app.openai_youtube_dialogue import OpenAIYouTubeDialogueConverter

converter = OpenAIYouTubeDialogueConverter(client=OpenAI(), model="gpt-5.5")
```

The model can also be selected with `OPENAI_MODEL`. Real OpenAI calls may incur charges; never
commit `OPENAI_API_KEY` or a populated `.env`. Conversion uses only the supplied script: it is not
research, performs no web lookup, and must not invent facts, quotes, statistics, sources, laws,
dates, or outcomes or strengthen uncertainty into certainty.

This feature adds no image prompts or generation, TTS, subtitles/SRT, Premiere integration,
dialogue persistence or database changes, YouTube descriptions, upload, or publishing.

For Claude-backed conversion, inject the equivalent provider:

```python
from anthropic import Anthropic

from app.anthropic_youtube_dialogue import AnthropicYouTubeDialogueConverter

converter = AnthropicYouTubeDialogueConverter(client=Anthropic(), model="claude-sonnet-4-5")
```

The model can also be selected with `ANTHROPIC_MODEL`. Real Anthropic calls may incur charges;
never commit `ANTHROPIC_API_KEY` or a populated `.env`.

## 16:9 visual planning and image prompts

Visual planning converts the completed さび助×ハル dialogue into reusable text concepts and
image-generation prompts without generating any image:

```text
さび助×ハル dialogue → scene segmentation → visual concepts → image prompts
```

A scene represents a coherent visual beat, not one image per dialogue line. Adjacent lines may be
combined into one scene, while changes in topic, object, comparison, location, or explanatory
purpose may begin another. Every chapter must be represented, every scene traces back to exact
opening/chapter/closing line references, and scene chronology follows the source. Opening and
closing scenes are included by the local planner but are not required by core validation.

Supported visual types are `character_dialogue`, `realistic_scene`, `technical_explainer`,
`infographic`, `map`, `timeline`, `comparison`, `object_closeup`, `environment`, and `title_card`.
Every scene and prompt must explicitly use a horizontal `16:9` YouTube composition.

```python
from app.youtube_visuals import (
    LocalYouTubeVisualPlanner,
    generate_youtube_visual_plan,
)

visual_plan = generate_youtube_visual_plan(
    dialogue_script,
    LocalYouTubeVisualPlanner(),
    channel_focus="AI industry news",
)
```

An image prompt describes the grounded subject, environment, composition, camera/framing,
lighting, mood, accuracy constraints, horizontal 16:9 layout, and clean typography space where
appropriate. `overlay_text` is separate metadata for later graphics/Premiere work; prompts should
not ask an image model to render long Japanese text. The concise negative prompt excludes common
failures such as unreadable or garbled text, duplicate objects, malformed anatomy, watermarks, and
vertical composition.

For OpenAI-backed text planning, inject the typed Responses API provider:

```python
from openai import OpenAI

from app.openai_youtube_visuals import OpenAIYouTubeVisualPlanner

planner = OpenAIYouTubeVisualPlanner(client=OpenAI(), model="gpt-5.5")
```

The model can also be selected with `OPENAI_MODEL`. Real OpenAI text-generation calls may incur
charges; never commit `OPENAI_API_KEY` or a populated `.env`. Planning uses supplied dialogue only:
it performs no web research and must not invent facts, products, organizations, equipment,
locations, statistics, dates, quotes, or outcomes.

This feature creates text plans and prompts only. It does not call the OpenAI Images API or any
image/video service, create or download PNG/JPG/WEBP files, store canonical character assets,
generate TTS/subtitles, integrate with Premiere, persist plans, or upload to YouTube.

For Claude-backed text planning, inject the equivalent provider:

```python
from anthropic import Anthropic

from app.anthropic_youtube_visuals import AnthropicYouTubeVisualPlanner

planner = AnthropicYouTubeVisualPlanner(client=Anthropic(), model="claude-sonnet-4-5")
```

The model can also be selected with `ANTHROPIC_MODEL`. Real Anthropic text-generation calls may
incur charges; never commit `ANTHROPIC_API_KEY` or a populated `.env`.

## YouTube scene image generation

The execution layer turns a validated visual plan into actual scene image files and a deterministic
manifest:

```text
YouTubeVisualPlan → image requests → provider → validation → files → manifest.json
```

Provider-independent core code owns scene selection, prompt composition, validation, retries,
deterministic filenames, atomic filesystem writes, and manifest creation. A `SceneImageGenerator`
only receives a `YouTubeImageRequest` and returns image bytes plus provider/model/media/dimension
metadata; it never chooses paths or writes files.

```python
from pathlib import Path

from app.youtube_image_generation import (
    LocalSceneImageGenerator,
    generate_youtube_scene_images,
)

result = generate_youtube_scene_images(
    visual_plan,
    LocalSceneImageGenerator(),
    output_directory=Path("output"),
)
```

The deterministic offline generator creates valid solid PNG fixtures without network access or an
API key. A successful three-scene run produces:

```text
output/
├── scene_000.png
├── scene_001.png
├── scene_002.png
└── manifest.json
```

Use `scene_indexes=[3, 5]` to generate a subset; caller order is normalized back to visual-plan
order. The default `scene_limit=50` is a cost guard checked before provider calls. Existing image
files or `manifest.json` cause a preflight failure by default; replacement requires explicit
`overwrite=True`.

`max_retries=2` means up to three attempts, and only provider exceptions are retried. Invalid
payloads are rejected immediately. On partial failure, already completed scene files remain, the
failed scene index is included in `SceneImageGenerationError`, and no complete result or success
manifest is written.

Output uses deterministic `scene_NNN` filenames selected from the validated media type. Writes use
a same-directory temporary file and atomic replacement. PNG/JPEG/WEBP signatures, positive
dimensions, horizontal orientation, and approximate 16:9 ratio are checked. The default project
size is `1792x1024`; its ratio differs from exact 16:9 by about 0.028 and is accepted by the
documented `0.03` tolerance. Images are never resized, cropped, padded, or otherwise post-processed.

The manifest preserves scene indexes, visual types, source dialogue references, file names,
provider/model, media type, dimensions, final prompt, revised prompt, and overlay metadata.
`overlay_text` is not burned into generated images; prompt composition only requests clean space
for later typography.

For real OpenAI generation, inject the Images API provider:

```python
from openai import OpenAI

from app.openai_youtube_image_generation import OpenAISceneImageGenerator
from app.youtube_image_generation import generate_youtube_scene_images

result = generate_youtube_scene_images(
    visual_plan,
    OpenAISceneImageGenerator(client=OpenAI(), model="gpt-image-2"),
    output_directory=Path("output"),
)
```

The adapter uses the installed SDK's `client.images.generate` API with base64 PNG output and
supports the project size `1792x1024`. Real generation incurs API charges. Never commit
`OPENAI_API_KEY` or a populated `.env`; credentials are never written to manifests.

This feature performs no overlay rasterization, character-reference conditioning, OCR/vision QA,
resizing/cropping, Seedance/Veo generation, TTS, subtitles, Premiere integration, database
persistence, or YouTube upload.

Anthropic has no Images API equivalent, so there is no `AnthropicSceneImageGenerator`. When
`SCHEDULER_PROVIDER=anthropic`, every text stage above runs on Claude but scene image generation
still uses `LocalSceneImageGenerator` (deterministic placeholder PNGs). To get real generated scene
images alongside Claude-authored text, inject `OpenAISceneImageGenerator` directly into
`ProductionProviders.image_generator` instead of relying on the scheduler's built-in wiring.

## Run tests

```bash
pytest
```

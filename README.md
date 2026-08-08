# AI News Intelligence

Backend foundation for AI News Intelligence, built with Python and FastAPI.

## Requirements

- Python 3.12 or newer

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

`searchability_score` is only a heuristic based on the supplied title, topic, and SEO keywords. It
is not measured YouTube or Google search volume, Google Trends data, CTR, audience size, or a view
prediction. Potential results are not persisted, and this feature adds no trends integration,
clustering, script generation, or database schema changes.

## Run tests

```bash
pytest
```

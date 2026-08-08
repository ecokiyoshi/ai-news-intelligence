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
export OPENAI_MODEL="gpt-5.6"
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
summarizer = OpenAISummarizer(client=client, model="gpt-5.6")

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

## Run tests

```bash
pytest
```

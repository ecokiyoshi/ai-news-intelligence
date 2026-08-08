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

## Run tests

```bash
pytest
```

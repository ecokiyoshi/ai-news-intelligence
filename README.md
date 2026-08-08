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

## Run tests

```bash
pytest
```

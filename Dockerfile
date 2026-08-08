FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/state /data/outputs \
    && chown -R appuser:appuser /data

USER appuser

CMD ["python", "-m", "app.runtime", "run"]

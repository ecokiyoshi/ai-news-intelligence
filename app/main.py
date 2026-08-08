"""FastAPI application entry point."""

from fastapi import FastAPI

app = FastAPI(title="AI News Intelligence")


@app.get("/health")
def health() -> dict[str, str]:
    """Return the service health status."""

    return {"status": "ok"}

"""FastAPI application entry point."""

from fastapi import FastAPI

from app.dashboard import router as dashboard_router
from app.audio.api import router as audio_router

app = FastAPI(title="AI News Intelligence")
app.include_router(dashboard_router)
app.include_router(audio_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Return the service health status."""

    return {"status": "ok"}

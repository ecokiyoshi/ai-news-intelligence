"""FastAPI application entry point."""

from fastapi import FastAPI

from app.dashboard import router as dashboard_router

app = FastAPI(title="AI News Intelligence")
app.include_router(dashboard_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Return the service health status."""

    return {"status": "ok"}

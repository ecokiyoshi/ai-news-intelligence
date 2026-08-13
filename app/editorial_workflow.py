"""Backward-compatible editorial review metadata for production runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

EDITORIAL_SCHEMA = "ai-news-intelligence/editorial-workflow"
EDITORIAL_SCHEMA_VERSION = 1
REVIEW_STATUS = "in_review"
LEGACY_STATUS = "completed"


def new_review_metadata(created_at: datetime) -> dict[str, Any]:
    return {
        "schema": EDITORIAL_SCHEMA,
        "schema_version": EDITORIAL_SCHEMA_VERSION,
        "status": REVIEW_STATUS,
        "revision": 1,
        "generated_at": created_at.isoformat(),
        "updated_at": created_at.isoformat(),
        "approved_at": None,
    }


def editorial_status(run: dict[str, Any]) -> str:
    """Treat artifacts created before this feature as completed."""

    editorial = run.get("editorial")
    if not isinstance(editorial, dict):
        return LEGACY_STATUS
    status = editorial.get("status")
    return status if isinstance(status, str) and status else LEGACY_STATUS

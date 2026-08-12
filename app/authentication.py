"""HTTP Basic authentication for the dashboard and its backing APIs."""

from __future__ import annotations

import os
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


PUBLIC_PATHS = frozenset({"/health"})


def _configured_credentials() -> tuple[str | None, str | None]:
    return os.environ.get("DASHBOARD_USERNAME"), os.environ.get("DASHBOARD_PASSWORD")


def _authentication_required() -> bool:
    value = os.environ.get("DASHBOARD_AUTH_REQUIRED", "false")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {"detail": "Authentication required"},
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="AI News Intelligence", charset="UTF-8"'},
    )


class DashboardAuthenticationMiddleware(BaseHTTPMiddleware):
    """Protect all application routes except the liveness endpoint."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        username, password = _configured_credentials()
        if not username and not password and not _authentication_required():
            return await call_next(request)
        if not username or not password:
            return JSONResponse(
                {"detail": "Dashboard authentication is not configured"},
                status_code=503,
            )

        credentials = request.headers.get("Authorization", "")
        try:
            scheme, encoded = credentials.split(" ", 1)
            if scheme.lower() != "basic":
                return _unauthorized()
            import base64

            supplied = base64.b64decode(encoded, validate=True).decode("utf-8")
            supplied_username, supplied_password = supplied.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return _unauthorized()

        username_matches = secrets.compare_digest(
            supplied_username.encode("utf-8"), username.encode("utf-8")
        )
        password_matches = secrets.compare_digest(
            supplied_password.encode("utf-8"), password.encode("utf-8")
        )
        if not (username_matches and password_matches):
            return _unauthorized()

        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "private, no-store")
        return response

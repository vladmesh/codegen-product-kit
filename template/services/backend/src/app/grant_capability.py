"""Protect the generated user-grant capability from public callers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from secrets import compare_digest

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

GRANT_CAPABILITY_HEADER = "X-Grant-Capability"
GRANT_PATH = "/users/grant"


class GrantCapabilityMiddleware(BaseHTTPMiddleware):
    """Require one exact capability header before grant request processing."""

    def __init__(self, app: ASGIApp, *, capability: str) -> None:
        super().__init__(app)
        self._capability = capability

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method != "POST" or request.url.path != GRANT_PATH:
            return await call_next(request)

        presented = request.headers.getlist(GRANT_CAPABILITY_HEADER)
        if (
            len(presented) != 1
            or not presented[0]
            or not presented[0].isascii()
            or not compare_digest(presented[0], self._capability)
        ):
            return JSONResponse(status_code=403, content={"detail": "Grant capability required"})

        return await call_next(request)

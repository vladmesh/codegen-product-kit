"""Thin tg-reader client; no response diagnostics or credentials escape this boundary."""

from datetime import datetime
from http import HTTPStatus
import os
from typing import TypeVar

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from codegen_kit_tg_channels.models import PostPage, ResolvedChannel

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
PLATFORM_ENVIRONMENT = ("PLATFORM_BASE_URL", "PLATFORM_KEY")


def missing_environment() -> list[str]:
    """Names, never values, of the unset platform variables."""
    return [name for name in PLATFORM_ENVIRONMENT if not os.getenv(name)]


class ServiceError(Exception):
    """Only a classified status and bounded retry delay cross the boundary."""

    def __init__(self, status: int, retry_after: int = 60) -> None:
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"channel service unavailable (status {status})")


class ReaderClient:
    def __init__(
        self, base_url: str, key: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._key = SecretStr(key)
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            transport=transport,
            timeout=15,
            follow_redirects=False,
            trust_env=False,
        )

    @classmethod
    def from_environment(cls) -> "ReaderClient":
        missing = missing_environment()
        if missing:
            raise RuntimeError(
                f"{missing[0]} is not set; please add it to your environment variables"
            )
        return cls(os.environ["PLATFORM_BASE_URL"], os.environ["PLATFORM_KEY"])

    async def close(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, model: type[ResponseModel], **params: str) -> ResponseModel:
        # Do not keep httpx exceptions as causes: those contain the request and its headers.
        failure = ServiceError(503)
        try:
            response = await self._http.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
            )
            if response.status_code != HTTPStatus.OK:
                retry = response.headers.get("Retry-After", "60")
                delay = min(86400, max(1, int(retry))) if retry.isdecimal() else 60
                failure = ServiceError(response.status_code, delay)
            else:
                return model.model_validate(response.json())
        except (httpx.HTTPError, ValidationError, ValueError):
            failure = ServiceError(503)
        # Raise outside the except block so __context__ contains no original diagnostic.
        raise failure

    async def resolve(self, channel: str) -> ResolvedChannel:
        return await self._get(f"v1/channels/{channel}", ResolvedChannel)

    async def posts(
        self, channels: list[str], *, since: datetime, cursor: str | None = None
    ) -> PostPage:
        params = {"channels": ",".join(sorted(set(channels))), "limit": "200"}
        if cursor is None:
            params["since"] = since.isoformat()
        else:
            params["cursor"] = cursor
        return await self._get("v1/posts", PostPage, **params)

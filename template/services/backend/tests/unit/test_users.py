"""Integration tests for the generated user access capability."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock

from fastapi import status
from httpx import AsyncClient
import pytest

import shared.generated.events as events_module


async def _grant(
    client: AsyncClient, channel: str = "telegram", external_id: str = "111"
) -> dict[str, Any]:
    response = await client.post(
        "/users/grant", json={"channel": channel, "external_id": external_id}
    )
    assert response.status_code == status.HTTP_200_OK
    return cast(dict[str, Any], response.json())


@pytest.mark.asyncio
async def test_grant_is_idempotent_and_reactivates_the_identity(client: AsyncClient) -> None:
    first = await _grant(client)

    inactive = await client.patch(f"/users/{first['user_id']}/status", json={"status": "inactive"})
    assert inactive.status_code == status.HTTP_200_OK
    assert inactive.json()["status"] == "inactive"

    second = await _grant(client)
    assert second == first | {"status": "active"}

    resolved = await client.get(
        "/users/access", params={"channel": "telegram", "external_id": "111"}
    )
    assert resolved.status_code == status.HTTP_200_OK
    assert resolved.json() == second

    listed = await client.get("/users")
    assert listed.status_code == status.HTTP_200_OK
    assert len(listed.json()) == 1


@pytest.mark.asyncio
async def test_resolve_reports_inactive_status_without_admitting_it(client: AsyncClient) -> None:
    granted = await _grant(client)
    response = await client.patch(
        f"/users/{granted['user_id']}/status", json={"status": "inactive"}
    )
    assert response.status_code == status.HTTP_200_OK

    resolved = await client.get(
        "/users/access", params={"channel": "telegram", "external_id": "111"}
    )
    assert resolved.status_code == status.HTTP_200_OK
    assert resolved.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_unknown_identity_is_not_resolved(client: AsyncClient) -> None:
    response = await client.get(
        "/users/access", params={"channel": "telegram", "external_id": "unknown"}
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_grant_validates_channel_identity(client: AsyncClient) -> None:
    response = await client.post("/users/grant", json={"channel": "", "external_id": ""})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_grant_publishes_user_granted(client: AsyncClient) -> None:
    granted = await _grant(client, external_id="222")

    publish = cast(AsyncMock, events_module.get_broker().publish)
    publish.assert_awaited_once()
    await_args = publish.await_args
    assert await_args is not None
    event, channel = await_args.args
    assert channel == "user_granted"
    assert event.user_id == granted["user_id"]
    assert event.status == "active"


@pytest.mark.asyncio
async def test_delete_user_removes_its_channel_identity(client: AsyncClient) -> None:
    granted = await _grant(client)
    deleted = await client.delete(f"/users/{granted['user_id']}")
    assert deleted.status_code == status.HTTP_204_NO_CONTENT

    missing = await client.get(
        "/users/access", params={"channel": "telegram", "external_id": "111"}
    )
    assert missing.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_db_isolation_after_all_tests(client: AsyncClient) -> None:
    response = await client.get("/users")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []

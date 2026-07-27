"""Integration tests for user CRUD endpoints."""

from __future__ import annotations

from typing import Any, cast

from fastapi import status
from httpx import AsyncClient
import pytest

import shared.generated.events as events_module


async def _create_user(client: AsyncClient, telegram_id: int = 111) -> dict[str, Any]:
    response = await client.post(
        "/users",
        json={"telegram_id": telegram_id},
    )
    assert response.status_code == status.HTTP_201_CREATED
    return cast(dict[str, Any], response.json())


@pytest.mark.asyncio
async def test_list_users(client: AsyncClient) -> None:
    empty = await client.get("/users")
    assert empty.status_code == status.HTTP_200_OK
    assert empty.json() == []

    await _create_user(client, telegram_id=100)
    await _create_user(client, telegram_id=200)

    response = await client.get("/users")
    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()) == 2  # noqa: PLR2004


@pytest.mark.asyncio
async def test_create_and_get_user(client: AsyncClient) -> None:
    created = await _create_user(client, telegram_id=123456789)

    fetched = await client.get(f"/users/{created['id']}")
    assert fetched.status_code == status.HTTP_200_OK
    assert fetched.json()["telegram_id"] == 123456789  # noqa: PLR2004


@pytest.mark.asyncio
async def test_create_user_publishes_user_registered(client: AsyncClient) -> None:
    await _create_user(client, telegram_id=123456789)

    broker = events_module.get_broker()
    broker.publish.assert_awaited_once()
    event, channel = broker.publish.await_args.args
    assert channel == "user_registered"
    assert event.telegram_id == 123456789  # noqa: PLR2004


@pytest.mark.asyncio
async def test_update_user(client: AsyncClient) -> None:
    created = await _create_user(client, telegram_id=1111)
    update_payload = {"telegram_id": 987654321}

    updated = await client.put(f"/users/{created['id']}", json=update_payload)
    assert updated.status_code == status.HTTP_200_OK
    body = updated.json()
    assert body["telegram_id"] == 987654321  # noqa: PLR2004
    assert body["is_admin"] is False


@pytest.mark.asyncio
async def test_create_user_rejects_is_admin(client: AsyncClient) -> None:
    """The privilege flag is not an input: the backend is reachable from outside."""
    response = await client.post("/users", json={"telegram_id": 3333, "is_admin": True})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    listed = await client.get("/users")
    assert listed.json() == []


@pytest.mark.asyncio
async def test_update_user_rejects_is_admin(client: AsyncClient) -> None:
    created = await _create_user(client, telegram_id=4444)

    updated = await client.put(f"/users/{created['id']}", json={"is_admin": True})

    assert updated.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    unchanged = await client.get(f"/users/{created['id']}")
    assert unchanged.json()["is_admin"] is False


@pytest.mark.asyncio
async def test_delete_user(client: AsyncClient) -> None:
    created = await _create_user(client, telegram_id=2222)

    deleted = await client.delete(f"/users/{created['id']}")
    assert deleted.status_code == status.HTTP_204_NO_CONTENT

    missing = await client.get(f"/users/{created['id']}")
    assert missing.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_create_user_rejects_duplicate_telegram_id(client: AsyncClient) -> None:
    await _create_user(client, telegram_id=42)

    duplicate = await client.post("/users", json={"telegram_id": 42})

    assert duplicate.status_code == status.HTTP_409_CONFLICT
    assert duplicate.json()["detail"] == "Telegram user already exists"


@pytest.mark.asyncio
async def test_update_user_requires_payload(client: AsyncClient) -> None:
    created = await _create_user(client, telegram_id=77)

    response = await client.put(f"/users/{created['id']}", json={})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "No changes supplied"


@pytest.mark.asyncio
async def test_db_isolation_after_all_tests(client: AsyncClient) -> None:
    """Verify that previous tests' data was rolled back.

    This test MUST be last in the file. Every test above creates users,
    but the transactional fixture should roll them all back.
    If this fails, test isolation is broken.
    """
    response = await client.get("/users")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == [], "Data leaked from previous tests — isolation is broken"

"""Contract tests for the manifest-backed core jobs contract."""

from __future__ import annotations

from collections.abc import Generator
import json
from typing import Any, cast

from fastapi import FastAPI, status
from httpx import AsyncClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.backend.src.app.models.job_command import JobCommand
from services.backend.src.app.repositories.job_command import JobCommandRepository
from services.backend.src.controllers import jobs as jobs_controller
from services.backend.src.core.settings import get_settings
from services.backend.src.generated.jobs_schemas import JOB_SCHEMAS

JOBS_CAPABILITY_HEADER = "X-Jobs-Capability"
TWO_PRODUCTS = 2
FRIDAY_DIGEST = {
    "type": "object",
    "properties": {"week": {"type": "integer", "minimum": 1}},
    "additionalProperties": False,
}


@pytest.fixture(autouse=True)
def declared_jobs() -> Generator[None, None, None]:
    """Give the generated core one representative manifest declaration."""
    JOB_SCHEMAS.clear()
    JOB_SCHEMAS.update({"friday_digest": FRIDAY_DIGEST})
    yield
    JOB_SCHEMAS.clear()


@pytest.fixture()
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every ``job_fired`` the core emits, without a live broker."""
    recorded: list[Any] = []

    async def _publish(message: Any) -> None:
        recorded.append(message)

    monkeypatch.setattr(jobs_controller, "publish_job_fired", _publish)
    return recorded


def _headers(capability: str | None = None) -> dict[str, str]:
    return {JOBS_CAPABILITY_HEADER: capability or get_settings().jobs_fire_capability}


def _fire_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "command_id": "command-1",
        "name": "friday_digest",
        "arguments": {"week": 36},
        "fired_by_product": "product-a",
        "fired_by_run": "run-1",
    }
    payload.update(overrides)
    return payload


async def _fire(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await client.post("/jobs/fire", headers=_headers(), json=_fire_payload(**overrides))
    assert response.status_code == status.HTTP_200_OK, response.text
    return cast(dict[str, Any], response.json())


async def _commands(session: AsyncSession) -> list[JobCommand]:
    return list((await session.execute(select(JobCommand))).scalars().all())


@pytest.mark.asyncio
async def test_fire_rejects_missing_duplicate_and_invalid_capabilities_before_recording(
    client: AsyncClient, db_session: AsyncSession, emitted: list[Any]
) -> None:
    payload = _fire_payload()

    missing = await client.post("/jobs/fire", json=payload)
    wrong = await client.post("/jobs/fire", headers=_headers("wrong"), json=payload)
    duplicate = await client.post(
        "/jobs/fire",
        headers=[(JOBS_CAPABILITY_HEADER, _headers()[JOBS_CAPABILITY_HEADER])] * 2,
        json=payload,
    )
    short = await client.post("/jobs/fire", headers=_headers("t"), json=payload)
    non_ascii = await client.post(
        "/jobs/fire", headers=[(JOBS_CAPABILITY_HEADER.encode(), b"\xff")], json=payload
    )

    assert [
        response.status_code for response in (missing, wrong, duplicate, short, non_ascii)
    ] == [status.HTTP_403_FORBIDDEN] * 5
    assert all(
        get_settings().jobs_fire_capability not in response.text
        for response in (missing, wrong, duplicate, short, non_ascii)
    )
    assert await _commands(db_session) == []
    assert emitted == []


@pytest.mark.asyncio
async def test_reading_evidence_back_carries_no_capability(
    client: AsyncClient, emitted: list[Any]
) -> None:
    fired = await _fire(client)

    read = await client.post(
        "/jobs/evidence", json={"command_id": "command-1", "fired_by_product": "product-a"}
    )

    assert read.status_code == status.HTTP_200_OK
    assert read.json() == fired
    assert read.json()["dispatch_status"] == "dispatched"
    assert read.json()["dispatched_at"] is not None
    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_undeclared_name_and_undeclared_argument_are_refused(
    client: AsyncClient, db_session: AsyncSession, emitted: list[Any]
) -> None:
    undeclared = await client.post(
        "/jobs/fire", headers=_headers(), json=_fire_payload(name="monday_digest")
    )
    invalid = await client.post(
        "/jobs/fire", headers=_headers(), json=_fire_payload(arguments={"weeks": 36})
    )

    assert undeclared.status_code == status.HTTP_404_NOT_FOUND
    assert undeclared.json()["detail"] == "Job name not declared"
    assert invalid.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert invalid.json()["detail"] == "Job arguments do not satisfy the declared schema"
    assert await _commands(db_session) == []
    assert emitted == []


@pytest.mark.asyncio
async def test_replaying_one_command_identity_never_executes_twice(
    client: AsyncClient, db_session: AsyncSession, emitted: list[Any]
) -> None:
    first = await _fire(client)
    second = await _fire(client)
    third = await _fire(client, fired_by_run="run-2", arguments={"week": 99})

    assert first == second == third
    assert len(emitted) == 1
    assert len(await _commands(db_session)) == 1


@pytest.mark.asyncio
async def test_a_fire_that_loses_the_identity_race_returns_the_recorded_command(
    client: AsyncClient,
    db_session: AsyncSession,
    emitted: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent fire sees no row, loses the unique constraint, and emits nothing."""
    recorded = await _fire(client)
    original_get = JobCommandRepository.get
    seen: list[str] = []

    async def _racing_get(self: JobCommandRepository, product: str, command_id: str) -> Any:
        if not seen:
            seen.append(command_id)
            return None
        return await original_get(self, product, command_id)

    monkeypatch.setattr(JobCommandRepository, "get", _racing_get)

    raced = await _fire(client)

    assert raced == recorded
    assert len(emitted) == 1
    assert len(await _commands(db_session)) == 1


@pytest.mark.asyncio
async def test_an_undelivered_command_is_retried_without_recording_a_second_command(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _failing_publish(message: Any) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(jobs_controller, "publish_job_fired", _failing_publish)
    undelivered = await _fire(client)

    delivered: list[Any] = []

    async def _publish(message: Any) -> None:
        delivered.append(message)

    monkeypatch.setattr(jobs_controller, "publish_job_fired", _publish)
    retried = await _fire(client)
    replayed = await _fire(client)

    assert undelivered["dispatch_status"] == "undelivered"
    assert undelivered["dispatched_at"] is None
    assert retried["dispatch_status"] == "dispatched"
    assert replayed == retried
    assert len(delivered) == 1
    assert len(await _commands(db_session)) == 1


@pytest.mark.asyncio
async def test_the_emitted_event_names_no_module_and_carries_no_capability(
    client: AsyncClient, emitted: list[Any]
) -> None:
    await _fire(client)

    (event,) = emitted
    payload = event.model_dump(mode="json")

    assert payload == {
        "contract_version": 1,
        "command_id": "command-1",
        "name": "friday_digest",
        "arguments": {"week": 36},
        "fired_by_product": "product-a",
        "fired_by_run": "run-1",
        "accepted_at": payload["accepted_at"],
    }
    assert get_settings().jobs_fire_capability not in json.dumps(payload)


@pytest.mark.asyncio
async def test_one_products_command_is_not_visible_or_fireable_as_another(
    client: AsyncClient, db_session: AsyncSession, emitted: list[Any]
) -> None:
    mine = await _fire(client)

    foreign_read = await client.post(
        "/jobs/evidence", json={"command_id": "command-1", "fired_by_product": "product-b"}
    )
    theirs = await _fire(client, fired_by_product="product-b", fired_by_run="run-9")

    assert foreign_read.status_code == status.HTTP_404_NOT_FOUND
    assert foreign_read.json()["detail"] == "Job command not found"
    assert theirs["fired_by_product"] == "product-b"
    assert theirs["fired_by_run"] == "run-9"
    assert mine["fired_by_run"] == "run-1"
    assert len(emitted) == TWO_PRODUCTS
    assert len(await _commands(db_session)) == TWO_PRODUCTS


def test_the_capability_is_absent_from_the_generated_openapi_document(app: FastAPI) -> None:
    document = json.dumps(app.openapi())

    assert "X-Jobs-Capability" not in document
    assert "JOBS_FIRE_CAPABILITY" not in document
    assert "/jobs/fire" in document
    assert "/jobs/evidence" in document

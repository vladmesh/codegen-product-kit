"""Implementation of the versioned, manifest-backed core jobs contract.

The core never schedules: it starts no timer and runs no loop. It accepts a fire of a
name the product declared, records the command under the caller's identity, and emits
``job_fired``. Whichever optional module declared that it provides ``jobs.fire``
subscribes to that event and does the work, so a caller never names a module, a queue,
a container or a transport.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from jsonschema import Draft202012Validator
from sqlalchemy.ext.asyncio import AsyncSession

from services.backend.src.app.models.job_command import DispatchStatus, JobCommand
from services.backend.src.app.repositories.job_command import JobCommandRepository
from services.backend.src.generated.jobs_schemas import JOB_SCHEMAS
from services.backend.src.generated.protocols import JobsControllerProtocol
from shared.generated.events import publish_job_fired
from shared.generated.schemas import (
    DispatchStatus as ContractDispatchStatus,
    JobCommand as JobCommandContract,
    JobCommandRef,
    JobFire,
    JobFired,
)

CORE_JOBS_CONTRACT_VERSION = 1


def _schema_for(name: str) -> dict[str, Any]:
    schema = JOB_SCHEMAS.get(name)
    if not isinstance(schema, dict):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job name not declared")
    return schema


def _validate_arguments(name: str, arguments: Any) -> None:
    if any(Draft202012Validator(_schema_for(name)).iter_errors(arguments)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Job arguments do not satisfy the declared schema",
        )


def _to_contract(command: JobCommand) -> JobCommandContract:
    return JobCommandContract(
        contract_version=CORE_JOBS_CONTRACT_VERSION,
        command_id=command.command_id,
        name=command.name,
        arguments=command.arguments,
        fired_by_product=command.fired_by_product,
        fired_by_run=command.fired_by_run,
        dispatch_status=ContractDispatchStatus(command.dispatch_status.value),
        accepted_at=command.accepted_at,
        dispatched_at=command.dispatched_at,
    )


class JobsController(JobsControllerProtocol):
    """Fire only declared behaviours, exactly once per command identity."""

    async def fire(self, session: AsyncSession, payload: JobFire) -> JobCommandContract:
        """Record a fire and emit ``job_fired`` at most once for this identity."""

        _validate_arguments(payload.name, payload.arguments)
        repository = JobCommandRepository(session)
        command, _created = await repository.record(
            command_id=payload.command_id,
            name=payload.name,
            arguments=payload.arguments,
            fired_by_product=payload.fired_by_product,
            fired_by_run=payload.fired_by_run,
            accepted_at=datetime.now(UTC),
        )
        if command.dispatch_status is DispatchStatus.DISPATCHED:
            # Terminal evidence already exists: a replay never executes a second time.
            return _to_contract(command)

        if await _emit(command):
            await repository.mark_dispatched(command, datetime.now(UTC))
        return _to_contract(command)

    async def evidence(
        self, session: AsyncSession, payload: JobCommandRef
    ) -> JobCommandContract:
        """Return the recorded evidence of a command within its own product."""

        command = await JobCommandRepository(session).get(
            payload.fired_by_product, payload.command_id
        )
        if command is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job command not found"
            )
        return _to_contract(command)


async def _emit(command: JobCommand) -> bool:
    """Publish ``job_fired``, reporting delivery instead of raising it at the caller.

    A command whose event could not be delivered stays non-terminal and is recorded,
    so a retry of the same identity re-attempts delivery without recording a second
    command. The payload carries no capability, secret or token.
    """
    try:
        await publish_job_fired(
            JobFired(
                contract_version=CORE_JOBS_CONTRACT_VERSION,
                command_id=command.command_id,
                name=command.name,
                arguments=command.arguments,
                fired_by_product=command.fired_by_product,
                fired_by_run=command.fired_by_run,
                accepted_at=command.accepted_at,
            )
        )
    except Exception:
        return False
    return True

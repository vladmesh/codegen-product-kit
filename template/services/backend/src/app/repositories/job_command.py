"""Database access for the idempotent, product-scoped job command ledger."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.job_command import DispatchStatus, JobCommand


class JobCommandRepository:
    """Read and record commands without taking ownership of the transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, fired_by_product: str, command_id: str) -> JobCommand | None:
        """Return a command only within the product that fired it."""
        result = await self.session.execute(
            select(JobCommand).where(
                JobCommand.fired_by_product == fired_by_product,
                JobCommand.command_id == command_id,
            )
        )
        return result.scalar_one_or_none()

    async def record(
        self,
        *,
        command_id: str,
        name: str,
        arguments: Any,
        fired_by_product: str,
        fired_by_run: str,
        accepted_at: datetime,
    ) -> tuple[JobCommand, bool]:
        """Record a command once, returning it and whether this call created it.

        A concurrent fire of the same identity loses the unique constraint rather
        than creating a second command, so the number of recorded commands never
        grows with the number of retries.
        """
        existing = await self.get(fired_by_product, command_id)
        if existing is not None:
            return existing, False

        try:
            async with self.session.begin_nested():
                command = JobCommand(
                    command_id=command_id,
                    name=name,
                    arguments=arguments,
                    fired_by_product=fired_by_product,
                    fired_by_run=fired_by_run,
                    dispatch_status=DispatchStatus.UNDELIVERED,
                    accepted_at=accepted_at,
                )
                self.session.add(command)
                await self.session.flush()
        except IntegrityError:
            raced = await self.get(fired_by_product, command_id)
            if raced is None:  # pragma: no cover - only a foreign constraint could do this
                raise
            return raced, False
        return command, True

    async def mark_dispatched(self, command: JobCommand, dispatched_at: datetime) -> JobCommand:
        """Make dispatch terminal, so no later fire of this identity emits again."""
        command.dispatch_status = DispatchStatus.DISPATCHED
        command.dispatched_at = dispatched_at
        await self.session.flush()
        return command


__all__ = ["JobCommandRepository"]

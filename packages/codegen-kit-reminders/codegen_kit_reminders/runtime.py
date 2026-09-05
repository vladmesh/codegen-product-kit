"""Durable tick consumption and due-event emission."""

from __future__ import annotations

from datetime import datetime
import os
from typing import Any

from codegen_kit import package_session, publish_event
from faststream.redis import RedisBroker, StreamSub
from faststream.redis.parser import BinaryMessageFormatV1
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import text as sql

from codegen_kit_reminders.api import SCHEMA
from codegen_kit_reminders.identity import due_event_id

CONSUMER_GROUP = "events:package:reminders"
JOB_STREAM = "job_fired"
DUE_STREAM = "reminders.due"


class TickArguments(BaseModel):
    """The instant against which scheduled reminders are evaluated."""

    at: AwareDatetime


async def transition_due(at: datetime) -> None:
    """Commit due state and an outbox row before any external publication."""

    async with package_session(SCHEMA) as session:
        rows = (
            await session.execute(
                sql(
                    "SELECT id FROM reminders "
                    "WHERE state = 'scheduled' AND remind_at <= :at "
                    "ORDER BY remind_at, id FOR UPDATE"
                ),
                {"at": at},
            )
        ).all()
        for row in rows:
            reminder_id = row.id
            await session.execute(
                sql("UPDATE reminders SET state = 'due', due_at = :at WHERE id = :id"),
                {"at": at, "id": reminder_id},
            )
            await session.execute(
                sql(
                    "INSERT INTO due_emissions (reminder_id, event_id, occurred_at) "
                    "VALUES (:reminder_id, :event_id, :occurred_at) "
                    "ON CONFLICT (reminder_id) DO NOTHING"
                ),
                {
                    "reminder_id": reminder_id,
                    "event_id": due_event_id(reminder_id),
                    "occurred_at": at,
                },
            )


async def emit_pending() -> None:
    """Publish pending outbox rows and mark only confirmed publications emitted."""

    async with package_session(SCHEMA) as session:
        rows = (
            await session.execute(
                sql(
                    "SELECT e.reminder_id, e.event_id, e.occurred_at, "
                    "r.user_ref, r.text, r.remind_at "
                    "FROM due_emissions AS e "
                    "JOIN reminders AS r ON r.id = e.reminder_id "
                    "WHERE e.emitted_at IS NULL "
                    "ORDER BY e.occurred_at, e.reminder_id FOR UPDATE OF e"
                )
            )
        ).all()
        for row in rows:
            emitted_at = datetime.now(row.occurred_at.tzinfo)
            await publish_event(
                DUE_STREAM,
                {
                    "reminder_id": row.reminder_id,
                    "user_ref": row.user_ref,
                    "text": row.text,
                    "remind_at": row.remind_at,
                },
                event_id=row.event_id,
                occurred_at=row.occurred_at,
            )
            await session.execute(
                sql(
                    "UPDATE due_emissions SET emitted_at = :emitted_at "
                    "WHERE reminder_id = :reminder_id"
                ),
                {"emitted_at": emitted_at, "reminder_id": row.reminder_id},
            )
            await session.execute(
                sql(
                    "UPDATE reminders SET state = 'emitted', emitted_at = :emitted_at "
                    "WHERE id = :reminder_id"
                ),
                {"emitted_at": emitted_at, "reminder_id": row.reminder_id},
            )


async def handle_job_fired(envelope: dict[str, Any]) -> None:
    """Run only the package's declared tick and ignore every other core job."""

    payload = envelope.get("payload")
    if not isinstance(payload, dict) or payload.get("name") != "reminders.tick":
        return
    arguments = TickArguments.model_validate(payload.get("arguments"))
    await transition_due(arguments.at)
    await emit_pending()


class ReminderConsumer:
    """Own the package's durable Redis Stream readers."""

    def __init__(self) -> None:
        self.broker: RedisBroker | None = None

    async def start(self) -> None:
        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            raise RuntimeError("REDIS_URL is not set; please add it to your environment variables")
        broker = RedisBroker(redis_url, message_format=BinaryMessageFormatV1)
        for role, idle_time in (("live", None), ("reclaim", 300_000)):
            subscriber = broker.subscriber(
                stream=StreamSub(
                    JOB_STREAM,
                    group=CONSUMER_GROUP,
                    consumer=f"reminders.{role}:{os.getpid()}",
                    min_idle_time=idle_time,
                    polling_interval=5_000,
                )
            )
            subscriber(handle_job_fired)
        await broker.start()
        self.broker = broker

    async def stop(self) -> None:
        if self.broker is not None:
            await self.broker.stop()
            self.broker = None

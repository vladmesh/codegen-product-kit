"""HTTP contract for one-time reminders owned by the verified caller."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from codegen_kit import caller_identity
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import text as sql

from codegen_kit_reminders.database import database

router = APIRouter()

# The owner is always the core-verified caller, never a value from the request.
CallerRef = Annotated[str, Depends(caller_identity)]


class ReminderCreate(BaseModel):
    """The deliberately small one-time reminder input."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    remind_at: AwareDatetime


class ReminderView(BaseModel):
    """A reminder as exposed by the package HTTP API."""

    id: UUID
    user_ref: str
    text: str
    remind_at: AwareDatetime
    state: Literal["scheduled", "cancelled", "due", "emitted"]
    created_at: AwareDatetime
    cancelled_at: AwareDatetime | None = None
    due_at: AwareDatetime | None = None
    emitted_at: AwareDatetime | None = None


def _view(row: object) -> ReminderView:
    return ReminderView.model_validate(dict(row._mapping))  # type: ignore[attr-defined]


@router.post("", response_model=ReminderView, status_code=status.HTTP_201_CREATED)
async def create_reminder(payload: ReminderCreate, user_ref: CallerRef) -> ReminderView:
    """Create one reminder for the caller at an explicit timezone-aware instant."""

    reminder_id = uuid4()
    created_at = datetime.now(UTC)
    async with database.session() as session:
        row = (
            await session.execute(
                sql(
                    "INSERT INTO reminders "
                    "(id, user_ref, text, remind_at, state, created_at) "
                    "VALUES (:id, :user_ref, :text, :remind_at, 'scheduled', :created_at) "
                    "RETURNING *"
                ),
                {
                    "id": reminder_id,
                    "user_ref": user_ref,
                    "text": payload.text,
                    "remind_at": payload.remind_at,
                    "created_at": created_at,
                },
            )
        ).one()
    return _view(row)


@router.get("", response_model=list[ReminderView])
async def list_reminders(user_ref: CallerRef) -> list[ReminderView]:
    """List the caller's own reminders."""

    async with database.session() as session:
        rows = (
            await session.execute(
                sql(
                    "SELECT * FROM reminders WHERE user_ref = :user_ref "
                    "ORDER BY remind_at, created_at, id"
                ),
                {"user_ref": user_ref},
            )
        ).all()
    return [_view(row) for row in rows]


@router.delete("/{reminder_id}", response_model=ReminderView)
async def cancel_reminder(reminder_id: UUID, user_ref: CallerRef) -> ReminderView:
    """Cancel the caller's own reminder only while it is still scheduled.

    Another user's reminder answers 404, exactly like a missing one.
    """

    async with database.session() as session:
        row = (
            await session.execute(
                sql(
                    "UPDATE reminders SET state = 'cancelled', cancelled_at = :cancelled_at "
                    "WHERE id = :id AND user_ref = :user_ref AND state = 'scheduled' "
                    "RETURNING *"
                ),
                {
                    "id": reminder_id,
                    "user_ref": user_ref,
                    "cancelled_at": datetime.now(UTC),
                },
            )
        ).one_or_none()
        if row is None:
            existing = await session.scalar(
                sql("SELECT state FROM reminders WHERE id = :id AND user_ref = :user_ref"),
                {"id": reminder_id, "user_ref": user_ref},
            )
            if existing is None:
                raise HTTPException(status_code=404, detail="Reminder not found")
            raise HTTPException(status_code=409, detail="Reminder is no longer cancellable")
    return _view(row)

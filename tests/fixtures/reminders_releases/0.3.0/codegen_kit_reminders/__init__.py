"""In-process package entry point for codegen-kit-reminders."""

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from codegen_kit import Package
from sqlalchemy import text as sql

from codegen_kit_reminders.api import router
from codegen_kit_reminders.runtime import ReminderConsumer

FIRST_DEPLOY_REMINDER_ID = uuid5(NAMESPACE_URL, "codegen-kit-reminders:first-deploy")
FIRST_DEPLOY_REMIND_AT = datetime(2000, 1, 1, tzinfo=UTC)


class RemindersPackage:
    """Mount reminder routes and consume externally fired ticks."""

    router = router

    def __init__(self) -> None:
        self.consumer = ReminderConsumer()

    async def startup(self, application: object) -> None:
        await self.consumer.start()

    async def shutdown(self, application: object) -> None:
        await self.consumer.stop()

    async def seed_setting(self, session: Any, key: str, value: Any) -> None:
        """Create the stable first-deploy reminder without replaying its state."""

        if key != "reminders.reminder_owner_ref" or not isinstance(value, str) or not value:
            raise ValueError("unsupported reminders setting seed")
        await session.execute(
            sql(
                "INSERT INTO reminders.reminders "
                "(id, user_ref, text, remind_at, state, created_at) "
                "VALUES (:id, :user_ref, :text, :remind_at, 'scheduled', :created_at) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": FIRST_DEPLOY_REMINDER_ID,
                "user_ref": value,
                "text": "Your first reminder is ready.",
                "remind_at": FIRST_DEPLOY_REMIND_AT,
                "created_at": datetime.now(UTC),
            },
        )


package: Package = RemindersPackage()

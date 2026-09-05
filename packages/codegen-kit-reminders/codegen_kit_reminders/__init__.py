"""In-process package entry point for codegen-kit-reminders."""

from codegen_kit import Package

from codegen_kit_reminders.api import router
from codegen_kit_reminders.runtime import ReminderConsumer


class RemindersPackage:
    """Mount reminder routes and consume externally fired ticks."""

    router = router

    def __init__(self) -> None:
        self.consumer = ReminderConsumer()

    async def startup(self, application: object) -> None:
        await self.consumer.start()

    async def shutdown(self, application: object) -> None:
        await self.consumer.stop()


package: Package = RemindersPackage()

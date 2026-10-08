"""In-process package lifecycle and initial-list setting seed."""

import logging
from typing import Any

from codegen_kit import Package, publish_event

from codegen_kit_tg_channels import api
from codegen_kit_tg_channels.client import ReaderClient, missing_environment
from codegen_kit_tg_channels.polling import Poller
from codegen_kit_tg_channels.runtime import ChannelConsumer
from codegen_kit_tg_channels.service import ChannelsService
from codegen_kit_tg_channels.store import Store

LOGGER = logging.getLogger(__name__)


class ChannelsPackage:
    router = api.router

    def __init__(self) -> None:
        self.store = Store()
        self.client: ReaderClient | None = None
        self.consumer: ChannelConsumer | None = None

    async def startup(self, application: object) -> None:
        missing = missing_environment()
        if missing:
            # Not configured is a valid state (for example a product's own integration tests):
            # platform actions answer not_configured and the timer does not poll.
            self.client = None
            LOGGER.warning(
                "tg-channels platform access is not configured: %s not set",
                ", ".join(missing),
            )
        else:
            self.client = ReaderClient.from_environment()
        api._service = ChannelsService(self.store, self.client)
        self.consumer = ChannelConsumer(Poller(self.store, self.client, publish_event))
        try:
            await self.consumer.start()
        except BaseException:
            if self.client is not None:
                await self.client.close()
            api._service = None
            raise

    async def shutdown(self, application: object) -> None:
        try:
            if self.consumer is not None:
                await self.consumer.stop()
        finally:
            if self.client is not None:
                await self.client.close()
            api._service = None

    async def seed_setting(self, session: Any, key: str, value: Any) -> None:
        await self.store.seed(session, key, value)


package: Package = ChannelsPackage()

"""In-process package lifecycle and initial-list setting seed."""

from typing import Any

from codegen_kit import Package, publish_event

from codegen_kit_tg_channels import api
from codegen_kit_tg_channels.client import ReaderClient
from codegen_kit_tg_channels.polling import Poller
from codegen_kit_tg_channels.runtime import ChannelConsumer
from codegen_kit_tg_channels.service import ChannelsService
from codegen_kit_tg_channels.store import Store


class ChannelsPackage:
    router = api.router

    def __init__(self) -> None:
        self.store = Store()
        self.client: ReaderClient | None = None
        self.consumer: ChannelConsumer | None = None

    async def startup(self, application: object) -> None:
        self.client = ReaderClient.from_environment()
        api._service = ChannelsService(self.store, self.client)
        self.consumer = ChannelConsumer(Poller(self.store, self.client, publish_event))
        try:
            await self.consumer.start()
        except BaseException:
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

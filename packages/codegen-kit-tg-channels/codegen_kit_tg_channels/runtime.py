"""Durable core job consumption using live and abandoned-entry stream readers."""

from contextlib import suppress
import os
from typing import Any

from faststream.redis import RedisBroker, StreamSub
from faststream.redis.parser import BinaryMessageFormatV1
from pydantic import AwareDatetime, BaseModel
from redis.exceptions import ResponseError

from codegen_kit_tg_channels.polling import Poller

#: The core fires package jobs under the package's identifier prefix, the name with "-" replaced
#: by "_" (framework/spec/loader.py `_package_prefix`), so the declared `tick` job is fired as
#: `tg_channels.tick`. The published `tg-channels.post` event keeps its own declared name.
TICK_JOB = "tg_channels.tick"


class TickArguments(BaseModel):
    at: AwareDatetime


class ChannelConsumer:
    def __init__(self, poller: Poller) -> None:
        self.poller = poller
        self.broker: RedisBroker | None = None

    async def handle_job(self, envelope: dict[str, Any]) -> None:
        payload = envelope.get("payload")
        if not isinstance(payload, dict) or payload.get("name") != TICK_JOB:
            return
        arguments = TickArguments.model_validate(payload.get("arguments"))
        await self.poller.tick(arguments.at)

    async def start(self) -> None:
        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            raise RuntimeError("REDIS_URL is not set; please add it to your environment variables")
        broker = RedisBroker(redis_url, message_format=BinaryMessageFormatV1)
        try:
            redis = await broker.connect()
            try:
                await redis.xgroup_create(
                    "job_fired", "events:package:tg-channels", id="$", mkstream=True
                )
            except ResponseError as error:
                if str(error).partition(" ")[0] != "BUSYGROUP":
                    raise
            for role, idle_time in (("live", None), ("reclaim", 300_000)):
                broker.subscriber(
                    stream=StreamSub(
                        "job_fired",
                        group="events:package:tg-channels",
                        consumer=f"tg-channels.{role}:{os.getpid()}",
                        min_idle_time=idle_time,
                        polling_interval=5_000,
                    )
                )(self.handle_job)
            await broker.start()
        except BaseException:
            with suppress(BaseException):
                await broker.stop()
            raise
        self.broker = broker

    async def stop(self) -> None:
        if self.broker is not None:
            await self.broker.stop()
            self.broker = None

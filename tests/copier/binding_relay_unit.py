"""Generated relay/lifecycle subset with no Redis or Telegram network."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4


async def scenario():  # noqa: PLR0915, C901
    from faststream.redis import RedisBroker, TestRedisBroker
    from faststream.redis.parser import BinaryMessageFormatV1
    from services.tg_bot.src.generated import binding_relay as r, bindings as b
    from shared.generated.events import EventEnvelope
    from telegram.error import BadRequest, Forbidden, TimedOut

    class MemoryRedis:
        def __init__(self):
            self.values = {}
            self.closed = False
            self.groups = []
            self.xack = AsyncMock()

        async def get(self, key):
            return self.values.get(key)

        async def set(self, key, value, *, nx=False, ex=None):
            assert ex is not None
            if nx and key in self.values:
                return False
            self.values[key] = value.encode()
            return True

        async def eval(self, script, count, key, token, *args):
            assert count == 1
            if self.values.get(key) != token.encode():
                return 0
            if script == r.FINISH:
                assert args == (r.RETENTION_SECONDS,)
                self.values[key] = b"done"
            else:
                assert script == r.RELEASE
                del self.values[key]
            return 1

        async def xgroup_create(self, *args, **kwargs):
            self.groups.append((args, kwargs))

        async def aclose(self):
            self.closed = True

    binding = b.DATA["bindings"][0]
    event = binding["events"][0]
    bot = SimpleNamespace(send_message=AsyncMock())
    store = MemoryRedis()
    relay = r.BindingRelay(bot, None)
    relay.redis = store

    def envelope(text="normal", recipient="telegram:123"):
        return EventEnvelope[dict](
            event_id=uuid4(),
            occurred_at=datetime.now(UTC),
            payload={
                "reminder_id": str(uuid4()),
                "user_ref": recipient,
                "text": text,
                "remind_at": datetime.now(UTC).isoformat(),
            },
        ).model_dump(mode="json")

    body = envelope()
    assert await relay.deliver(binding, event, body)
    assert await relay.deliver(binding, event, body)
    bot.send_message.assert_awaited_once_with(chat_id=123, text="Reminder: normal")
    key = f"bindings:{r.CONSUMER_GROUP}:{body['event_id']}"
    assert store.values[key] == b"done"
    body = envelope("retry")
    bot.send_message.side_effect = TimedOut()
    assert not await relay.deliver(binding, event, body)
    assert f"bindings:{r.CONSUMER_GROUP}:{body['event_id']}" not in store.values
    bot.send_message.side_effect = None
    assert await relay.deliver(binding, event, body)
    bot.send_message.side_effect = Forbidden("blocked")
    body = envelope("blocked")
    assert await relay.deliver(binding, event, body)
    assert await relay.deliver(binding, event, body)
    before = bot.send_message.await_count
    for body in (
        envelope(recipient="telegram:01"),
        envelope(recipient="email:123"),
        {"event_id": "bad"},
        envelope(recipient="telegram:-123"),
    ):
        assert await relay.deliver(binding, event, body)
    assert bot.send_message.await_count == before
    bot.send_message.side_effect = None
    body = envelope("concurrent")
    await asyncio.gather(*(relay.deliver(binding, event, body) for _ in range(8)))
    assert bot.send_message.await_count == before + 1

    for reason in ("Chat not found", "User is deactivated", "Bot was blocked"):
        body = envelope(reason)
        bot.send_message.side_effect = BadRequest(reason)
        assert await relay.deliver(binding, event, body)
        key = f"bindings:{r.CONSUMER_GROUP}:{body['event_id']}"
        assert store.values[key] == b"done"
    body = envelope("nonterminal bad request")
    bot.send_message.side_effect = BadRequest("Other Telegram error")
    assert not await relay.deliver(binding, event, body)
    assert f"bindings:{r.CONSUMER_GROUP}:{body['event_id']}" not in store.values
    body = envelope("cancelled")
    bot.send_message.side_effect = asyncio.CancelledError()
    try:
        await relay.deliver(binding, event, body)
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("delivery cancellation swallowed")
    assert f"bindings:{r.CONSUMER_GROUP}:{body['event_id']}" not in store.values
    bot.send_message.side_effect = None
    before = bot.send_message.await_count
    for version in (True, 1.0, 2):
        body = envelope()
        body["schema_version"] = version
        assert await relay.deliver(binding, event, body)
    assert bot.send_message.await_count == before

    # Exercise actual FastStream subscription, binary encoding, decoding and message injection.
    broker = RedisBroker("redis://redis.invalid", message_format=BinaryMessageFormatV1, logger=None)
    with (
        patch.object(r.Redis, "from_url", return_value=store),
        patch.object(r, "RedisBroker", return_value=broker),
        patch.object(broker, "start", new=AsyncMock()),
        patch.dict("os.environ", {"REDIS_URL": "redis://redis.invalid"}),
    ):
        await relay.start()
    assert len(store.groups) == 1 and store.groups[0][1] == {"id": "0-0", "mkstream": True}
    async with TestRedisBroker(broker):
        await broker.publish(envelope("transport"), stream=event["event"])
    assert bot.send_message.call_args.kwargs["text"] == "Reminder: transport"
    await relay.stop()
    assert store.closed and relay.broker is None and relay.redis is None

    failed_store = MemoryRedis()
    failed_store.xgroup_create = AsyncMock(side_effect=RuntimeError("startup failed"))
    failed_broker = SimpleNamespace(stop=AsyncMock())
    with (
        patch.object(r.Redis, "from_url", return_value=failed_store),
        patch.object(r, "RedisBroker", return_value=failed_broker),
        patch.dict("os.environ", {"REDIS_URL": "redis://redis.invalid"}),
    ):
        failed = r.BindingRelay(bot, None)
        try:
            await failed.start()
        except RuntimeError:
            pass
        else:
            raise AssertionError("startup failure ignored")
    assert failed_store.closed
    failed_broker.stop.assert_awaited_once()
    runtime = SimpleNamespace(relay=None)
    application = SimpleNamespace(bot=bot, bot_data={"bindings": runtime})
    with (
        patch.object(r.BindingRelay, "start", new=AsyncMock(side_effect=RuntimeError("failed"))),
        patch.object(r.BindingRelay, "stop", new=AsyncMock()) as stop,
    ):
        try:
            await b.start(application)
        except RuntimeError:
            pass
        else:
            raise AssertionError("failed relay startup ignored")
        stop.assert_awaited_once()
        assert runtime.relay is None


if __name__ == "__main__":
    asyncio.run(scenario())
    print("generated relay unit transport and lifecycle passed")

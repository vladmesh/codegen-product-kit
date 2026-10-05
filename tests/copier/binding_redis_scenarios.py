"""CI-only real Redis/BinaryMessageFormatV1 proof with a fake Telegram sender."""

import asyncio
from collections import Counter
from datetime import UTC, datetime
import json
import os
from uuid import uuid4


async def run_relay():  # noqa: PLR0915, C901
    from faststream.redis import RedisBroker
    from faststream.redis.parser import BinaryMessageFormatV1
    from redis.asyncio import Redis
    from services.tg_bot.src.generated.binding_relay import (
        CONSUMER_GROUP,
        RETENTION_SECONDS,
        BindingRelay,
    )
    from services.tg_bot.src.generated.bindings import DATA
    from shared.generated.events import EventEnvelope
    from telegram.error import Forbidden, TimedOut

    assert os.environ.get("CI") == "true", "Real Redis proof runs only in CI"
    url = os.environ["CODEGEN_BINDINGS_REDIS_URL"]
    os.environ["REDIS_URL"] = url
    redis = Redis.from_url(url)
    publisher = RedisBroker(url, message_format=BinaryMessageFormatV1, logger=None)
    binding = DATA["bindings"][0]
    event = binding["events"][0]
    stream = event["event"]
    assert stream == "reminders.due"
    sent = Counter()
    attempts = Counter()
    ids = []
    receipts = {}

    class Sender:
        async def send_message(self, *, chat_id, text):
            assert chat_id == 123
            attempts[text] += 1
            if text == "Reminder: transient" and attempts[text] == 1:
                raise TimedOut()
            if text == "Reminder: terminal":
                raise Forbidden("bot was blocked")
            await asyncio.sleep(0.05)
            sent[text] += 1

    async def publish(text, event_id=None, recipient="telegram:123"):
        identity = event_id or uuid4()
        if identity not in ids:
            ids.append(identity)
        envelope = EventEnvelope[dict](
            event_id=identity,
            occurred_at=datetime.now(UTC),
            schema_version=1,
            payload={
                "reminder_id": str(uuid4()),
                "user_ref": recipient,
                "text": text,
                "remind_at": datetime.now(UTC).isoformat(),
            },
        )
        await publisher.publish(envelope, stream=stream)
        return identity

    async def eventually(predicate):
        async with asyncio.timeout(20):
            while not await predicate():
                await asyncio.sleep(0.05)

    async def drained():
        pending = await redis.xpending(stream, CONSUMER_GROUP)
        groups = await redis.xinfo_groups(stream)
        group = next(item for item in groups if item["name"] == CONSUMER_GROUP.encode())
        return pending["pending"] == 0 and group["lag"] == 0

    async def sent_count(text, count):
        return sent["Reminder: " + text] == count

    relays = [
        BindingRelay(Sender(), None, reclaim_idle_ms=150, reclaim_poll_ms=50) for _ in range(2)
    ]
    try:
        await redis.delete(stream)
        await publisher.connect()
        prestart = await publish("before first startup")
        await relays[0].start()
        await eventually(lambda: sent_count("before first startup", 1))
        receipts["before_first_startup"] = True
        assert await redis.get(f"bindings:{CONSUMER_GROUP}:{prestart}") == b"done"
        ttl = await redis.ttl(f"bindings:{CONSUMER_GROUP}:{prestart}")
        assert RETENTION_SECONDS - 30 <= ttl <= RETENTION_SECONDS
        identity = await publish("normal")
        await eventually(lambda: sent_count("normal", 1))
        await publish("normal", identity)
        await eventually(drained)
        assert sent["Reminder: normal"] == 1
        receipts["normal_duplicate"] = True
        await relays[1].start()
        concurrent = uuid4()
        await asyncio.gather(*(publish("concurrent", concurrent) for _ in range(8)))
        await eventually(lambda: sent_count("concurrent", 1))
        await eventually(drained)
        assert sent["Reminder: concurrent"] == 1
        receipts["concurrent_consumers"] = True
        for relay in relays:
            await relay.stop()
        await relays[0].start()
        await publish("normal", identity)
        await eventually(drained)
        assert sent["Reminder: normal"] == 1
        receipts["restart_persistent_dedupe"] = True
        transient = await publish("transient")
        await eventually(lambda: sent_count("transient", 1))
        await eventually(drained)
        assert attempts["Reminder: transient"] >= 2
        assert await redis.get(f"bindings:{CONSUMER_GROUP}:{transient}") == b"done"
        receipts["transient_reclaim_retry"] = True
        terminal = await publish("terminal")
        await publish("poison", recipient="telegram:0123")
        await publisher.publish({"event_id": "broken"}, stream=stream)
        await eventually(drained)
        assert sent["Reminder: terminal"] == sent["Reminder: poison"] == 0
        await publish("terminal", terminal)
        await eventually(drained)
        assert attempts["Reminder: terminal"] == 1
        receipts["terminal_and_invalid_envelope"] = True
        crashed = uuid4()
        await redis.set(f"bindings:{CONSUMER_GROUP}:{crashed}", "crashed-lease", ex=1)
        await publish("crash recovery", crashed)
        await eventually(lambda: sent_count("crash recovery", 1))
        await eventually(drained)
        receipts["expired_claim_recovery"] = True
        receipts.update(
            stream=stream,
            group=CONSUMER_GROUP,
            first_offset="0-0",
            retention_seconds=RETENTION_SECONDS,
            sends=dict(sent),
            attempts=dict(attempts),
        )
        return receipts
    finally:
        for relay in relays:
            await relay.stop()
        await publisher.stop()
        await redis.delete(stream, *(f"bindings:{CONSUMER_GROUP}:{identity}" for identity in ids))
        await redis.aclose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_relay()), sort_keys=True))

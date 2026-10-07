"""Consumer lifecycle, job selection and real API error envelopes without infrastructure."""
# ruff: noqa: PLR2004

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
import httpx
import pytest
from test_behaviour import store
from test_client_contract import KEY, channel


def test_real_api_replies_errors_pending_acceptance_and_user_owned_removal(modules):
    async def exercise():
        repository, memory = store(modules)
        status = ["ok"]
        client = modules.client.ReaderClient(
            "https://service.test",
            KEY,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=channel(status[0]))
            ),
        )
        modules.api._service = modules.service.ChannelsService(repository, client)
        app = FastAPI()
        app.include_router(modules.api.router, prefix="/tg-channels")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
        ) as http:
            # A request cannot override the canonical caller with a JSON user_ref.
            refused = await http.post(
                "/tg-channels", json={"text": "cyproplan", "user_ref": "telegram:456"}
            )
            assert refused.status_code == 422 and not memory.subscriptions
            invalid = await http.post("/tg-channels", json={"text": "  @bad name "})
            assert invalid.json() == {"detail": {"code": "invalid_username"}}
            status[0] = "pending"
            pending = await http.post("/tg-channels", json={"text": "  @CyproPlan "})
            assert pending.status_code == 409 and pending.json() == {"detail": {"code": "pending"}}
            assert memory.subscriptions == {("telegram:123", "cyproplan")}
            listed = await http.get("/tg-channels")
            assert listed.json() == [{"id": "cyproplan", "channel": "cyproplan"}]
            await http.delete("/tg-channels/cyproplan")
            assert (await http.get("/tg-channels")).json() == []
            assert (await http.get("/tg-channels/digest")).json() == []
        await client.close()

    asyncio.run(exercise())


def test_seed_initialization_at_union_limit_returns_declared_error_for_all_actions(modules):
    async def exercise():
        repository, memory = store(modules)
        for index in range(50):
            await repository.add("telegram:456", f"channel{index}")
        await repository.seed(memory, "tg_channels.starting_channels", ["cyproplan"])
        client = modules.client.ReaderClient(
            "https://service.test",
            KEY,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=channel())),
        )
        modules.api._service = modules.service.ChannelsService(repository, client)
        app = FastAPI()
        app.include_router(modules.api.router, prefix="/tg-channels")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
        ) as http:
            for method, path, kwargs in [
                ("GET", "/tg-channels", {}),
                ("DELETE", "/tg-channels/cyproplan", {}),
                ("GET", "/tg-channels/digest", {}),
                ("POST", "/tg-channels", {"json": {"text": "cyproplan"}}),
            ]:
                reply = await http.request(method, path, **kwargs)
                assert reply.status_code == 409
                assert reply.json() == {"detail": {"code": "try_later"}}
                assert "telegram:123" not in memory.users
            assert len(memory.subscriptions) == 50
        await client.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", [None, "BUSYGROUP", "NOAUTH", "start"])
def test_consumer_initializes_group_registers_reclaim_and_cleans_up(  # noqa: C901
    modules, monkeypatch, failure
):
    calls = []
    handlers = []

    class ResponseError(Exception):
        pass

    class Broker:
        async def connect(self):
            calls.append("connect")
            return self

        async def xgroup_create(self, *args, **kwargs):
            calls.append("group")
            assert args == ("job_fired", "events:package:tg-channels")
            assert kwargs == {"id": "$", "mkstream": True}
            if failure in ("BUSYGROUP", "NOAUTH"):
                raise ResponseError(failure)

        def subscriber(self, *, stream):
            calls.append(stream)
            return handlers.append

        async def start(self):
            calls.append("start")
            if failure == "start":
                raise RuntimeError("start failed")

        async def stop(self):
            calls.append("stop")

    broker = Broker()
    for name, module in {
        "faststream": ModuleType("faststream"),
        "faststream.redis": SimpleNamespace(
            RedisBroker=lambda *args, **kwargs: broker,
            StreamSub=lambda *args, **kwargs: kwargs["min_idle_time"],
        ),
        "faststream.redis.parser": SimpleNamespace(BinaryMessageFormatV1=object()),
        "redis": ModuleType("redis"),
        "redis.exceptions": SimpleNamespace(ResponseError=ResponseError),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "codegen_kit_tg_channels.runtime", raising=False)
    runtime = importlib.import_module("codegen_kit_tg_channels.runtime")
    monkeypatch.setenv("REDIS_URL", "redis://offline.test")
    poller = SimpleNamespace(tick=AsyncMock())
    consumer = runtime.ChannelConsumer(poller)

    async def exercise():
        if failure in ("NOAUTH", "start"):
            with pytest.raises((ResponseError, RuntimeError)):
                await consumer.start()
            assert consumer.broker is None and calls[-1] == "stop"
            return
        await consumer.start()
        assert calls == ["connect", "group", None, 300_000, "start"]
        assert handlers == [consumer.handle_job, consumer.handle_job]
        await consumer.handle_job({"payload": {"name": "unrelated.tick", "arguments": {}}})
        poller.tick.assert_not_awaited()
        await consumer.handle_job(
            {"payload": {"name": "tg-channels.tick", "arguments": {"at": "2026-10-07T21:00:00Z"}}}
        )
        poller.tick.assert_awaited_once()
        await consumer.stop()
        assert consumer.broker is None and calls[-1] == "stop"
        monkeypatch.delenv("REDIS_URL")
        with pytest.raises(RuntimeError, match="REDIS_URL is not set"):
            await consumer.start()

    asyncio.run(exercise())

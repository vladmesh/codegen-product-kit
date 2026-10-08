"""Package startup without platform environment: not_configured actions and no platform poll."""
# ruff: noqa: PLR2004

import asyncio
from datetime import UTC, datetime
import importlib.util
import logging
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from conftest import PACKAGE
from fastapi import FastAPI
import httpx
import pytest
from test_behaviour import store
from test_client_contract import KEY

BASE_URL = "https://platform.example.test/tg-reader"
AT = datetime(2026, 10, 7, 21, tzinfo=UTC)


class Consumer:
    instances: list["Consumer"] = []

    def __init__(self, poller):
        self.poller = poller
        self.start = AsyncMock()
        self.stop = AsyncMock()
        Consumer.instances.append(self)


def lifecycle(modules, monkeypatch, environment):
    """Load the real package lifecycle over the offline modules and a recording consumer."""
    for name in ("PLATFORM_BASE_URL", "PLATFORM_KEY"):
        if name in environment:
            monkeypatch.setenv(name, environment[name])
        else:
            monkeypatch.delenv(name, raising=False)
    Consumer.instances = []
    publish = AsyncMock()
    codegen_kit = sys.modules["codegen_kit"]
    monkeypatch.setitem(
        sys.modules,
        "codegen_kit",
        SimpleNamespace(
            caller_identity=codegen_kit.caller_identity, Package=object, publish_event=publish
        ),
    )
    monkeypatch.setitem(
        sys.modules, "codegen_kit_tg_channels.runtime", SimpleNamespace(ChannelConsumer=Consumer)
    )
    spec = importlib.util.spec_from_file_location(
        "codegen_kit_tg_channels_lifecycle", PACKAGE / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repository, memory = store(modules)
    module.package.store = repository
    return SimpleNamespace(module=module, package=module.package, memory=memory, publish=publish)


def warnings(caplog):
    return [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and record.name.startswith("codegen_kit_tg_channels")
    ]


@pytest.mark.parametrize(
    "environment,missing",
    [
        ({}, "PLATFORM_BASE_URL, PLATFORM_KEY"),
        ({"PLATFORM_KEY": KEY}, "PLATFORM_BASE_URL"),
        ({"PLATFORM_BASE_URL": BASE_URL}, "PLATFORM_KEY"),
    ],
)
def test_unconfigured_startup_warns_once_and_platform_actions_are_not_configured(
    modules, monkeypatch, caplog, environment, missing
):
    caplog.set_level(logging.DEBUG)
    context = lifecycle(modules, monkeypatch, environment)

    async def exercise():
        await context.package.startup(object())
        assert context.package.client is None
        (consumer,) = Consumer.instances
        consumer.start.assert_awaited_once()
        assert consumer.poller.client is None
        app = FastAPI()
        app.include_router(modules.api.router, prefix="/tg-channels")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
        ) as http:
            added = await http.post("/tg-channels", json={"text": "@CyproPlan"})
            assert added.status_code == 409
            assert added.json() == {"detail": {"code": "not_configured"}}
            # Username validation stays local and precedes the platform check.
            invalid = await http.post("/tg-channels", json={"text": "bad name"})
            assert invalid.json() == {"detail": {"code": "invalid_username"}}
            assert not context.memory.subscriptions
            # Empty lists need no platform read.
            assert (await http.get("/tg-channels/digest")).json() == []
            # Stored channels (for example a seeded starting list) stay listable and removable.
            await context.package.store.add("telegram:123", "cyproplan")
            await context.package.store.add("telegram:123", "kipr_podslushano_limasol")
            digest = await http.get("/tg-channels/digest")
            assert digest.status_code == 409
            assert digest.json() == {"detail": {"code": "not_configured"}}
            listed = await http.get("/tg-channels")
            assert [item["channel"] for item in listed.json()] == [
                "cyproplan",
                "kipr_podslushano_limasol",
            ]
            removed = await http.delete("/tg-channels/kipr_podslushano_limasol")
            assert removed.json() == {
                "id": "kipr_podslushano_limasol",
                "channel": "kipr_podslushano_limasol",
            }
            assert (await http.get("/tg-channels")).json() == [
                {"id": "cyproplan", "channel": "cyproplan"}
            ]
        # A timer tick with subscriptions performs no platform request and changes no state.
        state = dict(context.memory.state)
        statements = len(context.memory.statements)
        await consumer.poller.tick(AT)
        assert context.memory.state == state
        assert not any("poll_state" in query for query in context.memory.statements[statements:])
        context.publish.assert_not_awaited()
        await context.package.shutdown(object())
        consumer.stop.assert_awaited_once()
        assert modules.api._service is None

    asyncio.run(exercise())
    (warning,) = warnings(caplog)
    assert warning.getMessage() == (
        f"tg-channels platform access is not configured: {missing} not set"
    )
    assert KEY not in caplog.text and BASE_URL not in caplog.text


def test_unconfigured_timer_still_emits_committed_deliveries(modules, monkeypatch):
    context = lifecycle(modules, monkeypatch, {})

    async def exercise():
        await context.package.startup(object())
        (consumer,) = Consumer.instances
        context.memory.deliveries["event"] = SimpleNamespace(
            event_id="event", payload={"user_ref": "telegram:123"}, occurred_at=AT, emitted_at=None
        )
        await consumer.poller.tick(AT)
        context.publish.assert_awaited_once()
        assert context.memory.deliveries["event"].emitted_at == AT
        await context.package.shutdown(object())

    asyncio.run(exercise())


def test_configured_startup_builds_the_client_without_warning_or_key_in_logs(
    modules, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    context = lifecycle(modules, monkeypatch, {"PLATFORM_BASE_URL": BASE_URL, "PLATFORM_KEY": KEY})

    async def exercise():
        await context.package.startup(object())
        client = context.package.client
        assert isinstance(client, modules.client.ReaderClient)
        (consumer,) = Consumer.instances
        assert consumer.poller.client is client
        assert modules.api._service.client is client
        assert str(client._http.base_url) == BASE_URL + "/"
        assert KEY not in repr(vars(client))
        await context.package.shutdown(object())
        assert client._http.is_closed

    asyncio.run(exercise())
    assert warnings(caplog) == []
    assert KEY not in caplog.text


def test_failed_consumer_start_without_client_resets_the_service(modules, monkeypatch):
    context = lifecycle(modules, monkeypatch, {})
    original = Consumer.__init__

    def failing(self, poller):
        original(self, poller)
        self.start = AsyncMock(side_effect=RuntimeError("REDIS_URL is not set"))

    monkeypatch.setattr(Consumer, "__init__", failing)
    with pytest.raises(RuntimeError, match="REDIS_URL is not set"):
        asyncio.run(context.package.startup(object()))
    assert modules.api._service is None

"""Focused lifecycle tests for the independently installed reminders consumer."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

RUNTIME = (
    Path(__file__).parents[2] / "packages/codegen-kit-reminders/codegen_kit_reminders/runtime.py"
)


class FakeResponseError(Exception):
    """Stand in for redis.exceptions.ResponseError in the dependency-light unit suite."""


class FakeRedis:
    def __init__(self, events: list[str], error: FakeResponseError | None = None) -> None:
        self.events = events
        self.error = error
        self.create_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def xgroup_create(self, *args: Any, **kwargs: Any) -> None:
        self.events.append("xgroup_create")
        self.create_calls.append((args, kwargs))
        if self.error is not None:
            raise self.error


def _load_runtime(
    monkeypatch: pytest.MonkeyPatch,
    redis: FakeRedis,
) -> tuple[ModuleType, list[Any]]:
    events = redis.events
    brokers: list[Any] = []

    class FakeSubscriber:
        def __call__(self, handler: Any) -> None:
            events.append("handler_registered")

    class FakeBroker:
        def __init__(self, url: str, **kwargs: Any) -> None:
            self.url = url
            self.kwargs = kwargs
            self.started = False
            self.stop_calls = 0
            brokers.append(self)

        async def connect(self) -> FakeRedis:
            events.append("connect")
            return redis

        def subscriber(self, *, stream: Any) -> FakeSubscriber:
            events.append("subscriber")
            return FakeSubscriber()

        async def start(self) -> None:
            events.append("start")
            self.started = True

        async def stop(self) -> None:
            events.append("stop")
            self.started = False
            self.stop_calls += 1

    modules = {
        "codegen_kit": SimpleNamespace(publish_event=None),
        "faststream": ModuleType("faststream"),
        "faststream.redis": SimpleNamespace(
            RedisBroker=FakeBroker,
            StreamSub=lambda *args, **kwargs: (args, kwargs),
        ),
        "faststream.redis.parser": SimpleNamespace(BinaryMessageFormatV1=object()),
        "redis": ModuleType("redis"),
        "redis.exceptions": SimpleNamespace(ResponseError=FakeResponseError),
        "sqlalchemy": SimpleNamespace(text=lambda statement: statement),
        "codegen_kit_reminders": ModuleType("codegen_kit_reminders"),
        "codegen_kit_reminders.database": SimpleNamespace(database=SimpleNamespace()),
        "codegen_kit_reminders.identity": SimpleNamespace(due_event_id=lambda value: value),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location("_tested_reminders_runtime", RUNTIME)
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    return runtime, brokers


def test_start_creates_stream_group_before_registering_readers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    redis = FakeRedis(events)
    runtime, brokers = _load_runtime(monkeypatch, redis)
    monkeypatch.setenv("REDIS_URL", "redis://empty")
    consumer = runtime.ReminderConsumer()

    asyncio.run(consumer.start())

    assert redis.create_calls == [
        ((runtime.JOB_STREAM, runtime.CONSUMER_GROUP), {"id": "$", "mkstream": True})
    ]
    assert events == [
        "connect",
        "xgroup_create",
        "subscriber",
        "handler_registered",
        "subscriber",
        "handler_registered",
        "start",
    ]
    assert consumer.broker is brokers[0]


def test_existing_group_survives_shutdown_and_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    redis = FakeRedis(events, FakeResponseError("BUSYGROUP Consumer Group name already exists"))
    runtime, brokers = _load_runtime(monkeypatch, redis)
    monkeypatch.setenv("REDIS_URL", "redis://existing")
    consumer = runtime.ReminderConsumer()

    asyncio.run(consumer.start())
    asyncio.run(consumer.stop())
    asyncio.run(consumer.start())

    assert len(redis.create_calls) == 2
    assert len(brokers) == 2
    assert brokers[0].stop_calls == 1
    assert consumer.broker is brokers[1]
    assert brokers[1].started is True


def test_non_busygroup_redis_failure_aborts_startup_with_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    failure = FakeResponseError("NOAUTH Authentication required")
    redis = FakeRedis(events, failure)
    runtime, brokers = _load_runtime(monkeypatch, redis)
    monkeypatch.setenv("REDIS_URL", "redis://broken")
    consumer = runtime.ReminderConsumer()

    with pytest.raises(FakeResponseError) as raised:
        asyncio.run(consumer.start())

    assert raised.value is failure
    assert events == ["connect", "xgroup_create", "stop"]
    assert brokers[0].stop_calls == 1
    assert consumer.broker is None

"""Execute generated channel commands and relay in a real product, with offline transport."""
# ruff: noqa: PLR2004

import asyncio
from datetime import UTC, datetime
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx


def update(text="", *, user=123, data=None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user),
        effective_chat=SimpleNamespace(id=123),
        message=SimpleNamespace(text=text, reply_text=AsyncMock()),
        callback_query=None
        if data is None
        else SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=user),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        ),
    )


class MemoryRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, nx=False, ex=None):
        assert nx and ex
        if key in self.values:
            return False
        self.values[key] = value.encode()
        return True

    async def eval(self, script, count, key, token, *args):
        assert count == 1
        if self.values.get(key) != token.encode():
            return 0
        if args:
            self.values[key] = b"done"
        else:
            del self.values[key]
        return 1


async def scenario(locale):  # noqa: C901, PLR0915
    from services.tg_bot.src.generated import binding_relay as r, bindings as b
    from services.tg_bot.src.main import BackendClient

    os.environ["USER_IDENTITY_CAPABILITY"] = "test-capability"
    os.environ["BACKEND_API_URL"] = "http://backend.test"
    language = [locale]
    channels = []
    posts = []
    requests = []
    failure = [None]

    def respond(request):
        body = json.loads(request.content) if request.content else {}
        requests.append((request, body))
        if request.url.path == "/settings/get":
            assert body == {"contract_version": 1, "key": "language", "scope": "product"}
            return httpx.Response(200, json=body | {"value": language[0]})
        assert request.headers["X-Identity-Capability"] == "test-capability"
        assert request.headers["X-User-Channel"] == "telegram"
        assert request.headers["X-User-External-Id"] == "123"
        if failure[0]:
            return httpx.Response(409, json={"detail": {"code": failure[0], "secret": "redacted"}})
        if request.method == "POST":
            assert request.url.path == "/tg-channels" and set(body) == {"text"}
            # The package behaviour lane proves normalization; this boundary proves $text survives.
            assert body["text"] == "  @cyproplan  "
            result = {"id": "cyproplan", "channel": "cyproplan"}
            channels.append(result)
            return httpx.Response(200, json=result)
        if request.method == "DELETE":
            assert request.url.path == "/tg-channels/cyproplan"
            result = channels.pop()
            return httpx.Response(200, json=result)
        assert request.method == "GET"
        if request.url.path == "/tg-channels":
            return httpx.Response(200, json=channels)
        assert request.url.path == "/tg-channels/digest"
        return httpx.Response(200, json=posts)

    class FakeClient(BackendClient):
        async def __aenter__(self):
            self.initial_delay = 0
            self._client = httpx.AsyncClient(
                base_url=self.base_url, transport=httpx.MockTransport(respond)
            )
            return self

    (binding,) = b.DATA["bindings"]
    assert binding["binding_version"] == 2 and binding["package"] == "tg-channels"
    created, listed, digest = binding["commands"]
    runtime = b.ChannelBindings(FakeClient)

    async def command(selected, text):
        incoming = update(text)
        await runtime.command(binding, selected, incoming, None)
        return incoming.message.reply_text.call_args

    for selected in (listed, digest):
        reply = await command(selected, "/" + selected["command"])
        assert reply.args[0] == selected["on_none"][locale]
        before = len(requests)
        reply = await command(selected, "/" + selected["command"] + " extra")
        assert reply.args[0] == selected["help"][locale] and len(requests) == before + 1
    assert (await command(created, "/channel \t")).args[0] == created["on_empty"][locale]
    assert (await command(created, "/channel " + "x" * 4001)).args[0] == created["on_invalid_text"][
        locale
    ]
    reply = await command(created, "/channel   @cyproplan  ")
    assert (
        reply.args[0]
        == ("Добавлен канал: @" if locale == "ru" else "Channel added: @") + "cyproplan"
    )
    for code in created["on_error"]:
        failure[0] = code
        before = len(requests)
        reply = await command(created, "/channel arbitrary")
        assert reply.args[0] == created["on_error"][code][locale]
        assert "redacted" not in reply.args[0] and len(requests) == before + 2
    failure[0] = None
    reply = await command(listed, "/channels")
    assert reply.args[0] == "@cyproplan"
    button = reply.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == ("Удалить" if locale == "ru" else "Remove")
    forged = update(user=456, data=button.callback_data)
    await runtime.callback(forged, None)
    assert forged.callback_query.answer.call_args.kwargs["show_alert"] and len(channels) == 1
    accepted = update(data=button.callback_data)
    await runtime.callback(accepted, None)
    assert accepted.callback_query.edit_message_text.call_args.args[0] == (
        "Удалён канал: @cyproplan" if locale == "ru" else "Channel removed: @cyproplan"
    )
    assert not channels
    replay = update(data=button.callback_data)
    await runtime.callback(replay, None)
    assert replay.callback_query.answer.call_args.kwargs["show_alert"]
    assert (await command(listed, "/channels")).args[0] == listed["on_none"][locale]
    post = {
        "channel": "cyproplan",
        "date": "2026-10-07T21:00:00+00:00",
        "text": "News",
        "url": "https://t.me/cyproplan/7",
    }
    posts.append(post)
    displayed = "@cyproplan\n2026-10-07T21:00:00+00:00\nNews\nhttps://t.me/cyproplan/7"
    reply = await command(digest, "/digest")
    assert reply.args[0] == displayed and reply.kwargs["reply_markup"] is None
    (event,) = binding["events"]
    bot = SimpleNamespace(send_message=AsyncMock())
    relay = r.BindingRelay(bot, runtime)
    relay.redis = MemoryRedis()
    envelope = {
        "event_id": str(uuid4()),
        "occurred_at": datetime.now(UTC).isoformat(),
        "schema_version": 1,
        "payload": post | {"user_ref": "telegram:123"},
    }
    assert await relay.deliver(binding, event, envelope)
    assert await relay.deliver(binding, event, envelope)
    bot.send_message.assert_awaited_once_with(
        chat_id=123, text=("Новая публикация: " if locale == "ru" else "New post: ") + displayed
    )
    language[0] = "fr"
    assert (await command(digest, "/digest")).args[0] == b.LANGUAGE_SETUP
    print(f"channels {locale}: commands, errors, callbacks and delivery passed")


if __name__ == "__main__":
    import sys

    asyncio.run(scenario(sys.argv[1]))

"""Execute generated v2 handlers and relay with real HTTP/Telegram contracts, offline."""

import asyncio
from datetime import UTC, datetime
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

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
        self.expires = {}
        self.now = 0

    async def get(self, key):
        if key in self.values and self.expires[key] <= self.now:
            del self.values[key]
        return self.values.get(key)

    async def set(self, key, value, *, nx=False, ex=None):
        assert nx and ex
        if await self.get(key) is not None:
            return False
        self.values[key] = value.encode()
        self.expires[key] = self.now + ex
        return True

    async def eval(self, script, count, key, token, *args):
        assert count == 1
        if await self.get(key) != token.encode():
            return 0
        if args:
            self.values[key] = b"done"
            self.expires[key] = self.now + args[0]
        else:
            del self.values[key]
        return 1


async def scenario(locale):  # noqa: C901, PLR0915
    from services.tg_bot.src.generated import binding_relay as r, bindings as b
    from services.tg_bot.src.main import BackendClient

    os.environ["USER_IDENTITY_CAPABILITY"] = "test-capability"
    os.environ["BACKEND_API_URL"] = "http://backend.test"
    language = [locale]
    items = []
    requests = []
    failure = [None]
    settings_status = [200]
    settings_error = [None]
    settings_body = [None]
    settings_fail_key = ["language"]

    def settings_response(request, data):
        value = "Europe/Moscow" if data["key"] == "timezone" else language[0]
        if data["key"] != settings_fail_key[0]:
            return httpx.Response(200, json={**data, "value": value})
        if settings_error[0] is not None:
            raise settings_error[0]("secret transport diagnostic", request=request)
        if isinstance(settings_body[0], bytes):
            return httpx.Response(settings_status[0], content=settings_body[0])
        body = settings_body[0] if settings_body[0] is not None else {**data, "value": value}
        return httpx.Response(settings_status[0], json=body)

    def respond(request):
        data = json.loads(request.content) if request.content else {}
        requests.append((request, data))
        if request.url.path == "/settings/get":
            assert data["contract_version"] == 1 and data["scope"] == "product"
            assert set(data) == {"contract_version", "key", "scope"}
            assert data["key"] in ("timezone", "language")
            return settings_response(request, data)
        assert request.headers["X-Identity-Capability"] == "test-capability"
        assert request.headers["X-User-Channel"] == "telegram"
        assert request.headers["X-User-External-Id"] == "123"
        if failure[0]:
            return httpx.Response(
                409, json={"detail": {"code": failure[0], "secret": "never display"}}
            )
        if request.method == "POST":
            assert request.url.path == "/binding-notes" and set(data) == {"text"}
            item = {"id": str(uuid4()), "text": data["text"], "state": "active"}
            items.append(item)
            return httpx.Response(200, json=item)
        if request.method == "GET" and request.url.path == "/reminders":
            return httpx.Response(200, json=[])
        if request.method == "GET":
            assert request.url.path in ("/binding-notes", "/binding-notes/digest")
            return httpx.Response(200, json=items)
        assert request.method == "DELETE"
        item = next(item for item in items if request.url.path == "/binding-notes/" + item["id"])
        item["state"] = "removed"
        return httpx.Response(200, json=item)

    class FakeClient(BackendClient):
        async def __aenter__(self):
            # Retain the real retry loop without backoff waits against an offline transport.
            self.initial_delay = 0
            self._client = httpx.AsyncClient(
                base_url=self.base_url, transport=httpx.MockTransport(respond)
            )
            return self

    binding = next(binding for binding in b.DATA["bindings"] if binding["binding_version"] == 2)
    create, listed, show = binding["commands"]
    runtime = b.ChannelBindings(FakeClient)

    async def command(text, selected=create):
        u = update(text)
        await runtime.command(binding, selected, u, None)
        return u.message.reply_text.call_args

    expected = {
        "ru": ["Добавлено: ", "Заметка: ", "Удалено: ", "Дайджест: ", "Новое: ", "Удалить"],
        "en": ["Added: ", "Note: ", "Removed: ", "Digest: ", "New: ", "Remove"],
    }[locale]
    for selected in (listed, show):
        reply = await command("/" + selected["command"], selected)
        assert reply.args[0] == selected["on_none"][locale]
        before = len(requests)
        reply = await command("/" + selected["command"] + " unexpected", selected)
        assert reply.args[0] == selected["help"][locale]
        assert len(requests) == before + 1  # language read, no action
    reply = await command("/note \t")
    assert reply.args[0] == create["on_empty"][locale] and not items
    reply = await command("/note " + "x" * 4001)
    assert reply.args[0] == create["on_invalid_text"][locale] and not items
    original = "My   Note"
    reply = await command("/note " + original)
    assert reply.args[0] == expected[0] + original and items[0]["text"] == original
    for code in ("not_found", "pending", "unknown"):
        failure[0] = code
        before = len(requests)
        reply = await command("/note second")
        assert reply.args[0] == create["on_error"].get(code, b.ERROR)[locale]
        assert len(requests) == before + 2 and len(items) == 1
    failure[0] = None
    reply = await command("/notes", listed)
    assert reply.args[0] == expected[1] + original
    button = reply.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == expected[5]
    forged = update(user=124, data=button.callback_data)
    await runtime.callback(forged, None)
    assert forged.callback_query.answer.call_args.kwargs["show_alert"]
    assert items[0]["state"] == "active"
    # Language is read again on callback, rather than frozen in pending context.
    other = "en" if locale == "ru" else "ru"
    language[0] = other
    accepted = update(data=button.callback_data)
    await runtime.callback(accepted, None)
    assert (
        accepted.callback_query.edit_message_text.call_args.args[0]
        == listed["buttons"][0]["reply"]["parts"][0][other] + original
    )
    assert items[0]["state"] == "removed"
    duplicate = update(data=button.callback_data)
    await runtime.callback(duplicate, None)
    assert duplicate.callback_query.answer.call_args.kwargs["show_alert"]
    language[0] = locale
    reply = await command("/digest", show)
    assert reply.args[0] == expected[3] + original and reply.kwargs["reply_markup"] is None
    listed["filter"] = {"field": "state", "equals": "active"}
    reply = await command("/notes", listed)
    assert reply.args[0] == listed["on_none"][locale]
    listed["filter"] = None

    event = binding["events"][0]
    bot = SimpleNamespace(send_message=AsyncMock())
    relay = r.BindingRelay(bot, runtime)
    relay.redis = MemoryRedis()
    peer = r.BindingRelay(bot, runtime)
    peer.redis = relay.redis

    def body():
        return {
            "event_id": str(uuid4()),
            "occurred_at": datetime.now(UTC).isoformat(),
            "schema_version": 1,
            "payload": {"user_ref": "telegram:123", "text": original},
        }

    envelope = body()
    assert await relay.deliver(binding, event, envelope)
    assert await relay.deliver(binding, event, envelope)
    bot.send_message.assert_awaited_once_with(chat_id=123, text=expected[4] + original)
    reply = await command("/notes", listed)
    token = reply.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
    language[0] = "fr"
    before = len(requests)
    unconfigured = update(data=token)
    await runtime.callback(unconfigured, None)
    assert unconfigured.callback_query.edit_message_text.call_args.args[0] == b.LANGUAGE_SETUP
    assert len(requests) == before + 1
    unconfigured_events = []
    for value in (None, "fr", "", 1, ["en"]):
        language[0] = value
        before = len(requests)
        reply = await command("/note no mutation")
        assert reply.args[0] == b.LANGUAGE_SETUP
        assert len(requests) == before + 1 and len(items) == 1
        pending = body()
        unconfigured_events.append(pending)
        sent = bot.send_message.await_count
        with patch.object(r.LOGGER, "warning") as warning:
            for consumer in (relay, peer, relay):
                assert not await consumer.deliver(binding, event, pending)
                assert bot.send_message.await_count == sent
            warning.assert_called_once()
            assert warning.call_args.args[2:] == ("language", "setup", "invalid_response_or_value")
            assert "secret" not in str(warning.call_args)
            relay.redis.now += r.SETTINGS_LOG_SECONDS
            assert not await relay.deliver(binding, event, pending)
            assert warning.call_count == 2 and bot.send_message.await_count == sent
        assert f"bindings:{r.CONSUMER_GROUP}:{pending['event_id']}" not in relay.redis.values
    language[0] = locale
    for pending in unconfigured_events:
        sent = bot.send_message.await_count
        assert await relay.deliver(binding, event, pending)
        assert bot.send_message.call_args.kwargs["text"] == expected[4] + original
        assert await relay.deliver(binding, event, pending)
        assert bot.send_message.await_count == sent + 1
    settings_status[0] = 404
    assert (await command("/notes", listed)).args[0] == b.LANGUAGE_SETUP
    settings_status[0] = 200

    assert b.SETTINGS_TEMPORARY == (
        "Настройки временно недоступны. Попробуйте ещё раз. / "
        "Settings are temporarily unavailable. Please try again."
    )

    async def settings_failure(state, reason, reply_text):
        """Exercise the same failed read through all three entry points, then recover."""
        # Create a fresh callback context before making settings unavailable.
        status, error, malformed = settings_status[0], settings_error[0], settings_body[0]
        settings_status[0], settings_error[0], settings_body[0] = 200, None, None
        reply = await command("/notes", listed)
        token = reply.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        settings_status[0], settings_error[0], settings_body[0] = status, error, malformed
        reply = await command("/note no mutation")
        assert reply.args[0] == reply_text and len(items) == 1
        callback = update(data=token)
        await runtime.callback(callback, None)
        assert callback.callback_query.edit_message_text.call_args.args[0] == reply_text
        assert items[0]["state"] == "removed"
        if state == "transient":
            assert reply_text not in (b.LANGUAGE_SETUP, b.TIMEZONE_SETUP)
        pending = body()
        sent = bot.send_message.await_count
        key = f"bindings:{r.CONSUMER_GROUP}:{pending['event_id']}"
        with patch.object(r.LOGGER, "warning") as warning:
            for consumer in (relay, peer, relay):
                assert not await consumer.deliver(binding, event, pending)
                assert bot.send_message.await_count == sent
                assert key not in relay.redis.values
            warning.assert_called_once()
            assert warning.call_args.args[1:] == (key, settings_fail_key[0], state, reason)
            assert "secret" not in str(warning.call_args)
        settings_status[0], settings_error[0], settings_body[0] = 200, None, None
        assert await relay.deliver(binding, event, pending)
        assert await relay.deliver(binding, event, pending)
        assert bot.send_message.await_count == sent + 1
        assert bot.send_message.call_args.kwargs == {"chat_id": 123, "text": expected[4] + original}

    for key in ("language", "timezone"):
        timezone = binding["timezone"]
        if key == "timezone":
            binding["timezone"] = {"key": "timezone"}
        settings_fail_key[0] = key
        setup = b.LANGUAGE_SETUP if key == "language" else b.TIMEZONE_SETUP
        try:
            settings_status[0] = 404
            await settings_failure("setup", "http_404", setup)
            for malformed in ({}, [], {"contract_version": 2}, b"not json; secret body"):
                settings_body[0] = malformed
                await settings_failure("setup", "invalid_response_or_value", setup)
            settings_status[0] = 503
            await settings_failure("transient", "http_503", b.SETTINGS_TEMPORARY)
            for error, reason in (
                (httpx.ReadTimeout, "timeout"),
                (httpx.ConnectError, "transport"),
            ):
                settings_error[0] = error
                await settings_failure("transient", reason, b.SETTINGS_TEMPORARY)
        finally:
            binding["timezone"] = timezone
    settings_fail_key[0] = "language"

    v1_bindings = [item for item in b.DATA["bindings"] if item["binding_version"] == 1]
    if v1_bindings:
        v1_binding = v1_bindings[0]
        v1_list = v1_binding["commands"][1]
        u = update("/" + v1_list["command"])
        await runtime.command(v1_binding, v1_list, u, None)
        assert u.message.reply_text.call_args.args[0] == v1_list["on_none"]
        assert requests[-2][1]["key"] == "timezone"

    date_reply = {"parts": [{"source": "$result.at", "format": "month_word"}]}
    displayed = b.render_v2(
        date_reply, {"result": {"at": "2026-10-07T18:00:00Z"}}, ZoneInfo("Europe/Moscow"), locale
    )
    assert displayed == (
        "7 октября 2026 в 21:00 MSK" if locale == "ru" else "7 October 2026 at 21:00 MSK"
    )
    print(f"v2 {locale}: commands, callbacks, settings, date display and relay passed")


if __name__ == "__main__":
    import sys

    asyncio.run(scenario(sys.argv[1]))

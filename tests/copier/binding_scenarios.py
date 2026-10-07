"""Run against a generated bot interpreter, locally and in published-install CI."""

import asyncio
from datetime import datetime
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx


def run_handlers():  # noqa: C901, PLR0915
    from importlib.metadata import distribution

    from services.tg_bot.src.generated import bindings as b
    from services.tg_bot.src.main import BackendClient, build_application, enforce_access

    assert distribution("codegen-kit-textparse").version == "0.1.0"

    os.environ["USER_IDENTITY_CAPABILITY"] = "test-capability-never-in-replies"
    os.environ["BACKEND_API_URL"] = "http://backend.test"
    os.environ["TELEGRAM_BOT_TOKEN"] = "123456:test"  # noqa: S105
    requests = []
    items = []
    setting = {
        "contract_version": 1,
        "key": "timezone",
        "scope": "product",
        "value": "America/New_York",
    }
    failure = {"settings": 0, "action": 0, "malformed": False}

    def respond(request):
        data = json.loads(request.content) if request.content else {}
        requests.append((request, data))
        if request.url.path == "/settings/get":
            assert data == {"contract_version": 1, "key": "timezone", "scope": "product"}
            return httpx.Response(failure["settings"] or 200, json=setting)
        assert request.headers["X-Identity-Capability"] == os.environ["USER_IDENTITY_CAPABILITY"]
        assert request.headers["X-User-Channel"] == "telegram"
        assert request.headers["X-User-External-Id"] == "123"
        assert request.url.path.startswith("/reminders")
        assert "user_ref" not in data
        if failure["action"]:
            return httpx.Response(failure["action"], json={"detail": "do not leak this capability"})
        if failure["malformed"]:
            return httpx.Response(200, json={"bad": "response"})
        if request.method == "POST":
            assert request.url.path == "/reminders"
            assert set(data) == {"text", "remind_at"}
            item = {
                "id": str(uuid4()),
                "user_ref": "telegram:123",
                "state": "scheduled",
                "created_at": "2026-10-07T18:00:00Z",
                **data,
            }
            items.append(item)
            return httpx.Response(200, json=item)
        if request.method == "GET":
            assert request.url.path == "/reminders"
            return httpx.Response(200, json=items)
        assert request.method == "DELETE" and data == {}
        item = next(item for item in items if request.url.path == "/reminders/" + item["id"])
        item["state"] = "cancelled"
        return httpx.Response(200, json=item)

    class FakeClient(BackendClient):
        async def __aenter__(self):
            self._client = httpx.AsyncClient(
                base_url=self.base_url, transport=httpx.MockTransport(respond)
            )
            return self

    def update(text="", user=123, chat=123, data=None):
        query = (
            None
            if data is None
            else SimpleNamespace(
                data=data,
                from_user=SimpleNamespace(id=user),
                answer=AsyncMock(),
                edit_message_text=AsyncMock(),
            )
        )
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=user),
            effective_chat=SimpleNamespace(id=chat),
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
            callback_query=query,
        )

    async def scenario():  # noqa: C901, PLR0915, PLR0912
        now = datetime.fromisoformat("2026-10-07T14:00:00-04:00")
        elapsed = [0.0]
        runtime = b.ChannelBindings(FakeClient, clock=lambda: now, monotonic=lambda: elapsed[0])
        binding = b.DATA["bindings"][0]
        create, listed = binding["commands"]
        context = SimpleNamespace(args=[])
        function = create["parse"]["function"]
        real_parser = b.PARSERS[function]
        parse_calls = []

        def observe(text, **kwargs):
            parse_calls.append((text, kwargs))
            return real_parser(text, **kwargs)

        b.PARSERS[function] = observe

        async def command(text, selected=create):
            u = update(text)
            await runtime.command(binding, selected, u, context)
            return u.message.reply_text.call_args

        reply = await command("/" + create["command"] + " Buy   Milk in 2 minutes")
        assert parse_calls[-1] == (
            "Buy   Milk in 2 minutes",
            {"lang": "en", "now": now, "tz": "America/New_York"},
        )
        assert items[-1]["text"] == "Buy Milk"
        assert datetime.fromisoformat(items[-1]["remind_at"]) == now.replace(minute=2)
        assert "October" in reply.args[0] and "Buy Milk" in reply.args[0]
        assert reply.args[0].startswith(create["reply"]["parts"][0])
        for text in ("/remind", "/remind \t ", "/remind in 2 minutes"):
            before = len(items)
            reply = await command(text)
            assert reply.args[0] == create["on_invalid_text"]
            assert len(items) == before
        original = "Buy   More MILK"
        for index, expected in enumerate(
            (
                now.timestamp() + 300,
                now.timestamp() + 3600,
                datetime.fromisoformat("2026-10-08T09:00:00-04:00").timestamp(),
            )
        ):
            reply = await command("/remind " + original)
            markup = reply.kwargs["reply_markup"]
            assert [button.text for button in markup.inline_keyboard[0]] == [
                p["label"] for p in create["on_empty"]["presets"]
            ]
            data = markup.inline_keyboard[0][index].callback_data
            assert len(data.encode()) <= 64 and original not in data and "telegram:" not in data
            for forged in (
                update(user=124, data=data),
                update(chat=124, data=data),
                update(data="b1:forged:0"),
            ):
                before = len(items)
                await runtime.callback(forged, context)
                assert (
                    len(items) == before
                    and forged.callback_query.answer.call_args.kwargs["show_alert"]
                )
            accepted = update(data=data)
            await asyncio.gather(
                runtime.callback(accepted, context), runtime.callback(update(data=data), context)
            )
            assert items[-1]["text"] == original
            assert datetime.fromisoformat(items[-1]["remind_at"]).timestamp() == expected
            assert "October" in accepted.callback_query.edit_message_text.call_args.args[0]

        for invalid in (404, 503):
            failure["settings"] = invalid
            before = len(items)
            reply = await command("/remind task in 2 minutes")
            assert "timezone" in reply.args[0] and len(items) == before
        failure["settings"] = 0
        for field, value in (
            ("contract_version", True),
            ("contract_version", 1.0),
            ("contract_version", 2),
            ("scope", "user"),
            ("key", "other"),
            ("subject_id", "telegram:123"),
        ):
            original_setting = dict(setting)
            setting[field] = value
            before = len(items)
            reply = await command("/remind task in 2 minutes")
            assert "timezone" in reply.args[0] and len(items) == before
            setting.clear()
            setting.update(original_setting)
        for value in (
            "",
            "Invalid/Zone",
            None,
            "localtime",
            "posixrules",
            "/UTC",
            ".UTC",
            "posix/UTC",
            "right/UTC",
        ):
            setting["value"] = value
            reply = await command("/remind task in 2 minutes")
            assert "timezone" in reply.args[0]
        setting["value"] = "America/New_York"
        for status in (401, 403, 500):
            failure["action"] = status
            reply = await command("/remind task in 2 minutes")
            assert reply.args[0] == b.ERROR_REPLY
        failure["action"] = 0
        failure["malformed"] = True
        assert (await command("/remind task in 2 minutes")).args[0] == b.ERROR_REPLY
        failure["malformed"] = False
        reply = await command("/remind every day at 9 do something")
        assert "reply_markup" in reply.kwargs  # parser refusal is retained, never guessed
        data = reply.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        elapsed[0] += b.CONTEXT_TTL + 1
        before = len(items)
        await runtime.callback(update(data=data), context)
        assert len(items) == before
        fresh = b.ChannelBindings(FakeClient)
        await fresh.callback(update(data=data), context)  # process restart loses pending context

        reply = await command("/reminders", listed)
        button = reply.kwargs["reply_markup"].inline_keyboard[0][0]
        cancelled = update(data=button.callback_data)
        await runtime.callback(cancelled, context)
        assert cancelled.callback_query.edit_message_text.call_args.args[0].startswith("Cancelled:")
        items.clear()
        assert (await command("/reminders", listed)).args[0] == listed["on_none"]
        for dt in ("2026-03-08T01:58:00-05:00", "2026-11-01T01:58:00-04:00"):
            clock = datetime.fromisoformat(dt)
            at = b.preset_at(
                {"kind": "offset", "seconds": 300}, clock, ZoneInfo("America/New_York")
            )
            assert datetime.fromisoformat(at).timestamp() == clock.timestamp() + 300
        try:
            b.preset_at(
                {"kind": "wall_time", "days": 1, "time": "09:00"},
                datetime.fromisoformat("2011-12-29T12:00:00-10:00"),
                ZoneInfo("Pacific/Apia"),
            )
        except b.BindingRuntimeError:
            pass
        else:
            raise AssertionError("nonexistent tomorrow 09:00 admitted")
        assert len(b.bounded("😀" * 4000).encode("utf-16-le")) <= 8000
        # Same wall-time helper refuses a fold/gap rather than selecting an instant.
        for dt, wall in (
            ("2026-03-07T12:00:00-05:00", "02:30"),
            ("2026-10-31T12:00:00-04:00", "01:30"),
        ):
            try:
                b.preset_at(
                    {"kind": "wall_time", "days": 1, "time": wall},
                    datetime.fromisoformat(dt),
                    ZoneInfo("America/New_York"),
                )
            except b.BindingRuntimeError:
                pass
            else:
                raise AssertionError("DST ambiguity admitted")
        for _ in range(b.MAX_CONTEXTS + 1):
            runtime.remember(update(), binding, [{"call": {"label": "test"}, "contexts": {}}])
        assert len(runtime.pending) == b.MAX_CONTEXTS
        application = build_application()
        assert application.handlers[-1][0].callback is enforce_access
        names = {
            name
            for handler in application.handlers[0]
            if hasattr(handler, "commands")
            for name in handler.commands
        }
        assert {"start", "command", create["command"], listed["command"]} <= names
        assert requests
        return {
            "handler_import": True,
            "real_parser": True,
            "library_version": distribution("codegen-kit-textparse").version,
            "create_list_cancel": True,
            "caller_headers": True,
            "timezone_failures": True,
            "callback_bounds": True,
            "command": create["command"],
            "reply": reply.args[0],
        }

    return asyncio.run(scenario())


if __name__ == "__main__":
    print(json.dumps(run_handlers(), sort_keys=True))

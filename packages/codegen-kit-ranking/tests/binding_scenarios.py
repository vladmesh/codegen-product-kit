"""Execute the generated /top command in a real bound product, with offline transport."""
# ruff: noqa: PLR2004

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

ITEMS = [
    {
        "channel": "cyproplan",
        "date": "2026-10-07T21:00:00+00:00",
        "text": "Post 2",
        "url": "https://t.me/cyproplan/2",
        "views": "9000",
        "score": 0.98,
    },
    {
        "channel": "cyproplan",
        "date": "2026-10-05T21:00:00+00:00",
        "text": "Post 3",
        "url": "https://t.me/cyproplan/3",
        "views": "?",
        "score": 0.25,
    },
]


def update(text):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=123),
        message=SimpleNamespace(text=text, reply_text=AsyncMock()),
        callback_query=None,
    )


async def scenario(locale):
    from services.tg_bot.src.generated import bindings as b
    from services.tg_bot.src.main import BackendClient

    os.environ["USER_IDENTITY_CAPABILITY"] = "test-capability"
    os.environ["BACKEND_API_URL"] = "http://backend.test"
    items = []
    failure = [None]
    requests = []

    def respond(request):
        body = json.loads(request.content) if request.content else {}
        requests.append(request)
        if request.url.path == "/settings/get":
            assert body == {"contract_version": 1, "key": "language", "scope": "product"}
            return httpx.Response(200, json=body | {"value": locale})
        assert (request.method, request.url.path) == ("GET", "/ranking/top")
        assert request.headers["X-Identity-Capability"] == "test-capability"
        assert request.headers["X-User-Channel"] == "telegram"
        assert request.headers["X-User-External-Id"] == "123"
        if failure[0]:
            return httpx.Response(409, json={"detail": {"code": failure[0], "secret": "redacted"}})
        return httpx.Response(200, json=items)

    class FakeClient(BackendClient):
        async def __aenter__(self):
            self.initial_delay = 0
            self._client = httpx.AsyncClient(
                base_url=self.base_url, transport=httpx.MockTransport(respond)
            )
            return self

    packages = sorted(binding["package"] for binding in b.DATA["bindings"])
    assert packages == ["ranking", "tg-channels"], packages
    (binding,) = [item for item in b.DATA["bindings"] if item["package"] == "ranking"]
    assert binding["binding_version"] == 2
    (top,) = binding["commands"]
    assert (top["kind"], top["command"]) == ("show", "top")
    runtime = b.ChannelBindings(FakeClient)

    async def command(text):
        incoming = update(text)
        await runtime.command(binding, top, incoming, None)
        return incoming.message.reply_text.call_args_list

    (reply,) = await command("/top")
    assert reply.args[0] == top["on_none"][locale]
    before = len(requests)
    (reply,) = await command("/top extra")
    assert reply.args[0] == top["help"][locale] and len(requests) == before + 1
    items.extend(ITEMS)
    replies = await command("/top")
    views = "Просмотры" if locale == "ru" else "Views"
    assert [call.args[0] for call in replies] == [
        f"@cyproplan\n2026-10-07T21:00:00+00:00\n{views}: 9000\nPost 2\nhttps://t.me/cyproplan/2",
        f"@cyproplan\n2026-10-05T21:00:00+00:00\n{views}: ?\nPost 3\nhttps://t.me/cyproplan/3",
    ]
    for code in ("not_configured", "try_later"):
        failure[0] = code
        (reply,) = await command("/top")
        assert "redacted" not in reply.args[0] and code not in reply.args[0]
    print(f"ranking {locale}: /top, help, items and failures passed")


if __name__ == "__main__":
    import sys

    asyncio.run(scenario(sys.argv[1]))

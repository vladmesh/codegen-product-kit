"""Offline behavioural proof, including real store methods over transactional fake SQL."""
# ruff: noqa: PLR2004

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from test_client_contract import KEY, NOW, channel, page, post


class MemorySession:
    """Interpret the store's SQL statements, retaining transaction and uniqueness behaviour."""

    def __init__(self):
        self.users = set()
        self.subscriptions = set()
        self.seeds = None
        self.seen = set()
        self.deliveries = {}
        self.state = {"cursor": None, "since_at": None, "retry_at": None, "stopped": False}
        self.statements = []

    async def execute(self, query, args=None):  # noqa: C901, PLR0912
        args = args or {}
        self.statements.append(query)
        rows = []
        scalar = None
        if query.startswith("SELECT * FROM poll_state"):
            rows = [SimpleNamespace(_mapping=deepcopy(self.state))]
        elif query.startswith("INSERT INTO users"):
            if args["user_ref"] not in self.users:
                self.users.add(args["user_ref"])
                scalar = args["user_ref"]
        elif query.startswith("SELECT channels FROM starting_list"):
            scalar = self.seeds
        elif query.startswith("INSERT INTO tg_channels.starting_list"):
            if self.seeds is None:
                self.seeds = json.loads(args["channels"])
        elif query.startswith("SELECT channel, user_ref"):
            rows = [SimpleNamespace(channel=c, user_ref=u) for u, c in self.subscriptions]
        elif query.startswith("INSERT INTO subscriptions"):
            self.subscriptions.add((args["user_ref"], args["channel"]))
        elif query.startswith("SELECT channel FROM subscriptions"):
            rows = sorted(c for u, c in self.subscriptions if u == args["user_ref"])
        elif query.startswith("DELETE FROM subscriptions"):
            self.subscriptions.discard((args["user_ref"], args["channel"]))
        elif query.startswith("INSERT INTO seen_posts"):
            identity = (args["channel"], args["post_id"])
            if identity not in self.seen:
                self.seen.add(identity)
                scalar = args["post_id"]
        elif query.startswith("INSERT INTO deliveries"):
            self.deliveries.setdefault(
                args["event_id"],
                SimpleNamespace(
                    event_id=args["event_id"],
                    payload=json.loads(args["payload"]),
                    occurred_at=args["at"],
                    emitted_at=None,
                ),
            )
        elif query.startswith("UPDATE poll_state"):
            self.state = deepcopy(args)
        elif query.startswith("SELECT event_id, payload"):
            rows = [r for r in self.deliveries.values() if r.emitted_at is None]
        elif query.startswith("UPDATE deliveries SET"):
            self.deliveries[args["event_id"]].emitted_at = args["at"]
        else:
            raise AssertionError(query)
        return SimpleNamespace(
            all=lambda: rows, one=lambda: rows[0], scalars=lambda: rows, scalar=lambda: scalar
        )

    async def scalar(self, query, args=None):
        return (await self.execute(query, args)).scalar()


def store(modules):
    memory = MemorySession()
    result = modules.store.Store()

    @asynccontextmanager
    async def transaction():
        snapshot = deepcopy(vars(memory))
        try:
            yield memory
        except BaseException:
            vars(memory).update(snapshot)
            raise

    result.transaction = transaction
    return result, memory


@pytest.mark.parametrize(
    "text",
    [
        " cyproplan ",
        "  @CyproPlan\t",
        "t.me/cyproplan",
        "https://t.me/cyproplan",
        "http://t.me/cyproplan",
    ],
)
def test_add_normalizes_then_checks_and_owns_list(modules, text):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=channel())

    async def exercise():
        repository, memory = store(modules)
        client = modules.client.ReaderClient(
            "https://service.test", KEY, transport=httpx.MockTransport(respond)
        )
        service = modules.service.ChannelsService(repository, client)
        assert (await service.add("telegram:123", text)).channel == "cyproplan"
        assert [item.channel for item in await service.list("telegram:123")] == ["cyproplan"]
        assert await service.list("telegram:456") == []
        await service.remove("telegram:456", "cyproplan")
        assert memory.subscriptions == {("telegram:123", "cyproplan")}
        await service.remove("telegram:123", "cyproplan")
        assert await service.list("telegram:123") == []
        await client.close()

    asyncio.run(exercise())
    assert len(calls) == 1 and calls[0].url.path.endswith("/cyproplan")


@pytest.mark.parametrize(
    "text",
    [
        " ",
        "ab",
        "@123bad",
        "t.me/",
        "https://evil.test/name",
        "t.me/name/123",
        "t.me/name?start=x",
        "name space",
        "x" * 33,
    ],
)
def test_invalid_username_never_resolves(modules, text):
    repository, memory = store(modules)
    client = SimpleNamespace(resolve=AsyncMock())
    service = modules.service.ChannelsService(repository, client)
    with pytest.raises(modules.service.ActionError, match="invalid_username"):
        asyncio.run(service.add("telegram:123", text))
    client.resolve.assert_not_awaited()
    assert not memory.users


@pytest.mark.parametrize(
    "status,code,accepted",
    [
        ("pending", "pending", True),
        ("not_found", "not_found", False),
        ("not_a_channel", "not_a_channel", False),
        ("no_preview", "no_preview", False),
        ("error", "try_later", False),
        (429, "try_later", False),
        (401, "not_configured", False),
        (403, "not_configured", False),
        (503, "try_later", False),
    ],
)
def test_add_each_declared_failure_and_pending(modules, status, code, accepted):
    async def exercise():
        repository, memory = store(modules)
        client = modules.client.ReaderClient(
            "https://service.test",
            KEY,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    status if isinstance(status, int) else 200,
                    json={"detail": KEY} if isinstance(status, int) else channel(status),
                )
            ),
        )
        service = modules.service.ChannelsService(repository, client)
        with pytest.raises(modules.service.ActionError) as caught:
            await service.add("telegram:123", "@cyproplan")
        assert caught.value.code == code and KEY not in repr(caught.value)
        assert bool(memory.subscriptions) is accepted
        await client.close()

    asyncio.run(exercise())


def test_cyprus_seed_initializes_each_user_once_and_is_package_scoped(modules):
    async def exercise():
        repository, memory = store(modules)
        seeds = ["cyproplan", "kipr_podslushano_limasol"]
        await repository.seed(memory, "tg_channels.starting_channels", seeds)
        assert await repository.list("telegram:123") == sorted(seeds)
        await repository.remove("telegram:123", "cyproplan")
        assert await repository.list("telegram:123") == ["kipr_podslushano_limasol"]
        assert await repository.list("telegram:456") == sorted(seeds)
        await repository.seed(memory, "tg_channels.starting_channels", ["otherchannel"])
        assert await repository.list("telegram:789") == sorted(seeds)
        assert memory.statements[0].startswith("INSERT INTO tg_channels.starting_list")

    asyncio.run(exercise())


def test_product_union_quota_rolls_back_new_user_initialization(modules):
    async def exercise():
        repository, memory = store(modules)
        for index in range(50):
            await repository.add("telegram:123", f"channel{index}")
        await repository.add("telegram:456", "channel0")
        with pytest.raises(modules.service.ActionError, match="try_later"):
            await repository.add("telegram:789", "anotherchannel")
        assert "telegram:789" not in memory.users
        assert len(await repository.subscriptions(memory)) == 50

    asyncio.run(exercise())


def test_digest_latest_trimmed_links_fold_edits_and_deletions_without_product_cursor(modules):
    requests = []
    replies = [
        page([post(), post(id=8, seq=2), post(id=9, seq=3)], has_more=True),
        page(
            [
                post(seq=4, edited=True, text="   revised   "),
                post(id=8, seq=5, deleted=True, text=""),
                post(id=10, seq=6, text="x" * 4000),
            ],
            next_cursor="digest-end",
        ),
    ]

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=replies[len(requests) - 1])

    async def exercise():
        repository, memory = store(modules)
        await repository.add("telegram:123", "cyproplan")
        memory.state["cursor"] = "product-cursor"
        client = modules.client.ReaderClient(
            "https://service.test", KEY, transport=httpx.MockTransport(respond)
        )
        service = modules.service.ChannelsService(repository, client)
        assert await service.digest("telegram:456") == []
        digest = await service.digest("telegram:123")
        assert [item.url for item in digest] == [
            "https://t.me/cyproplan/10",
            "https://t.me/cyproplan/9",
            "https://t.me/cyproplan/7",
        ]
        assert digest[-1].text == "revised" and len(digest[0].text) == 2500
        assert all(
            item.channel == "cyproplan" and item.date.startswith("2026-10-07") for item in digest
        )
        assert memory.state["cursor"] == "product-cursor"
        assert not memory.deliveries
        await client.close()

    asyncio.run(exercise())
    assert requests[1].url.params["cursor"] == "opaque-next"


def test_poll_union_recipient_dedup_edits_tombstones_and_recovery(modules):
    requests = []
    replies = [
        page([post(), post(id=8, seq=2, edited=True), post(id=9, seq=3, deleted=True)]),
        page(
            [post(seq=4, edited=True), post(id=8, seq=5), post(id=9, seq=6), post(id=10, seq=7)],
            next_cursor="second-page",
        ),
    ]

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=replies[len(requests) - 1])

    async def exercise():
        repository, memory = store(modules)
        for user in ("telegram:123", "telegram:456"):
            await repository.add(user, "cyproplan")
        await repository.add("telegram:456", "kipr_podslushano_limasol")
        client = modules.client.ReaderClient(
            "https://service.test", KEY, transport=httpx.MockTransport(respond)
        )
        publish = AsyncMock(side_effect=RuntimeError("publisher crash"))
        poller = modules.polling.Poller(repository, client, publish)
        at = datetime.fromisoformat(NOW.replace("Z", "+00:00"))
        with pytest.raises(RuntimeError, match="publisher crash"):
            await poller.tick(at)
        assert memory.state["cursor"] == "opaque-next"
        assert len(memory.deliveries) == 2
        original_id = publish.call_args.kwargs["event_id"]
        publish.side_effect = None
        # A fresh poller uses committed cursor/outbox, not in-memory delivery state.
        await modules.polling.Poller(repository, client, publish).tick(at + timedelta(minutes=1))
        assert original_id in [call.kwargs["event_id"] for call in publish.call_args_list[1:]]
        assert len(memory.deliveries) == 4
        assert {r.payload["user_ref"] for r in memory.deliveries.values()} == {
            "telegram:123",
            "telegram:456",
        }
        assert {r.payload["url"] for r in memory.deliveries.values()} == {
            "https://t.me/cyproplan/7",
            "https://t.me/cyproplan/10",
        }
        assert all(row.emitted_at for row in memory.deliveries.values())
        await client.close()

    asyncio.run(exercise())
    assert requests[0].url.params["channels"] == "cyproplan,kipr_podslushano_limasol"
    assert requests[1].url.params["cursor"] == "opaque-next"


def test_409_restarts_from_since_without_redelivery(modules):
    requests = []
    replies = [
        httpx.Response(409, json={"detail": KEY}),
        httpx.Response(200, json=page([post()], next_cursor="reset")),
    ]

    def respond(request):
        requests.append(request)
        return replies[len(requests) - 1]

    async def exercise():
        repository, memory = store(modules)
        await repository.add("telegram:123", "cyproplan")
        memory.state.update(cursor="old-set", since_at=datetime.now(UTC) - timedelta(days=1))
        memory.seen.add(("cyproplan", 7))
        client = modules.client.ReaderClient(
            "https://service.test", KEY, transport=httpx.MockTransport(respond)
        )
        await modules.polling.Poller(repository, client, AsyncMock()).tick(datetime.now(UTC))
        assert memory.state["cursor"] == "reset" and not memory.deliveries
        await client.close()

    asyncio.run(exercise())
    assert requests[0].url.params["cursor"] == "old-set"
    assert "since" in requests[1].url.params and "cursor" not in requests[1].url.params


@pytest.mark.parametrize("status", [429, 401, 403])
def test_poll_rate_limit_preserves_cursor_or_auth_stops_without_key(modules, caplog, status):
    caplog.set_level(logging.DEBUG)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"detail": KEY}, headers={"Retry-After": "120"})

    async def exercise():
        repository, memory = store(modules)
        await repository.add("telegram:123", "cyproplan")
        memory.state["cursor"] = "retained"
        client = modules.client.ReaderClient(
            "https://service.test", KEY, transport=httpx.MockTransport(respond)
        )
        poller = modules.polling.Poller(repository, client, AsyncMock())
        at = datetime.now(UTC)
        await poller.tick(at)
        assert memory.state["cursor"] == "retained"
        await poller.tick(at + timedelta(seconds=60))
        assert len(requests) == 1
        if status == 429:
            assert memory.state["retry_at"] == at + timedelta(seconds=120)
            await poller.tick(at + timedelta(seconds=120))
            assert len(requests) == 2
        else:
            assert memory.state["stopped"]
            await poller.tick(at + timedelta(days=1))
            assert len(requests) == 1
            assert "Channel polling stopped: service not configured" in caplog.text
        await client.close()

    asyncio.run(exercise())
    assert KEY not in caplog.text

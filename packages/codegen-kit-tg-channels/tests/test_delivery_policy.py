"""Real date/cursor filtering with first-seen, history and per-user eligibility regressions."""
# ruff: noqa: PLR2004

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from test_behaviour import filter_since, store
from test_client_contract import KEY, page, post

AT = datetime(2026, 10, 7, 21, tzinfo=UTC)
CYPRUS = ["cyproplan", "kipr_podslushano_limasol"]


class ChangeFeed:
    """Current versions in seq order, with cursor channel-set checks and date filtering."""

    def __init__(self):
        self.versions = {}
        self.cursors = {}
        self.requests = []
        self.statuses = []

    def put(self, *, at, **fields):
        version = post(date=at.isoformat(), **fields)
        self.versions[version["channel"], version["id"]] = version

    def respond(self, request):
        self.requests.append(request)
        channels = tuple(request.url.params["channels"].split(","))
        cursor = request.url.params.get("cursor")
        if cursor is not None:
            original, seq = self.cursors[cursor]
            if original != channels:
                self.statuses.append(409)
                return httpx.Response(409, json={"detail": "channel set changed"})
        else:
            seq = 0
        versions = sorted(
            (
                version
                for version in self.versions.values()
                if version["channel"] in channels and version["seq"] > seq
            ),
            key=lambda version: version["seq"],
        )
        body = filter_since(page(versions), request)
        token = f"cursor-{len(self.requests)}"
        self.cursors[token] = (channels, max([seq, *(v["seq"] for v in body["items"])]))
        body["next_cursor"] = token
        body["channels"] = [{"channel": channel, "status": "ok"} for channel in channels]
        self.statuses.append(200)
        return httpx.Response(200, json=body)


def harness(modules, monkeypatch):
    repository, memory = store(modules)
    clock = [AT]
    monkeypatch.setattr(modules.store, "datetime", SimpleNamespace(now=lambda tz: clock[0]))
    feed = ChangeFeed()
    client = modules.client.ReaderClient(
        "https://service.test", KEY, transport=httpx.MockTransport(feed.respond)
    )
    publish = AsyncMock()
    return SimpleNamespace(
        repository=repository,
        memory=memory,
        clock=clock,
        feed=feed,
        client=client,
        publish=publish,
        poller=modules.polling.Poller(repository, client, publish),
    )


def recipients(context):
    return {
        (row.payload["url"], row.payload["user_ref"]) for row in context.memory.deliveries.values()
    }


def test_first_seen_edited_post_is_delivered_once(modules, monkeypatch):
    async def exercise():
        context = harness(modules, monkeypatch)
        await context.repository.add("telegram:123", "cyproplan")
        context.feed.put(at=AT, edited=True)
        await context.poller.tick(AT)
        context.publish.assert_awaited_once()
        assert recipients(context) == {("https://t.me/cyproplan/7", "telegram:123")}
        context.feed.put(at=AT, seq=2, edited=True, text="Corrected typo")
        await context.poller.tick(AT + timedelta(minutes=1))
        context.publish.assert_awaited_once()
        assert context.memory.seen == {("cyproplan", 7)}
        await context.client.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("deleted", [False, True])
def test_later_edit_or_tombstone_never_redelivers(modules, monkeypatch, deleted):
    async def exercise():
        context = harness(modules, monkeypatch)
        await context.repository.add("telegram:123", "cyproplan")
        context.feed.put(at=AT)
        await context.poller.tick(AT)
        context.feed.put(at=AT, seq=2, edited=True, deleted=deleted)
        await context.poller.tick(AT + timedelta(minutes=1))
        context.publish.assert_awaited_once()
        await context.client.close()

    asyncio.run(exercise())


def test_first_seen_tombstone_is_marked_seen_silently(modules, monkeypatch):
    async def exercise():
        context = harness(modules, monkeypatch)
        await context.repository.add("telegram:123", "cyproplan")
        context.feed.put(at=AT, deleted=True, text="")
        await context.poller.tick(AT)
        context.publish.assert_not_awaited()
        assert context.memory.seen == {("cyproplan", 7)}
        context.feed.put(at=AT, seq=2, deleted=False)
        await context.poller.tick(AT + timedelta(minutes=1))
        context.publish.assert_not_awaited()
        await context.client.close()

    asyncio.run(exercise())


def test_seeded_first_poll_marks_72_hour_history_then_delivers_fresh_post(modules, monkeypatch):
    async def exercise():
        context = harness(modules, monkeypatch)
        await context.repository.seed(context.memory, "tg_channels.starting_channels", CYPRUS)
        assert await context.repository.list("telegram:123") == sorted(CYPRUS)
        for number, channel in enumerate(CYPRUS):
            for hour in range(1, 73):
                context.feed.put(
                    at=AT - timedelta(hours=hour), channel=channel, id=hour, seq=number * 72 + hour
                )
        await context.poller.tick(AT)
        context.publish.assert_not_awaited()
        assert len(context.memory.seen) == 144
        assert datetime.fromisoformat(
            context.feed.requests[0].url.params["since"]
        ) == AT - timedelta(hours=72)
        assert context.memory.state["since_at"] == AT - timedelta(hours=24)
        fresh = AT + timedelta(minutes=1)
        context.feed.put(at=fresh, id=999, seq=145)
        await context.poller.tick(fresh)
        context.publish.assert_awaited_once()
        assert recipients(context) == {("https://t.me/cyproplan/999", "telegram:123")}
        assert context.memory.state["since_at"] == fresh - timedelta(hours=24)
        await context.client.close()

    asyncio.run(exercise())


def test_new_channel_history_after_409_is_silent_then_fresh_post_delivers(modules, monkeypatch):
    async def exercise():
        context = harness(modules, monkeypatch)
        await context.repository.add("telegram:123", "cyproplan")
        context.feed.put(at=AT - timedelta(hours=2), id=1)
        await context.poller.tick(AT)
        initial_cursor = context.memory.state["cursor"]
        later = AT + timedelta(days=20)
        context.clock[0] = later
        await context.repository.add("telegram:123", "kipr_podslushano_limasol")
        for index in range(30):
            context.feed.put(
                at=later - timedelta(minutes=30 * (index + 1)),
                channel="kipr_podslushano_limasol",
                id=index + 1,
                seq=index + 2,
            )
        # A retained post outside the bounded restart window must not be returned by the fake.
        context.feed.put(
            at=later - timedelta(days=2), channel="kipr_podslushano_limasol", id=100, seq=32
        )
        await context.poller.tick(later)
        context.publish.assert_not_awaited()
        assert context.feed.statuses[-2:] == [409, 200]
        assert context.feed.requests[-2].url.params["cursor"] == initial_cursor
        assert datetime.fromisoformat(
            context.feed.requests[-1].url.params["since"]
        ) == later - timedelta(hours=24)
        assert len(context.memory.seen) == 31
        assert ("kipr_podslushano_limasol", 100) not in context.memory.seen
        assert context.memory.state["since_at"] == later - timedelta(hours=24)
        fresh = later + timedelta(minutes=1)
        context.feed.put(at=fresh, channel="kipr_podslushano_limasol", id=999, seq=33)
        await context.poller.tick(fresh)
        context.publish.assert_awaited_once()
        assert recipients(context) == {
            ("https://t.me/kipr_podslushano_limasol/999", "telegram:123")
        }
        await context.client.close()

    asyncio.run(exercise())


def test_each_user_has_their_own_subscription_threshold(modules, monkeypatch):
    async def exercise():
        context = harness(modules, monkeypatch)
        await context.repository.add("telegram:123", "cyproplan")
        later = AT + timedelta(hours=2)
        context.clock[0] = later
        await context.repository.add("telegram:456", "cyproplan")
        context.feed.put(at=AT - timedelta(hours=1), id=1, seq=1)
        context.feed.put(at=AT + timedelta(hours=1), id=2, seq=2, edited=True)
        context.feed.put(at=later + timedelta(minutes=1), id=3, seq=3)
        await context.poller.tick(later + timedelta(minutes=1))
        assert recipients(context) == {
            ("https://t.me/cyproplan/2", "telegram:123"),
            ("https://t.me/cyproplan/3", "telegram:123"),
            ("https://t.me/cyproplan/3", "telegram:456"),
        }
        assert context.publish.await_count == 3 and len(context.memory.seen) == 3
        await context.client.close()

    asyncio.run(exercise())


def test_subscription_slack_and_readding_channel_timestamp(modules, monkeypatch):
    async def exercise():
        context = harness(modules, monkeypatch)
        await context.repository.add("telegram:123", "cyproplan")
        context.clock[0] = AT + timedelta(minutes=1)
        await context.repository.add("telegram:123", "cyproplan")
        assert context.memory.subscribed_at["telegram:123", "cyproplan"] == AT
        context.feed.put(at=AT - timedelta(minutes=10, seconds=1), id=1, seq=1)
        context.feed.put(at=AT - timedelta(minutes=10), id=2, seq=2)
        await context.poller.tick(AT + timedelta(minutes=1))
        assert recipients(context) == {("https://t.me/cyproplan/2", "telegram:123")}
        await context.repository.remove("telegram:123", "cyproplan")
        context.clock[0] = AT + timedelta(hours=1)
        await context.repository.add("telegram:123", "cyproplan")
        assert context.memory.subscribed_at["telegram:123", "cyproplan"] == context.clock[0]
        context.feed.put(at=AT + timedelta(minutes=2), id=3, seq=3)
        await context.poller.tick(context.clock[0])
        context.publish.assert_awaited_once()
        assert len(context.memory.seen) == 3
        await context.client.close()

    asyncio.run(exercise())

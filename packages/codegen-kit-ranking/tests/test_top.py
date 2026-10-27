"""`top` ranks the verified caller's provider posts under the seeded product settings."""
# ruff: noqa: PLR2004

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import codegen_kit_ranking
from codegen_kit_ranking import api
from conftest import CALLER, CONFIRMED_WEIGHTS, PROVIDER, SettingsTable
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

OK = 200
CONFLICT = 409
SEEDED = {"weights": CONFIRMED_WEIGHTS, "keywords": [], "top_count": 10}


@pytest.fixture
def table(monkeypatch: pytest.MonkeyPatch) -> SettingsTable:
    memory = SettingsTable()
    monkeypatch.setattr(api.store, "transaction", memory.session)
    PROVIDER.posts, PROVIDER.error, PROVIDER.callers = [], None, []
    return memory


@pytest.fixture
def client() -> TestClient:
    application = FastAPI()
    application.include_router(codegen_kit_ranking.package.router, prefix="/ranking")
    return TestClient(application)


def seed(table: SettingsTable, **values: Any) -> None:
    async def write() -> None:
        for name, value in values.items():
            await codegen_kit_ranking.package.seed_setting(table, f"ranking.{name}", value)

    asyncio.run(write())


def channel_post(number: int, *, hours_old: float, views: int | None, text: str = "News") -> Any:
    date = datetime.now(UTC) - timedelta(hours=hours_old)
    return SimpleNamespace(
        channel="cyproplan",
        date=date.isoformat(),
        text=text,
        url=f"https://t.me/cyproplan/{number}",
        views=views,
    )


def test_top_ranks_the_callers_posts_by_views_and_freshness(
    table: SettingsTable, client: TestClient
) -> None:
    seed(table, **SEEDED)
    PROVIDER.posts = [
        channel_post(1, hours_old=1, views=100),
        channel_post(2, hours_old=1, views=5000),
        # Without views only freshness counts: one half-life old, it scores 0.5.
        channel_post(3, hours_old=24, views=None),
    ]

    response = client.get("/ranking/top")

    assert response.status_code == OK
    items = response.json()
    assert [item["url"] for item in items] == [
        "https://t.me/cyproplan/2",
        "https://t.me/cyproplan/3",
        "https://t.me/cyproplan/1",
    ]
    assert [item["views"] for item in items] == ["5000", "?", "100"]
    assert items[0]["score"] > items[1]["score"] > items[2]["score"]
    assert set(items[0]) == {"channel", "date", "text", "url", "views", "score"}
    assert items[0]["date"] == PROVIDER.posts[1].date
    assert PROVIDER.callers == [CALLER]


def test_top_returns_at_most_the_seeded_count_and_follows_later_writes(
    table: SettingsTable, client: TestClient
) -> None:
    seed(table, **SEEDED)
    PROVIDER.posts = [channel_post(n, hours_old=n, views=n * 10) for n in range(1, 21)]
    assert len(client.get("/ranking/top").json()) == 10

    seed(table, top_count=3, keywords=["(акция)"])
    seed(table, weights={"views": 0, "reactions": 0, "forwards": 0, "freshness": 0, "keywords": 1})
    PROVIDER.posts.append(channel_post(99, hours_old=30, views=1, text="Скидка (АКЦИЯ) сегодня"))

    items = client.get("/ranking/top").json()
    assert len(items) == 3
    assert items[0]["url"] == "https://t.me/cyproplan/99"
    assert table.values() == {
        "weights": {"views": 0, "reactions": 0, "forwards": 0, "freshness": 0, "keywords": 1},
        "keywords": ["(акция)"],
        "top_count": 3,
    }


def test_top_without_posts_is_an_empty_list(table: SettingsTable, client: TestClient) -> None:
    seed(table, **SEEDED)

    response = client.get("/ranking/top")

    assert response.status_code == OK
    assert response.json() == []


@pytest.mark.parametrize("written", [{}, {"weights": CONFIRMED_WEIGHTS, "keywords": []}])
def test_top_without_every_seeded_setting_is_not_configured(
    table: SettingsTable, client: TestClient, written: dict[str, Any]
) -> None:
    seed(table, **written)
    PROVIDER.posts = [channel_post(1, hours_old=1, views=1)]

    response = client.get("/ranking/top")

    assert response.status_code == CONFLICT
    assert response.json() == {"detail": {"code": "not_configured"}}
    assert PROVIDER.callers == []


def test_top_under_all_zero_weights_is_not_configured(
    table: SettingsTable, client: TestClient
) -> None:
    zero = dict.fromkeys(CONFIRMED_WEIGHTS, 0)
    seed(table, **(SEEDED | {"weights": zero}))
    PROVIDER.posts = [channel_post(1, hours_old=1, views=1)]

    assert client.get("/ranking/top").json() == {"detail": {"code": "not_configured"}}


@pytest.mark.parametrize(
    ("provided", "answered"),
    [
        ("not_configured", "not_configured"),
        ("try_later", "try_later"),
        ("invalid_username", "try_later"),
    ],
)
def test_top_answers_provider_failures_with_declared_codes(
    table: SettingsTable, client: TestClient, provided: str, answered: str
) -> None:
    seed(table, **SEEDED)
    PROVIDER.error = provided

    response = client.get("/ranking/top")

    assert response.status_code == CONFLICT
    assert response.json() == {"detail": {"code": answered}}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ranking.weights", {**CONFIRMED_WEIGHTS, "views": 101}),
        ("ranking.weights", {**CONFIRMED_WEIGHTS, "views": "35"}),
        ("ranking.keywords", ["k" * 101]),
        ("ranking.keywords", [f"k{i}" for i in range(51)]),
        ("ranking.top_count", 0),
        ("ranking.top_count", 51),
        ("ranking.top_count", True),
        ("ranking.other", 1),
    ],
)
def test_a_seed_outside_the_setting_contract_is_refused_and_not_stored(
    table: SettingsTable, key: str, value: Any
) -> None:
    async def write() -> None:
        await codegen_kit_ranking.package.seed_setting(table, key, value)

    with pytest.raises((ValueError, ValidationError)):
        asyncio.run(write())
    assert table.rows == {}

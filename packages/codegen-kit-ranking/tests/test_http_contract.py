"""The rank route as the core mounts it under the manifest prefix, including its input bounds."""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any

import codegen_kit_ranking
from conftest import CONFIRMED_KEYWORDS, CONFIRMED_WEIGHTS, KEYWORD_WEIGHTS, NOW
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

OK = 200
UNPROCESSABLE = 422
PREFIX = "/ranking"


@pytest.fixture(scope="module")
def client() -> TestClient:
    application = FastAPI()
    application.include_router(codegen_kit_ranking.package.router, prefix=PREFIX)
    return TestClient(application)


def post(identifier: str, hours_old: int = 0, **fields: Any) -> dict[str, Any]:
    return {
        "id": identifier,
        "date": (NOW - timedelta(hours=hours_old)).isoformat(),
        **fields,
    }


def body(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "publications": [
            post("quiet", views=1, reactions=1, forwards=1),
            post("loud", views=100, reactions=100, forwards=100),
        ],
        "settings": {"weights": CONFIRMED_WEIGHTS, "keywords": []},
        "now": NOW.isoformat(),
    }
    payload.update(overrides)
    return payload


def test_ranking_returns_the_ordered_top(client: TestClient) -> None:
    response = client.post(f"{PREFIX}/rank", json=body())

    assert response.status_code == OK
    top = response.json()["top"]
    assert [item["publication"]["id"] for item in top] == ["loud", "quiet"]
    assert top[0]["score"] > top[1]["score"]


def test_ranking_honours_the_requested_size(client: TestClient) -> None:
    response = client.post(f"{PREFIX}/rank", json=body(limit=1))

    assert response.status_code == OK
    assert [item["publication"]["id"] for item in response.json()["top"]] == ["loud"]


def test_keywords_reach_the_ranking_through_the_settings_payload(client: TestClient) -> None:
    response = client.post(
        f"{PREFIX}/rank",
        json=body(
            publications=[
                post("matching", views=10, text="ИИ и стартапы"),
                post("other", views=10, text="Погода"),
            ],
            settings={"weights": KEYWORD_WEIGHTS, "keywords": list(CONFIRMED_KEYWORDS)},
        ),
    )

    assert response.status_code == OK
    top = response.json()["top"]
    assert [item["publication"]["id"] for item in top] == ["matching", "other"]
    assert "keywords" in top[0]["contributions"]


def test_the_same_publication_twice_is_answered_with_a_clarification(client: TestClient) -> None:
    response = client.post(
        f"{PREFIX}/rank",
        json=body(publications=[post("same", views=1), post("same", views=2)]),
    )

    assert response.status_code == UNPROCESSABLE
    detail = response.json()["detail"]
    assert detail["code"] == "duplicate_publication"
    assert detail["field"] == "publications"
    assert detail["clarify"]


def test_the_largest_admitted_request_is_ranked(client: TestClient) -> None:
    largest = post("i" * 200, views=10**12, text="x" * 10000, channel="c" * 64, url="u" * 2048)
    publications = [largest, *(post(f"p{index}") for index in range(999))]
    response = client.post(
        f"{PREFIX}/rank",
        json=body(
            publications=publications,
            settings={"weights": KEYWORD_WEIGHTS, "keywords": ["k" * 100] * 50},
            freshness_half_life_hours=8760,
            limit=1,
        ),
    )

    assert response.status_code == OK


def _publications(**fields: Any) -> dict[str, Any]:
    return {"publications": [post("post", **fields)]}


def _keywords(keywords: list[str]) -> dict[str, Any]:
    return {"settings": {"weights": KEYWORD_WEIGHTS, "keywords": keywords}}


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {"publications": [post(f"p{i}") for i in range(1001)]}, id="1001-publications"
        ),
        pytest.param(_publications(text="x" * 10001), id="text-10001"),
        pytest.param({"publications": [post("x" * 201)]}, id="id-201"),
        pytest.param(_publications(channel="c" * 65), id="channel-65"),
        pytest.param(_publications(url="u" * 2049), id="url-2049"),
        pytest.param(_keywords([f"k{i}" for i in range(51)]), id="51-keywords"),
        pytest.param(_keywords(["k" * 101]), id="keyword-101"),
        pytest.param(_publications(views=10**12 + 1), id="views-above-limit"),
        pytest.param(_publications(reactions=10**12 + 1), id="reactions-above-limit"),
        pytest.param(_publications(forwards=10**12 + 1), id="forwards-above-limit"),
        pytest.param(_publications(views=-1), id="views-negative"),
        pytest.param(_publications(views="100"), id="views-text"),
        pytest.param(_publications(shares=5), id="unknown-field"),
        pytest.param({"freshness_half_life_hours": 0}, id="half-life-zero"),
        pytest.param({"freshness_half_life_hours": 8760.001}, id="half-life-above-year"),
    ],
)
def test_input_outside_the_declared_bounds_is_refused(
    client: TestClient, overrides: dict[str, Any]
) -> None:
    response = client.post(f"{PREFIX}/rank", json=body(**overrides))

    assert response.status_code == UNPROCESSABLE


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("field", ["half_life", "weight"])
def test_a_non_finite_float_is_refused(client: TestClient, literal: str, field: str) -> None:
    weights = dict(CONFIRMED_WEIGHTS, views="__NUMBER__" if field == "weight" else 35)
    payload = body(settings={"weights": weights, "keywords": []})
    if field == "half_life":
        payload["freshness_half_life_hours"] = "__NUMBER__"
    content = json.dumps(payload).replace('"__NUMBER__"', literal)
    response = client.post(
        f"{PREFIX}/rank", content=content, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == UNPROCESSABLE
    assert response.json()["detail"]["code"] == "non_finite_number"

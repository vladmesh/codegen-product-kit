"""Offline package tests: the core façade, SQL and the tg-channels provider are stand-ins."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

ROOT = Path(__file__).parents[1]
CALLER = "telegram:123"


async def _caller_identity() -> str:
    return CALLER


class ProviderError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Provider:
    """The provider read of ``tg-channels.posts.v1``, answering canned posts or an error."""

    def __init__(self) -> None:
        self.posts: list[Any] = []
        self.error: str | None = None
        self.callers: list[str] = []

    async def recent_posts(self, user_ref: str) -> list[Any]:
        self.callers.append(user_ref)
        if self.error is not None:
            raise ProviderError(self.error)
        return list(self.posts)


PROVIDER = Provider()
_channels = ModuleType("codegen_kit_tg_channels")
_channels.__path__ = []
sys.modules["codegen_kit"] = SimpleNamespace(
    caller_identity=_caller_identity, package_database=lambda: None, Package=object
)
sys.modules["sqlalchemy"] = SimpleNamespace(text=lambda value: value)
sys.modules["codegen_kit_tg_channels"] = _channels
sys.modules["codegen_kit_tg_channels.posts"] = SimpleNamespace(
    ActionError=ProviderError, recent_posts=PROVIDER.recent_posts
)
sys.path.insert(0, str(ROOT))

from codegen_kit_ranking import CriterionWeights, Publication, RankingSettings  # noqa: E402

#: The weights the owner approved as the package defaults.
CONFIRMED_WEIGHTS: dict[str, Any] = {
    "views": 35,
    "reactions": 25,
    "forwards": 20,
    "freshness": 20,
    "keywords": 0,
}

#: A reconfiguration that switches the keywords criterion on.
KEYWORD_WEIGHTS: dict[str, Any] = {
    "views": 30,
    "reactions": 30,
    "forwards": 20,
    "freshness": 15,
    "keywords": 5,
}

CONFIRMED_KEYWORDS: tuple[str, ...] = ("ИИ", "стартапы", "инвестиции")

FRESHNESS_ONLY_WEIGHTS: dict[str, Any] = {
    "views": 0,
    "reactions": 0,
    "forwards": 0,
    "freshness": 100,
    "keywords": 0,
}

#: A fixed reference moment keeps every expectation reproducible.
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def publication(identifier: str, *, hours_old: float = 0.0, **fields: Any) -> Publication:
    """Build one publication aged ``hours_old`` before :data:`NOW`."""

    return Publication(id=identifier, date=NOW - timedelta(hours=hours_old), **fields)


def settings(
    weights: dict[str, Any] | None = None,
    keywords: tuple[str, ...] = (),
) -> RankingSettings:
    """Build the ranking settings from a weights mapping and keyword list."""

    return RankingSettings(
        weights=CriterionWeights(**(weights if weights is not None else CONFIRMED_WEIGHTS)),
        keywords=keywords,
    )


class SettingsTable:
    """Interpret the settings store's SQL over an in-memory ``ranking.settings`` table."""

    def __init__(self) -> None:
        self.rows: dict[str, str] = {}

    async def execute(self, query: str, args: dict[str, Any] | None = None) -> Any:
        if query.startswith("INSERT INTO ranking.settings") and "DO UPDATE" in query:
            assert args is not None
            self.rows[args["key"]] = args["value"]
            return SimpleNamespace(all=list)
        assert query == "SELECT key, value FROM ranking.settings", query
        rows = [SimpleNamespace(key=key, value=value) for key, value in self.rows.items()]
        return SimpleNamespace(all=lambda: rows)

    def values(self) -> dict[str, Any]:
        return {key: json.loads(value) for key, value in self.rows.items()}

    @asynccontextmanager
    async def session(self) -> Any:
        yield self

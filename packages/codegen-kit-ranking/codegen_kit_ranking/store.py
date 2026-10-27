"""Package copy of the seeded product settings ``ranking.*`` that ``top`` ranks under."""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import text as sql

from codegen_kit_ranking.database import database
from codegen_kit_ranking.models import CriterionWeights, Keywords, TopCount, TopSettings

#: Seeded product setting key to its local name and the value it must hold.
SEEDED: dict[str, tuple[str, TypeAdapter[Any]]] = {
    "ranking.weights": ("weights", TypeAdapter(CriterionWeights)),
    "ranking.keywords": ("keywords", TypeAdapter(Keywords)),
    "ranking.top_count": ("top_count", TypeAdapter(TopCount)),
}


class NotConfiguredError(Exception):
    """A seeded product setting ``top`` needs has never been written."""


class SettingsStore:
    def transaction(self) -> Any:
        return database.session()

    async def seed(self, session: Any, key: str, value: Any) -> None:
        """Validate and store the latest written value, so a later write overrides it."""

        if key not in SEEDED:
            raise ValueError(f"unsupported ranking setting seed {key!r}")
        name, adapter = SEEDED[key]
        adapter.validate_python(value)
        # Seeds run with the core search path; qualify the package-owned table explicitly.
        await session.execute(
            sql(
                "INSERT INTO ranking.settings (key, value) VALUES (:key, :value) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value"
            ),
            {"key": name, "value": json.dumps(value)},
        )

    async def read(self) -> TopSettings:
        async with self.transaction() as session:
            rows = (await session.execute(sql("SELECT key, value FROM ranking.settings"))).all()
        stored = {row.key: json.loads(row.value) for row in rows}
        if stored.keys() != {name for name, _ in SEEDED.values()}:
            raise NotConfiguredError
        return TopSettings.model_validate(stored)

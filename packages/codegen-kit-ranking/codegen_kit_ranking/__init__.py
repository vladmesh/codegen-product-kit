"""In-process lifecycle and public contract of the ranking package."""

from __future__ import annotations

from typing import Any

from codegen_kit import Package

from codegen_kit_ranking import api
from codegen_kit_ranking.models import (
    CRITERIA,
    DEFAULT_FRESHNESS_HALF_LIFE_HOURS,
    CriterionWeights,
    Publication,
    RankedPublication,
    RankingInputError,
    RankingRequest,
    RankingResponse,
    RankingSettings,
    TopPost,
    TopSettings,
)
from codegen_kit_ranking.scoring import rank


class RankingPackage:
    """Ranking owns no connection, timer or background task, only its seeded settings copy."""

    router = api.router

    async def startup(self, application: object) -> None:
        """Nothing to start."""

    async def shutdown(self, application: object) -> None:
        """Nothing to stop."""

    async def seed_setting(self, session: Any, key: str, value: Any) -> None:
        await api.store.seed(session, key, value)


package: Package = RankingPackage()

__all__ = [
    "CRITERIA",
    "DEFAULT_FRESHNESS_HALF_LIFE_HOURS",
    "CriterionWeights",
    "Publication",
    "RankedPublication",
    "RankingInputError",
    "RankingPackage",
    "RankingRequest",
    "RankingResponse",
    "RankingSettings",
    "TopPost",
    "TopSettings",
    "package",
    "rank",
]

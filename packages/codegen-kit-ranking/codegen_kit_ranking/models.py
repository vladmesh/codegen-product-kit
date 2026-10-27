"""Public data contract of the ranking package.

Every model refuses input it does not recognise instead of reading it as a value of another
type: unknown fields are rejected, a number is never read out of a string or a boolean, and
every size, count and float is bounded so a caller cannot reach an overflow or an unbounded batch.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

#: Scored criteria, in the stable order their contributions are reported in.
CRITERIA: tuple[str, ...] = ("views", "reactions", "forwards", "freshness", "keywords")

#: Age at which a publication keeps half of its freshness. Callers may override it per call;
#: it is a property of the ranking, not a product value, so it is not a package setting.
DEFAULT_FRESHNESS_HALF_LIFE_HOURS = 24.0

MAX_WEIGHT_PERCENT = 100
MAX_PUBLICATIONS = 1000
MAX_TEXT_LENGTH = 10000
MAX_ID_LENGTH = 200
MAX_CHANNEL_LENGTH = 64
MAX_URL_LENGTH = 2048
MAX_KEYWORDS = 50
MAX_KEYWORD_LENGTH = 100
MAX_COUNT = 10**12
MAX_HALF_LIFE_HOURS = 8760
MAX_TOP_COUNT = 50


class RankingInputError(ValueError):
    """Input the package refuses to interpret, so the caller can ask for clarification."""

    def __init__(self, code: str, detail: str, field: str | None = None) -> None:
        self.code = code
        self.detail = detail
        self.field = field
        super().__init__(detail)


# Strict numbers refuse a string, a boolean or (for counts) a float instead of converting it.
Metric = Annotated[int, Field(strict=True, ge=0, le=MAX_COUNT)]
Keyword = Annotated[str, Field(min_length=1, max_length=MAX_KEYWORD_LENGTH)]
Keywords = Annotated[tuple[Keyword, ...], Field(max_length=MAX_KEYWORDS)]
Percent = Annotated[float, Field(strict=True, ge=0, le=MAX_WEIGHT_PERCENT, allow_inf_nan=False)]
TopCount = Annotated[int, Field(strict=True, ge=1, le=MAX_TOP_COUNT)]
HalfLife = Annotated[float, Field(strict=True, gt=0, le=MAX_HALF_LIFE_HOURS, allow_inf_nan=False)]


class Publication(BaseModel):
    """One publication handed to the package.

    Every engagement metric is optional: availability depends on the publication's own data,
    and an absent metric is excluded from the score rather than counted as zero.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    date: AwareDatetime
    channel: str | None = Field(default=None, max_length=MAX_CHANNEL_LENGTH)
    url: str | None = Field(default=None, max_length=MAX_URL_LENGTH)
    text: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    views: Metric | None = None
    reactions: Metric | None = None
    forwards: Metric | None = None


class CriterionWeights(BaseModel):
    """Weight in percent of each criterion, mirroring the ``ranking.weights`` setting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    views: Percent
    reactions: Percent
    forwards: Percent
    freshness: Percent
    keywords: Percent

    def total(self) -> float:
        """Return the summed weight; the package normalises, so it need not be 100."""

        return float(sum(getattr(self, name) for name in CRITERIA))


class RankingSettings(BaseModel):
    """Weights and keywords of one call, as ``ranking.weights`` and ``ranking.keywords``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    weights: CriterionWeights
    keywords: Keywords = ()


class TopSettings(RankingSettings):
    """The product settings ``top`` ranks under, including ``ranking.top_count``."""

    top_count: TopCount


class RankedPublication(BaseModel):
    """A publication with its score and the per-criterion contributions behind it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    publication: Publication
    score: float
    contributions: dict[str, float]


class RankingRequest(BaseModel):
    """The HTTP form of one ranking call: publications plus the current settings."""

    model_config = ConfigDict(extra="forbid")

    publications: list[Publication] = Field(max_length=MAX_PUBLICATIONS)
    settings: RankingSettings
    limit: Annotated[int, Field(strict=True, ge=1)] | None = None
    now: AwareDatetime | None = None
    freshness_half_life_hours: HalfLife = DEFAULT_FRESHNESS_HALF_LIFE_HOURS


class RankingResponse(BaseModel):
    """The deterministically ordered top returned to the caller."""

    model_config = ConfigDict(extra="forbid")

    top: list[RankedPublication]


class TopPost(BaseModel):
    """One ranked channel post of ``top``; ``views`` is display text, ``?`` when unreported."""

    channel: str
    date: str
    text: str
    url: str
    views: str
    score: float

"""Deterministic weighted ranking of publications.

The score of a publication is the weighted mean of the criteria it carries data for. A
criterion the publication says nothing about is left out of both the numerator and the
denominator, so a post without ``views`` is not punished for data its channel never exposed.
Freshness decays smoothly and strictly with age, and keywords count only while their weight
is non-zero and keywords are configured.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from codegen_kit_ranking.models import (
    CRITERIA,
    DEFAULT_FRESHNESS_HALF_LIFE_HOURS,
    MAX_HALF_LIFE_HOURS,
    Publication,
    RankedPublication,
    RankingInputError,
    RankingSettings,
)

SECONDS_PER_HOUR = 3600.0

#: Engagement metrics a publication may or may not carry.
METRICS: tuple[str, ...] = ("views", "reactions", "forwards")


def _metric_maxima(publications: Sequence[Publication]) -> dict[str, float]:
    """Return the largest reported value of each metric, ignoring publications without it."""

    maxima: dict[str, float] = {}
    for metric in METRICS:
        reported = [
            getattr(publication, metric)
            for publication in publications
            if getattr(publication, metric) is not None
        ]
        maxima[metric] = float(max(reported)) if reported else 0.0
    return maxima


def _freshness(publication: Publication, now: datetime, half_life_hours: float) -> float:
    """Return a smooth, strictly decreasing freshness in ``(0, 1]``."""

    age_hours = max((now - publication.date).total_seconds(), 0.0) / SECONDS_PER_HOUR
    return float(0.5 ** (age_hours / half_life_hours))


def _keyword_share(text: str, keywords: Sequence[str]) -> float:
    """Return the share of configured keywords the text mentions, case-insensitively."""

    lowered = text.casefold()
    matched = sum(1 for keyword in keywords if keyword.casefold() in lowered)
    return matched / len(keywords)


def _uses_keywords(settings: RankingSettings) -> bool:
    return settings.weights.keywords > 0 and bool(settings.keywords)


def _criterion_scores(
    publication: Publication,
    settings: RankingSettings,
    maxima: dict[str, float],
    now: datetime,
    half_life_hours: float,
) -> dict[str, float]:
    """Score only the criteria this publication actually carries data for."""

    scores: dict[str, float] = {}
    for metric in METRICS:
        reported = getattr(publication, metric)
        if reported is None:
            continue
        maximum = maxima[metric]
        scores[metric] = reported / maximum if maximum > 0 else 0.0
    scores["freshness"] = _freshness(publication, now, half_life_hours)
    if _uses_keywords(settings) and publication.text is not None:
        scores["keywords"] = _keyword_share(publication.text, settings.keywords)
    return scores


def _rank_one(
    publication: Publication,
    settings: RankingSettings,
    maxima: dict[str, float],
    now: datetime,
    half_life_hours: float,
) -> RankedPublication:
    """Combine one publication's applicable criteria into a normalised score."""

    scores = _criterion_scores(publication, settings, maxima, now, half_life_hours)
    applicable = {
        name: getattr(settings.weights, name)
        for name in CRITERIA
        if name in scores and getattr(settings.weights, name) > 0
    }
    total = sum(applicable.values())
    if total <= 0:
        return RankedPublication(publication=publication, score=0.0, contributions={})
    contributions = {name: weight / total * scores[name] for name, weight in applicable.items()}
    return RankedPublication(
        publication=publication,
        score=sum(contributions.values()),
        contributions=contributions,
    )


def _order_key(ranked: RankedPublication) -> tuple[float, float, str]:
    """Order by score, then by recency, then by identifier, so the top is reproducible."""

    return (-ranked.score, -ranked.publication.date.timestamp(), ranked.publication.id)


def _require_unique(publications: Sequence[Publication]) -> None:
    seen: set[str] = set()
    for publication in publications:
        if publication.id in seen:
            raise RankingInputError(
                "duplicate_publication",
                f"publication {publication.id!r} was supplied more than once; "
                "say which one should be ranked",
                field="publications",
            )
        seen.add(publication.id)


def _resolved_moment(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        raise RankingInputError(
            "naive_moment",
            "the reference moment has no timezone; say which offset it is in",
            field="now",
        )
    return now


def _validated_limit(limit: int | None) -> int | None:
    if limit is not None and limit < 1:
        raise RankingInputError(
            "invalid_limit",
            f"a top of {limit} publications is not a size; say how many posts are wanted",
            field="limit",
        )
    return limit


def rank(
    publications: Iterable[Publication],
    settings: RankingSettings,
    *,
    limit: int | None = None,
    now: datetime | None = None,
    freshness_half_life_hours: float = DEFAULT_FRESHNESS_HALF_LIFE_HOURS,
) -> list[RankedPublication]:
    """Return ``publications`` as a deterministically ordered top under ``settings``.

    Raises :class:`~codegen_kit_ranking.models.RankingInputError` for input the component cannot
    read as a ranking request, so the caller can ask what was meant.
    """

    items = tuple(publications)
    _require_unique(items)
    if settings.weights.total() <= 0:
        raise RankingInputError(
            "weights_all_zero",
            "every criterion weight is zero, so there is nothing to rank by; "
            "say which criteria should count",
            field="settings.weights",
        )
    # The negated range also refuses NaN, which compares false with every bound.
    if not 0 < freshness_half_life_hours <= MAX_HALF_LIFE_HOURS:
        raise RankingInputError(
            "invalid_half_life",
            f"the freshness half-life must be in (0, {MAX_HALF_LIFE_HOURS}] hours",
            field="freshness_half_life_hours",
        )
    size = _validated_limit(limit)
    moment = _resolved_moment(now)
    maxima = _metric_maxima(items)
    ranked = [
        _rank_one(publication, settings, maxima, moment, freshness_half_life_hours)
        for publication in items
    ]
    ranked.sort(key=_order_key)
    return ranked if size is None else ranked[:size]

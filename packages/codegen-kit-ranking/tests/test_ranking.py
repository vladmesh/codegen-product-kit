"""Behaviour of the ranking contract itself."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from codegen_kit_ranking import Publication, RankedPublication, RankingSettings, rank
from conftest import (
    CONFIRMED_KEYWORDS,
    FRESHNESS_ONLY_WEIGHTS,
    KEYWORD_WEIGHTS,
    NOW,
    publication,
    settings,
)
import pytest

HALF_LIFE_HOURS = 24
SMOOTHNESS_LIMIT = 0.03
KEYWORDS_ONLY_WEIGHTS = {"views": 0, "reactions": 0, "forwards": 0, "freshness": 0, "keywords": 100}


def scored(
    posts: list[Publication], configuration: RankingSettings
) -> dict[str, RankedPublication]:
    """Rank ``posts`` at the fixed reference moment and index the result by identifier."""

    return {item.publication.id: item for item in rank(posts, configuration, now=NOW)}


def ordered(posts: list[Publication], configuration: RankingSettings, **options: Any) -> list[str]:
    """Return the identifiers of the top, in the order the package returned them."""

    return [item.publication.id for item in rank(posts, configuration, now=NOW, **options)]


def test_top_follows_the_configured_criteria() -> None:
    """Views, reactions and forwards combine under their configured weights."""

    posts = [
        publication("views-only", views=100, reactions=0, forwards=0),
        publication("engagement", views=0, reactions=100, forwards=100),
        publication("balanced", views=50, reactions=50, forwards=50),
    ]

    assert ordered(posts, settings()) == ["engagement", "balanced", "views-only"]


def test_top_is_deterministic_and_respects_the_requested_size() -> None:
    """Equal scores fall back to recency, then identifier, so the top never wobbles."""

    posts = [
        publication("older", hours_old=2, views=10),
        publication("c-newer", hours_old=1, views=10),
        publication("b-newer", hours_old=1, views=10),
    ]

    first = ordered(posts, settings(), limit=2)

    assert first == ["b-newer", "c-newer"]
    assert ordered(list(reversed(posts)), settings(), limit=2) == first


def test_a_missing_metric_is_excluded_rather_than_read_as_zero() -> None:
    """A publication whose channel never exposed a metric is not punished for it."""

    posts = [
        publication("complete", views=100, reactions=50, forwards=50),
        publication("no-views", reactions=50, forwards=50),
        publication("zero-views", views=0, reactions=50, forwards=50),
    ]

    ranked = scored(posts, settings())

    assert "views" not in ranked["no-views"].contributions
    assert "views" in ranked["zero-views"].contributions
    assert ranked["no-views"].score == pytest.approx(ranked["complete"].score)
    assert ranked["no-views"].score > ranked["zero-views"].score


def test_freshness_contribution_decreases_smoothly_with_age() -> None:
    """Freshness declines strictly and without a cliff as a post ages."""

    hours = list(range(2 * HALF_LIFE_HOURS + 1))
    posts = [publication(f"h{hour:02d}", hours_old=hour) for hour in hours]

    ranked = scored(posts, settings(FRESHNESS_ONLY_WEIGHTS))
    curve = [ranked[f"h{hour:02d}"].contributions["freshness"] for hour in hours]
    steps = [later - earlier for earlier, later in pairwise(curve)]

    assert all(step < 0 for step in steps)
    assert max(abs(step) for step in steps) < SMOOTHNESS_LIMIT
    # Each further hour costs less than the one before it: a smooth decay, not a drop.
    assert all(abs(later) < abs(earlier) for earlier, later in pairwise(steps))
    assert curve[HALF_LIFE_HOURS] == pytest.approx(curve[0] / 2)
    assert curve[2 * HALF_LIFE_HOURS] == pytest.approx(curve[0] / 4)


def test_a_post_dated_in_the_future_gets_the_maximum_freshness() -> None:
    posts = [
        publication("future", hours_old=-5),
        publication("now"),
        publication("older", hours_old=1),
    ]

    ranked = scored(posts, settings(FRESHNESS_ONLY_WEIGHTS))

    assert ranked["future"].contributions["freshness"] == 1.0
    assert ranked["now"].contributions["freshness"] == 1.0
    assert ranked["older"].contributions["freshness"] < 1.0


def test_keywords_count_only_when_weighted_and_configured() -> None:
    """The keywords criterion stays off without a non-zero weight and configured keywords."""

    posts = [publication("post", views=10, text="Инвестиции в ИИ и стартапы")]

    off_by_weight = scored(posts, settings(keywords=CONFIRMED_KEYWORDS))
    off_by_absence = scored(posts, settings(KEYWORD_WEIGHTS))
    switched_on = scored(posts, settings(KEYWORD_WEIGHTS, CONFIRMED_KEYWORDS))

    assert "keywords" not in off_by_weight["post"].contributions
    assert "keywords" not in off_by_absence["post"].contributions
    assert "keywords" in switched_on["post"].contributions


def test_configured_keywords_lift_the_publications_that_mention_them() -> None:
    """With keywords configured, a matching post outranks an otherwise identical one."""

    posts = [
        publication("matching", views=10, reactions=10, forwards=10, text="ИИ и стартапы растут"),
        publication("other", views=10, reactions=10, forwards=10, text="Погода в Москве"),
    ]

    ranked = scored(posts, settings(KEYWORD_WEIGHTS, CONFIRMED_KEYWORDS))

    assert ordered(posts, settings(KEYWORD_WEIGHTS, CONFIRMED_KEYWORDS)) == ["matching", "other"]
    assert ranked["other"].contributions["keywords"] == 0


@pytest.mark.parametrize(
    ("keyword", "matching", "other"),
    [
        (".*", "Скидки .* только сегодня", "Любой текст вообще"),
        ("(", "Цена (без НДС)", "Цена без скобок"),
        ("a+b", "Formula A+B holds", "Formula aab holds"),
    ],
)
def test_keywords_match_literally_after_casefold(keyword: str, matching: str, other: str) -> None:
    """Pattern metacharacters are ordinary characters: no keyword is read as an expression."""

    posts = [publication("matching", text=matching), publication("other", text=other)]

    ranked = scored(posts, settings(KEYWORDS_ONLY_WEIGHTS, (keyword,)))

    assert ranked["matching"].contributions["keywords"] == 1.0
    assert ranked["other"].contributions["keywords"] == 0.0


def test_keywords_ignore_a_publication_whose_text_never_arrived() -> None:
    """Text the package never received is unknown data, not a zero keyword match."""

    posts = [
        publication("lowercase", views=10, text="новости про ии и инвестиции"),
        publication("untexted", views=10),
    ]

    ranked = scored(posts, settings(KEYWORD_WEIGHTS, CONFIRMED_KEYWORDS))

    assert ranked["lowercase"].contributions["keywords"] > 0
    assert "keywords" not in ranked["untexted"].contributions


def test_the_largest_admitted_counts_rank_without_overflow() -> None:
    posts = [
        publication("max", views=10**12, reactions=10**12, forwards=10**12),
        publication("min", hours_old=1, views=0, reactions=0, forwards=0),
    ]

    assert ordered(posts, settings(), limit=1) == ["max"]


def test_an_empty_batch_ranks_to_an_empty_top() -> None:
    assert rank([], settings(), now=NOW) == []

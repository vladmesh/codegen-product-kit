"""Unrecognised input is never read as a record of another type.

Each case below would be a silent reinterpretation if the package were lenient: a string
becoming a view count, a boolean becoming a number, a timestamp without an offset becoming a
moment in some assumed zone, an unknown field becoming a known one. The package refuses every
one of them with a clarification instead.
"""

from __future__ import annotations

from datetime import datetime

from codegen_kit_ranking import Publication, RankingInputError, rank
from conftest import NOW, publication, settings
from pydantic import ValidationError
import pytest

ZERO_WEIGHTS = {
    "views": 0,
    "reactions": 0,
    "forwards": 0,
    "freshness": 0,
    "keywords": 0,
}


@pytest.mark.parametrize("field", ["views", "reactions", "forwards"])
def test_a_metric_written_as_text_is_refused_not_parsed(field: str) -> None:
    with pytest.raises(ValidationError):
        publication("post", **{field: "100"})


@pytest.mark.parametrize("field", ["views", "reactions", "forwards"])
def test_a_metric_written_as_a_flag_is_refused_not_counted(field: str) -> None:
    with pytest.raises(ValidationError):
        publication("post", **{field: True})


def test_a_fractional_metric_is_refused_rather_than_truncated() -> None:
    with pytest.raises(ValidationError):
        publication("post", views=10.5)


def test_an_unknown_field_is_refused_rather_than_dropped() -> None:
    with pytest.raises(ValidationError):
        publication("post", shares=10)


def test_a_timestamp_without_an_offset_is_refused() -> None:
    with pytest.raises(ValidationError):
        Publication(id="post", date="2026-10-26T12:00:00")


def test_an_empty_keyword_is_refused_rather_than_stored() -> None:
    with pytest.raises(ValidationError):
        settings(keywords=("ИИ", ""))


def test_the_same_publication_twice_asks_which_one_was_meant() -> None:
    posts = [publication("post", views=10), publication("post", views=20)]

    with pytest.raises(RankingInputError) as refused:
        rank(posts, settings(), now=NOW)

    assert refused.value.code == "duplicate_publication"
    assert refused.value.field == "publications"


def test_weights_that_rank_by_nothing_ask_which_criteria_should_count() -> None:
    with pytest.raises(RankingInputError) as refused:
        rank([publication("post", views=10)], settings(ZERO_WEIGHTS), now=NOW)

    assert refused.value.code == "weights_all_zero"


def test_a_reference_moment_without_an_offset_asks_for_the_zone() -> None:
    with pytest.raises(RankingInputError) as refused:
        rank([publication("post", views=10)], settings(), now=datetime(2026, 10, 26, 12, 0))

    assert refused.value.code == "naive_moment"


@pytest.mark.parametrize("size", [0, -1])
def test_a_top_of_no_posts_asks_how_many_were_wanted(size: int) -> None:
    with pytest.raises(RankingInputError) as refused:
        rank([publication("post", views=10)], settings(), now=NOW, limit=size)

    assert refused.value.code == "invalid_limit"


@pytest.mark.parametrize("half_life", [0, -1, 8760.5, float("inf"), float("nan")])
def test_an_in_process_half_life_outside_its_range_is_refused(half_life: float) -> None:
    with pytest.raises(RankingInputError) as refused:
        rank(
            [publication("post", views=10)],
            settings(),
            now=NOW,
            freshness_half_life_hours=half_life,
        )

    assert refused.value.code == "invalid_half_life"

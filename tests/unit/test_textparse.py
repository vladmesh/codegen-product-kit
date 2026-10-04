"""Reviewed three-form corpus with explicit clocks; no broad language accuracy claim."""

from datetime import UTC, datetime
import importlib.util
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import pytest

SOURCE = (
    Path(__file__).parents[2] / "packages/codegen-kit-textparse/codegen_kit_textparse/__init__.py"
)
spec = importlib.util.spec_from_file_location("textparse_under_test", SOURCE)
assert spec is not None and spec.loader is not None
textparse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(textparse)
NOW = datetime.fromisoformat("2026-10-07T14:00:00-04:00")


@pytest.mark.parametrize(
    ("text", "expected", "rest"),
    [
        ("buy milk in 2 minutes", "2026-10-07T14:02:00-04:00", "buy milk"),
        ("in 1 minute Take the Cake", "2026-10-07T14:01:00-04:00", "Take the Cake"),
        ("Remind me in 3 hours to Call Jo", "2026-10-07T17:00:00-04:00", "Call Jo"),
        ("remind me to Water Plants in 1 hour", "2026-10-07T15:00:00-04:00", "Water Plants"),
        ("at 11:15am Read Chapter 7", "2026-10-08T11:15:00-04:00", "Read Chapter 7"),
        ("at 12am", "2026-10-08T00:00:00-04:00", ""),
        ("at 12pm", "2026-10-08T12:00:00-04:00", ""),
        ("Pick up Mail at 6 pm", "2026-10-07T18:00:00-04:00", "Pick up Mail"),
        ("at 18:30 Check the Oven!", "2026-10-07T18:30:00-04:00", "Check the Oven!"),
        ("at 9 Call Bank", "2026-10-08T09:00:00-04:00", "Call Bank"),
        ("at 14", "2026-10-08T14:00:00-04:00", ""),
        ("TOMORROW AT 9AM Book Dentist", "2026-10-08T09:00:00-04:00", "Book Dentist"),
        ("Tomorrow at 23:59 Write Notes", "2026-10-08T23:59:00-04:00", "Write Notes"),
        ("Remind me to Buy Milk tomorrow at 4", "2026-10-08T04:00:00-04:00", "Buy Milk"),
        ("Email May in 2 minutes", "2026-10-07T14:02:00-04:00", "Email May"),
    ],
)
def test_supported_corpus(text: str, expected: str, rest: str) -> None:
    assert textparse.when(text, "en", NOW, "America/New_York") == {"at": expected, "rest": rest}


@pytest.mark.parametrize(
    "text",
    [
        "",
        "buy some milk",
        "I have 2 cats",
        "remind me to read chapter 7",
        "at noon",
        "at midnight",
        "in half an hour",
        "in two hours",
        "in 2 hrs",
        "in 0 minutes",
        "in -2 minutes",
        "in 1.5 hours",
        "in 1 hour 30 minutes",
        "in 1 hour and a half",
        "every day at 9",
        "each week at 9",
        "daily at 9",
        "next Friday at 9",
        "the day after tomorrow at 9",
        "on October 15 at 9",
        "May 5 at 9",
        "tonight at 9",
        "today at 9",
        "at 9 in the evening",
        "at 25:99",
        "at 24:00",
        "at 18:60",
        "at 0am",
        "at 13pm",
        "at 9:3",
        "at 9:000",
        "at 9.30",
        "at 9 30",
        "at 9-10",
        "at 9 a.m.",
        "at 9pmish",
        "at 9am or 10am",
        "at 9 and at 10",
        "tomorrow at 9 in 2 hours",
        "in 2 hours and in 3 minutes",
        "at 9 on 2026-11-01",
        "in 99999999999999999999999999 hours",
        "at 9:xx",
        "at 9:",
        "at 9 :30",
        "at 9 or 10",
        "at 9+0300",
        "at 9 UTC",
        "about in 2 hours",
        "tomorow at 9",
        "in 2 hours from now",
    ],
)
def test_refused_corpus(text: str) -> None:
    assert textparse.when(text, "en", NOW, "America/New_York") is None


@pytest.mark.parametrize(
    "time",
    [
        "at 9am-ish",
        "at 9pm-ish",
        "tomorrow at 9-ish",
        "in 2 minutes-ish",
        "at 18:30-ish",
        "tomorrow at 9am-ish",
        "in 1 minute-ish",
        "in 1 hour-ish",
        "in 2 hours-ish",
        "at 9am-approx",
        "in 2 minutes-approx",
    ],
)
def test_attached_textual_suffixes_are_refused(time: str) -> None:
    clock = datetime.fromisoformat("2026-10-04T08:00:00+00:00")
    assert textparse.when(f"buy milk {time}", "en", clock, "UTC") is None


@pytest.mark.parametrize(
    ("time", "at"),
    [
        ("at 9am", "2026-10-04T09:00:00+00:00"),
        ("at 9pm", "2026-10-04T21:00:00+00:00"),
        ("at 18:30", "2026-10-04T18:30:00+00:00"),
        ("tomorrow at 9", "2026-10-05T09:00:00+00:00"),
        ("in 2 minutes", "2026-10-04T08:02:00+00:00"),
        ("in 1 hour", "2026-10-04T09:00:00+00:00"),
        ("at 007", "2026-10-05T07:00:00+00:00"),
    ],
)
@pytest.mark.parametrize("punctuation", ["", ".", ",", ";", "!", "?", " -"])
def test_exact_times_retain_sentence_punctuation(time: str, at: str, punctuation: str) -> None:
    clock = datetime.fromisoformat("2026-10-04T08:00:00+00:00")
    result = textparse.when(f"Buy Milk {time}{punctuation}", "en", clock, "UTC")
    assert result == {"at": at, "rest": f"Buy Milk {punctuation.strip()}".strip()}


@pytest.mark.parametrize(
    ("text", "now", "zone", "at"),
    [
        ("tomorrow at 0", "2026-12-31T23:59:00+00:00", "UTC", "2027-01-01T00:00:00+00:00"),
        ("at 18:30", "2026-10-07T12:00:00+00:00", "Asia/Tokyo", "2026-10-08T18:30:00+09:00"),
        (
            "in 2 hours",
            "2027-03-14T00:30:00-05:00",
            "America/New_York",
            "2027-03-14T03:30:00-04:00",
        ),
        (
            "in 2 hours",
            "2026-11-01T00:30:00-04:00",
            "America/New_York",
            "2026-11-01T01:30:00-05:00",
        ),
        (
            "tomorrow at 9",
            "2026-10-31T20:00:00-04:00",
            "America/New_York",
            "2026-11-01T09:00:00-05:00",
        ),
    ],
)
def test_rollover_zone_and_elapsed_dst(text: str, now: str, zone: str, at: str) -> None:
    clock = datetime.fromisoformat(now)
    result = textparse.when(text, "en", clock, zone)
    assert result == {"at": at, "rest": ""}
    if text.startswith("in "):
        assert (
            datetime.fromisoformat(result["at"]).astimezone(UTC) - clock.astimezone(UTC)
        ).total_seconds() == 7200


@pytest.mark.parametrize(
    ("text", "now"),
    [
        ("tomorrow at 2:30am", "2027-03-13T12:00:00-05:00"),
        ("at 2:30am", "2027-03-14T00:00:00-05:00"),
        ("tomorrow at 1:30am", "2026-10-31T12:00:00-04:00"),
        ("at 1:30am", "2026-11-01T00:00:00-04:00"),
        # A passed clock rolls to tomorrow, whose wall time is a gap/fold.
        ("at 2:30am", "2027-03-13T12:00:00-05:00"),
        ("at 1:30am", "2026-10-31T12:00:00-04:00"),
    ],
)
def test_wall_times_refuse_gap_and_fold(text: str, now: str) -> None:
    assert textparse.when(text, "en", datetime.fromisoformat(now), "America/New_York") is None


def test_required_configuration_and_language() -> None:
    assert textparse.when("in 2 hours", "fr", NOW, "UTC") is None
    with pytest.raises(ValueError, match="aware"):
        textparse.when("in 2 hours", "en", datetime(2026, 1, 1), "UTC")
    with pytest.raises(ZoneInfoNotFoundError):
        textparse.when("in 2 hours", "en", NOW, "Not/AZone")
    with pytest.raises(ValueError):
        textparse.when("in 2 hours", "en", NOW, "")

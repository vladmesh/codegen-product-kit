"""Three English reminder-time forms, resolved against an explicit clock and zone."""

from datetime import UTC, datetime, timedelta
import re
from typing import TypedDict
from zoneinfo import ZoneInfo

__all__ = ["when"]
__version__ = "0.1.0"
_HALF_DAY_HOURS = 12

_TIME = re.compile(
    r"\b(?:in (?P<count>[0-9]+) (?P<unit>minutes?|hours?)\b|"
    r"(?:(?P<tomorrow>tomorrow) +)?at +(?P<hour>[0-9]+)"
    r"(?::(?P<minute>[0-9]{2}))?(?: *(?P<meridiem>am|pm))?\b)",
    re.IGNORECASE,
)
# Refusal guards, not an alternative grammar. Reserved temporal words outside the
# one consumed span are deliberately conservative, including inside task wording.
_UNSUPPORTED = re.compile(
    r"\b(?:today|tonight|tomorrow|yesterday|every|each|daily|weekly|monthly|yearly|"
    r"next|last|after|before|until|within|ago|half|quarter|couple|few|several|"
    r"about|around|approximately|tmrw|tomorow|tommorow|"
    r"noon|midnight|morning|afternoon|evening|night|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|wed|thu|fri|sat|sun|"
    r"january|february|march|april|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|"
    r"seconds?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?|am|pm|"
    r"utc|gmt|est|edt|cst|cdt|mst|mdt|pst|pdt)\b|\bfrom +now\b|"
    r"\bmay +[0-9]|\b[0-9]+ +may\b|"
    r"\b(?:at|in|on) +[+-]?[0-9]|[0-9]+[:/.][0-9]+|"
    r"\b[0-9]+(?:am|pm)\b",
    re.IGNORECASE,
)
_TRIGGER = re.compile(r"^\s*remind +me\b(?: +to\b)?\s*", re.IGNORECASE)


class _Result(TypedDict):
    at: str
    rest: str


def _wall_time(day: datetime, hour: int, minute: int, zone: ZoneInfo) -> datetime | None:
    """Admit a local wall time only if it identifies exactly one UTC instant."""
    wall = day.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=None)
    instants = set()
    for fold in (0, 1):
        candidate = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if candidate.astimezone(zone).replace(tzinfo=None) == wall:
            instants.add(candidate)
    if len(instants) != 1:
        return None
    return instants.pop().astimezone(zone)


def _absolute(match: re.Match[str], now: datetime, zone: ZoneInfo) -> datetime | None:
    hour = int(match["hour"])
    minute = int(match["minute"] or "0")
    meridiem = match["meridiem"]
    if meridiem:
        if not 1 <= hour <= _HALF_DAY_HOURS:
            return None
        hour = hour % _HALF_DAY_HOURS + (_HALF_DAY_HOURS if meridiem.lower() == "pm" else 0)
    day = now.astimezone(zone)
    if match["tomorrow"]:
        day += timedelta(days=1)
    at = _wall_time(day, hour, minute, zone)
    if at is None:
        return None
    if not match["tomorrow"] and at.astimezone(UTC) <= now.astimezone(UTC):
        at = _wall_time(day + timedelta(days=1), hour, minute, zone)
    return at


def _resolve(match: re.Match[str], now: datetime, zone: ZoneInfo) -> datetime | None:
    try:
        if match["count"] is not None:
            count = int(match["count"])
            if count <= 0:
                return None
            seconds = count * (60 if match["unit"].lower().startswith("minute") else 3600)
            return (now.astimezone(UTC) + timedelta(seconds=seconds)).astimezone(zone)
        return _absolute(match, now, zone)
    except (ValueError, OverflowError):
        return None


def when(text: str, lang: str, now: datetime, tz: str) -> _Result | None:
    """Return an aware ISO instant and original remainder, or refuse the time text.

    ``now`` must be aware and ``tz`` a valid IANA zone; configuration errors raise
    ValueError for a naive clock, ZoneInfoNotFoundError/ValueError for a bad zone.
    Only ``lang='en'`` is supported.
    See README.md for the grammar and conservative gap/fold refusal policy.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    zone = ZoneInfo(tz)
    if lang != "en":
        return None
    matches = list(_TIME.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    # Do not accept a prefix of malformed clock notation, decimal duration or suffix.
    if re.match(
        r"\s*[:/]|[.-] *[0-9]|\s*[+-][0-9]| +[0-9]|"
        r"\s+(?:and|or|to)\s+(?:at\s+)?[0-9]|"
        r"\s*(?:a\.m\.|p\.m\.|(?:am|pm)\w)",
        text[match.end() :],
        re.IGNORECASE,
    ):
        return None
    remainder = text[: match.start()] + " " + text[match.end() :]
    if _UNSUPPORTED.search(remainder):
        return None
    at = _resolve(match, now, zone)
    if at is None:
        return None
    rest = _TRIGGER.sub("", remainder)
    # A trigger before the time may leave its explicit infinitive marker behind.
    if _TRIGGER.match(text):
        rest = re.sub(r"^\s*to\b\s*", "", rest, flags=re.IGNORECASE)
    rest = re.sub(r"\s+", " ", rest).strip()
    return {"at": at.isoformat(), "rest": rest}

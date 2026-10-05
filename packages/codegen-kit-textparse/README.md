# codegen-kit-textparse 0.1.0

Source prepared for an English reminder PoC. The independent release tag is not published
by this change. This is a plain Python library, with no core activation or package manifest.

```python
from datetime import datetime
from codegen_kit_textparse import when

result = when(
    "Remind me to Buy Milk in 2 minutes",
    "en",
    datetime.fromisoformat("2026-10-07T14:00:00-04:00"),
    "America/New_York",
)
# {"at": "2026-10-07T14:02:00-04:00", "rest": "Buy Milk"}
```

`when(text, lang, now, tz)` returns `{at, rest}` or `None`. The caller supplies an aware
`datetime` and an explicit IANA timezone string. A naive clock raises `ValueError`; an
unknown zone raises `zoneinfo.ZoneInfoNotFoundError`, and malformed zone paths raise
`ValueError`. Configuration is checked even when the language is unsupported. `lang`
must be exactly `en`; other languages return `None`. There is no host clock, implicit
timezone, network, model call or product/user settings lookup. `at` is an aware ISO 8601
date-time string compatible with RFC 3339 and the catalog's `string/date-time` schema.
The catalog represents the aware `now` value as `string/date-time` at the data boundary;
the Python API receives a `datetime`, without implicit string conversion.

The complete grammar consists of three forms anywhere in reminder text:

- `in N minutes` or `in N hours`, with a positive integer, singular or plural units.
- `at H[:MM][am/pm]`, including glued `11:15am`, spaced `6 pm`, and `18:30`.
- `tomorrow at H[:MM][am/pm]`, using the next local calendar date.

Grammar is case insensitive. A bare hour is its literal 24-hour value: `at 4` means
04:00. Minutes require two digits. 12am means 00:00 and 12pm means 12:00. A time without
a date uses its next strictly future occurrence in `tz`; equality rolls to tomorrow.
An optional leading `remind me [to]` and the consumed time span are removed. Task words,
case and punctuation are retained; surrounding and join whitespace is normalized.
An explicit `to` after a leading trigger and time is also removed. Empty remainder is valid.

No word numbers, abbreviations, compound durations, recurrence, weekdays, month dates,
noon/midnight, part-of-day inference or fuzzy matching are supported. Invalid clocks,
multiple time spans and leftover temporal vocabulary are refused, including `in 1 hour
30 minutes`, `every day at 9`, `next Friday at 9`, and `the day after tomorrow at 9`.
An attached punctuation run followed by a word is an unsupported continuation of the
time token: `at 9am-ish`, `at 7pm'ish`, `tomorrow at 9am~ish`, `in 2 hours’ish` and
`at 9am...ish` all return `None`. This applies to any contiguous punctuation joiners,
including parentheses, plus, asterisk and typographic dashes, across all three forms.
The bounded qualifiers `ish`, `approx`, `roughly`, `or so`, `give or take` and
`thereabouts` outside the consumed time span are also refused, even when detached or
parenthesized. Both admission checks run before time resolution or remainder cleanup;
no qualifier is stripped and retried. Ordinary punctuation followed by whitespace/end
and spaced sentence dashes retain their existing behavior and stay in the remainder.
The refusal guards are conservative: reserved temporal words inside task wording may
also produce `None`. They are not another date grammar. This narrow corpus does not
claim the research prototype's general English accuracy.

Relative offsets are elapsed time added in UTC, including across DST. Absolute wall
times round-trip both folds through UTC. A gap or two distinct valid instants returns
`None`; no time is shifted and no fold is chosen. This also applies to the date selected
by next-occurrence rollover. Relative offsets can land in a fold because their UTC
instant is unambiguous.

Runtime dependency closure: Python stdlib plus the `tzdata` distribution only. `zoneinfo`
uses the IANA database from the system, falling back to `tzdata` if absent. Pin dependency
versions in the product's uv lock for reproducible deployment data. No parser engine,
lexicon tables, language registry or transitive runtime distributions are included.
See `THIRD_PARTY_NOTICES.md` for provenance.

After tag publication, from a generated product with tg_bot:

```bash
kit add textparse
# or an already built catalog-version artifact:
kit add textparse --wheel /path/to/codegen_kit_textparse-0.1.0-py3-none-any.whl
```

The command targets `services/tg_bot` even when a backend also exists. It copies the
verified wheel into that service's `packages/`, updates its uv dependency and lock, then
syncs its environment. It does not activate a package, change backend allowlists or
generate handlers. The caller wires `when` later; recommendations install nothing.
The service Dockerfile copies the locked wheel directory before frozen dependency sync.
Without tg_bot the command fails before writes. Python selection uses the service venv,
or `uv python find --project services/tg_bot --no-python-downloads` when no venv exists.
The interpreter must already be available. Missing tags and malformed wheels are refused
before writes. uv add/sync failures after verification are ordinary install failures and
do not promise transactional rollback.

See [release operation](../../docs/releases/textparse-0.1.0.md).

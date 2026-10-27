# ranking 0.1.0

Deterministic, configurable ranking of publications, packaged as an in-process kit package
(package protocol v1, distribution `codegen-kit-ranking`, module `codegen_kit_ranking`, entry
point `ranking`). It has two actions under the HTTP prefix `/ranking`:

- `POST /ranking/rank` ranks the publications in the request under the weights and keywords in
  the same request. It reads no channel and stores nothing.
- `GET /ranking/top` takes no body and ranks the verified caller's channel posts, read in process
  from the tg-channels provider, under the product settings below.

Both routes depend on the core caller identity (`codegen_kit.caller_identity`): a request without
the core identity capability and a known active user is refused with 401 or 403 before the
package runs. The shipped binding v2 adds `/top`, which shows each ranked post's channel, date,
views, text and link in RU or EN; it claims no other command.

Install it in a backend,tg_bot product after tg-channels, then bind both:

```sh
kit add tg-channels && kit add ranking
kit bind tg-channels --default && kit bind ranking --default
```

It requires core façade 2.4. The manifest declares `requires: [tg-channels.posts.v1]`, which
tg-channels 0.2.0 provides; generation refuses a product that activates ranking without a provider
of that interface. The provider is not a Python dependency of this distribution: tg-channels is
installed as its own package, and `package_dependencies` admits the import of its read
`codegen_kit_tg_channels.posts.recent_posts`.

## What it ranks by

| Criterion   | Source                                      |
|-------------|---------------------------------------------|
| `views`     | the publication's reported view count       |
| `reactions` | the publication's reported reaction count   |
| `forwards`  | the publication's reported forward count    |
| `freshness` | the publication's age at the reference time |
| `keywords`  | configured keywords found in its text       |

A publication's score is the weighted mean of the criteria it carries data for. Engagement
metrics are normalised against the largest value in the same batch. A criterion the publication
says nothing about is left out of both the numerator and the denominator, so a post whose channel
never exposed `views` is not punished for it, unlike a post that reports `views: 0`. Channel posts
from tg-channels carry views only; reactions and forwards are absent there and so never count.

Freshness halves every `freshness_half_life_hours` (24 by default; `rank` accepts a value in
`(0, 8760]` per call). Every date, including the reference moment `now`, must be timezone-aware:
a timestamp without an offset is refused. A post dated after the reference moment is treated as
age zero and gets the maximum freshness, 1.

Keywords count only while their weight is non-zero and keywords are configured. A keyword matches
literally: both the keyword and the text are casefolded and the keyword must occur as a substring.
No pattern syntax applies, so `.*` or `(` match only those characters.

Ties are broken by recency and then by publication id, so the same input always produces the same
order.

## Input bounds

Requests outside these bounds are refused with 422:

- at most 1000 publications per `rank` call;
- `id` 1 to 200 characters, `channel` at most 64, `url` at most 2048, `text` at most 10000;
- `views`, `reactions` and `forwards` integers from 0 to 10^12;
- weights numbers from 0 to 100;
- at most 50 keywords, each 1 to 100 characters;
- `freshness_half_life_hours` greater than 0 and at most 8760;
- no number may be sent as a string or a boolean, and no float may be infinite or NaN (a `NaN`
  or `Infinity` literal is answered with the code `non_finite_number`).

## Settings

The package owns three product settings, keys `ranking.weights`, `ranking.keywords` and
`ranking.top_count`. Each is a product-scope `setting_seeds` binding and declares its default in
`settings_schema`:

```yaml
ranking.weights:   {views: 35, reactions: 25, forwards: 20, freshness: 20, keywords: 0}
ranking.keywords:  []
ranking.top_count: 10   # 1..50
```

An install writes them through the core settings contract (`POST /settings/set`, product scope);
a brief or the platform overrides them the same way. Every successful write reaches the package's
`seed_setting` callback, which validates the value and keeps the latest one in the package schema
`ranking`. Until all three have been written, `top` answers the declared `not_configured` error,
and so does `top` under weights that are all zero. The package applies no value of its own.

## top

`top` reads the caller's posts through `tg-channels.posts.v1`, the same store, 72-hour window and
20-post limit as the tg-channels digest, ranks them and returns up to `top_count` items
`{channel, date, text, url, views, score}` by descending score. `views` is display text: the
reported count, or `?` when the channel reported none. With no posts it returns an empty list.
tg-channels' `not_configured` is answered as `not_configured`, its other failures as `try_later`;
both are HTTP 409 `{"detail": {"code": ...}}`.

## rank

```
POST /ranking/rank
{"publications": [...], "settings": {"weights": {...}, "keywords": [...]}, "limit": 10}
```

The same contract is importable in process: `from codegen_kit_ranking import rank`. Input it
cannot read as a ranking request (the same publication twice, weights that rank by nothing, a
reference moment without an offset, a non-positive limit, a half-life outside `(0, 8760]`) is
refused with `RankingInputError`, over HTTP as 422 `{"detail": {"code", "field", "clarify"}}`.

Package tests run in the kit unit lane through `tests/unit/test_ranking_package.py`.

# ranking 0.1.0

The first release of the ranking package, prepared and pending publication. No kit core, tooling,
template, orchestrator or `CORE_VERSION` change; package protocol stays 1.

Release identity: distribution `codegen-kit-ranking` 0.1.0, module `codegen_kit_ranking`, entry
point `ranking`, source `packages/codegen-kit-ranking`, `requires_core: ">=2.4,<3"`, MIT license
(the kit's), intended annotated tag `packages/ranking/v0.1.0`. The distribution follows the kit's
`codegen-kit-<name>` convention; the owner allowed keeping the candidate's `digest-ranking`, and
the kit name was chosen so that distribution, module and source path match the other kit packages.

It is pinned in `packages/pending-releases.yaml` as a first release (`supersedes: null`), whose
`catalog_package` holds its catalog metadata: summary, capabilities, the settings `weights`,
`keywords` and `top_count`, no environment, the `rank` and `top` actions and the default binding.
The catalog lists no ranking entry until its tag exists.

## Contents

- `POST /ranking/rank` ranks the publications in the request under the weights and keywords in the
  request, with the candidate's code/field/clarify error contract.
- `GET /ranking/top` ranks the verified caller's channel posts, read in process from the
  tg-channels provider, by views, freshness and optional keywords under the product settings, and
  returns up to `top_count` items `{channel, date, text, url, views, score}`; an empty list without
  posts, `not_configured` without the seeded settings.
- The default binding v2 claims only `/top`, a show command with RU and EN help and replies that
  shows each item's channel, date, views, text and link, with `on_none`.
- `ranking.weights`, `ranking.keywords` and `ranking.top_count` are product-scope `setting_seeds`
  with declared defaults `{views: 35, reactions: 25, forwards: 20, freshness: 20, keywords: 0}`,
  `[]` and `10` (1 to 50). The `seed_setting` callback keeps the latest written value in the
  package schema `ranking` (migration `ranking_0001`), so a brief or the platform overrides a value
  through the core settings contract.

## Owner review changes

The source is the reviewed candidate exported from the product commit
`60f3b7d0951f290d2b33e41f8f02efda89bb2892`, with the six required changes:

1. Both routes depend on the core caller identity, the only enforcement place the core offers for
   a package route: no capability or identity answers 401, an unknown or inactive user 403.
2. Input bounds in the models, the manifest actions and the settings schema: at most 1000
   publications, `text` 10000, `id` 200, `channel` 64 and `url` 2048 characters, at most 50
   keywords of at most 100 characters, counts from 0 to 10^12, half-life in `(0, 8760]`, `top_count`
   1 to 50, and no infinite or NaN float (a `NaN` or `Infinity` literal answers 422
   `non_finite_number`). Numbers are strict, so their bounds are real JSON Schema keywords.
3. Keywords match literally after casefold.
4. The README states that a post dated in the future gets the maximum freshness and that every
   date must be timezone-aware.
5. Defaults through `setting_seeds`, as above.
6. `pyproject.toml` declares the MIT license with the kit's license file.

## Composition

ranking is an ordinary catalog package. It declares `requires: [tg-channels.posts.v1]`, which
[tg-channels 0.2.0](tg-channels-0.2.0.md) provides, and lists `codegen_kit_tg_channels` in
`package_dependencies` for the import of `codegen_kit_tg_channels.posts.recent_posts`. Generation
enforces the provider across the active set and refuses ranking without it. tg-channels is not a
Python dependency of the distribution; it is installed first as its own package:

```sh
kit add tg-channels && kit add ranking
kit bind tg-channels --default && kit bind ranking --default
```

## Publication

A separate PO operation creates the annotated tag `packages/ranking/v0.1.0` at the reviewed merge
commit, after or together with `packages/tg-channels/v0.2.0`. A following change adds the ranking
entry under `packages` in `packages/catalog.yaml` from `catalog_package` with `catalog_entry` as its
first version, removes the pending entry, pins the tag in the runner's
`support.PUBLISHED_RELEASES` and proves the published release (docs/CONTRACTS.md, "Pending
releases"). ranking needs tg-channels 0.2.0 in the catalog first: 0.1.2 does not provide
`tg-channels.posts.v1`.

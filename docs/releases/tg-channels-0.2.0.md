# tg-channels 0.2.0

A package-only minor release, prepared and pending publication. No kit core, tooling, template,
orchestrator or `CORE_VERSION` change; `requires_core` stays `>=2.4,<3` and package protocol stays 1.

Release identity: distribution `codegen-kit-tg-channels` 0.2.0, entry point `tg-channels`, intended
annotated tag `packages/tg-channels/v0.2.0`, superseding 0.1.2. It is pinned in
`packages/pending-releases.yaml`; the catalog keeps selecting 0.1.2 until the publication below.

## Change

- The `digest` action output items gain `views: integer | null`, the tg-reader Post `views` of the
  folded post version that the client already receives (the vendored `contracts/openapi.json` of
  tg-reader API 0.1.0 already declares it, so the vendored contract is unchanged). No other digest
  field changes. The manifest action output, the HTTP response model and the client projection of
  the reader Post carry it.
- The `tg-channels.post` event payload is unchanged: its schema stays closed and carries no views.
- The manifest declares `provides: [tg-channels.posts.v1]`. The in-process read of that interface
  is `codegen_kit_tg_channels.posts.recent_posts(user_ref)`, which returns exactly what `digest`
  returns for the caller (same store, 72-hour window, 20-post limit and bounded page reads) and
  raises `ActionError` with `not_configured` or `try_later`. The package README documents it.
- The default binding and every other behaviour are unchanged; `/digest` still shows channel,
  date, text and link.

## Compatibility

A product on 0.1.2 that regenerates after upgrading sees one additive digest field and one provided
capability. Nothing requires the capability unless a package that declares it is installed, such as
[ranking 0.1.0](ranking-0.1.0.md). Generation refuses a second provider of the same capability.

## Publication

A separate PO operation creates the annotated tag `packages/tg-channels/v0.2.0` at the reviewed
merge commit. A following change appends the pending `catalog_entry` to the tg-channels `versions`
in `packages/catalog.yaml`, adds `views` to the catalog's `digest` action output, removes the
pending entry, pins the tag in the runner's `support.PUBLISHED_RELEASES` and proves the published
release (docs/CONTRACTS.md, "Pending releases").

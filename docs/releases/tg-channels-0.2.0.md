# tg-channels 0.2.0

A package-only minor release. No kit core, tooling, template, orchestrator or `CORE_VERSION`
change; `requires_core` stays `>=2.4,<3` and package protocol stays 1.

Release identity: distribution `codegen-kit-tg-channels` 0.2.0, entry point `tg-channels`,
superseding 0.1.2. Published as the annotated tag `packages/tg-channels/v0.2.0`, object
`416f512f28b1a67fd3ccbc1c0f2e41d65cb30a25`, at commit `00a8313d5b65af299b91a628c7388168d15b94e5`,
package tree `910b218b1475ee8607416703268383620d0dcb08`. The 0.1.x tags are unchanged.

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

`packages/catalog.yaml` lists 0.2.0 as the newest tg-channels version, so `kit add tg-channels`
selects it for a product core in `>=2.4,<3`; the catalog's `digest` action output carries `views`.
Nothing is pending for tg-channels. The runner's `support.PUBLISHED_RELEASES` pins the tag object,
commit and tree above, and the Runner Proof defaults to `proof_mode=published_release` with
`package_version=0.2.0`: a pull request against its own catalog (`catalog_mode=candidate_snapshot`),
a push to `main` against the real default branch's catalog (`catalog_mode=remote_head`); see
[RUNNER_PROOF.md](../RUNNER_PROOF.md).

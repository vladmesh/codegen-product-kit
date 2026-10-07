# tg-channels 0.1.0 preparation

The first tg-channels package adds public Telegram channel lists, checked username entry,
digests and timer-driven post delivery. It requires core façade `>=2.4,<3`, stays in process,
and ships RU/EN binding v2 with an explicit product language setting. The package owns its
database schema, cursor, seen post identities, recipient outbox, platform environment data
and a vendored API contract. No LLM, translation, stand run or platform auth change is included.

The package README documents installation, the «Кипр» starting list setting seed, pending
channels, quota backoff, cursor restart, durable authentication stop, digest bounds and
lease expiration. No kit core or template knows the concrete platform service name.

Before publication, Framework CI and Test Copier Template must pass, including the slow
generated-product typecheck lane. Worker-local focused/broad evidence covers offline package
contracts and fast generation/handlers; it does not replace executed CI or an exact-SHA gate.

The PO publishes from reviewed merged main in a separate operation:

1. Confirm the clean reviewed commit contains this package's 0.1.0 manifest, pyproject,
   resources and the catalog newest entry. Run the existing HEAD-consistency test.
2. Check the package tree at that commit: `git ls-tree -r HEAD -- packages/codegen-kit-tg-channels`.
   Verify the wheel includes its own manifest, binding, vendored contract and migrations.
3. Create the annotated tag `packages/tg-channels/v0.1.0` at that exact merge commit.
   Verify its package tree matches the reviewed tree, then push only that package tag.
4. Confirm catalog-resolved installation uses the immutable tagged source. No kit core tag
   is needed and no older package tag is moved or recreated.

This preparation creates no tag. Publishing and any deployed-product acceptance remain
separate PO operations.

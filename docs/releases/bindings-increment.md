# Product bindings increment (sprint:1480)

This increment adds mechanical `kit bind` and regeneration for a fresh backend,tg_bot product.
It does not publish a core tag, demonstrate a real 2.1 Copier upgrade, change orchestrator
installation or perform stand/Telegram acceptance. Those remain separate sprint boundaries.

The [product bindings contract](../CONTRACTS.md#product-telegram-bindings) gives exact CLI routes,
installed-product preflight, file/settings ownership, timezone setup, parser None/invalid-text
behavior, HTTP caller propagation, callback bounds and relay retention/retry/crash semantics.

The component artifacts stay immutable:

| Tag | Annotated object | Target | Package tree |
|---|---|---|---|
| `packages/reminders/v0.5.0` | `45a6eca816494f0100bd3ec75d45339eb977e36b` | `2748ffd05a982b9d193f4e43d47f6e4a6ff70a21` | `55d6cc832dba101e85a4a6053232dacc8513794e` |
| `packages/textparse/v0.1.0` | `4d15b0ce524b1fe90ad363d2fec935874af04e06` | `d68d997fe43a6067bfadf25bd76283141e1fc598` | `9f14cdb2e53a2fd4a34cabd437b9557540254846` |

The finite binding uses actual actions from the installed package, not catalog backports.
Reminders 0.3/0.4 remain usable at their previous boundary and refuse requested binding support.
No component source/ref or parser scope changes in this increment. Product-owned command/reply
edits affect regenerated behavior; default binding never silently replaces them.

Binding startup registers callbacks after the existing admission handler. It uses a client
factory to avoid importing main, and owns only its subscriber broker/client. No-binding backend
and standalone seeds import without textparse/relay; standalone bots still fail closed.
The service declares direct FastStream/Redis/JSON Schema dependencies and keeps locked wheel
copying for textparse. Generated code remains included in the existing product typecheck scope.
The immutable textparse 0.1.0 wheel has no PEP 561 marker; its generated import carries only
`type: ignore[import-untyped]`. Other generated code keeps the existing typecheck policy.
The published-remote receipt includes the full product typecheck output.

The existing published-remote CI lane uses candidate tooling and actual default catalog/remote
tags. It keeps the released textparse interpreter/image proof, adds reminders installation,
activation/resource checks and bind/regeneration, then runs controlled-clock generated handlers.
The same job's Redis 7 service runs real BinaryMessageFormatV1 stream delivery with a fake sender,
including a pre-start event, duplicates, concurrent consumers/restart, retry/terminal policy and
expired abandoned claim. Its uploaded `bindings-remote-redis-smoke-<run-id>` receipt records the
candidate, immutable provenance and execution assertions. Local fake transport does not attest
that real Redis or published-install boundary.

Worker validation uses focused subsets while editing and the exact TASK.md broad wrapper once
after final edits. The worker-local receipt records content identity and workspace import
provenance; it is distinct from the later dispatcher exact-SHA gate and CI artifacts. Committing
the same bytes reuses it through `check show`. Heavy proofs run in CI only. Report pending CI
URLs/artifacts truthfully until downstream gates execute, without replacing them with local fixtures.

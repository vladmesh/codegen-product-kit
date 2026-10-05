# Reminders 0.5.0 preparation and core 2.2

This card prepares reminders 0.5.0, core façade 2.2.0 and a declarative English binding.
Package protocol and catalog format remain 1. No kit core or reminders tag is published.
The later binding generator, Telegram handlers/callbacks/due relay, real Copier upgrade and
final kit release remain separate work. The reminder API, verified ownership, migrations,
timer and durable outbox implementation are unchanged.

The candidate report must name its exact commit and the Git tree at
`<candidate>:packages/codegen-kit-reminders`. Publication requires the reviewed merge,
green main CI and that same reviewed package tree, not an unmerged candidate substituted
for a release. Inspect the wheel for `codegen_kit_reminders/bindings/default.yaml`; the
real wheel fixture checks its bytes and generated-product tests resolve it through
`importlib.resources`. Action schemas must agree with the real FastAPI OpenAPI.

The independent PO operation, after those prerequisites, uses:

```bash
git fetch origin main --tags
release_commit=<reviewed-merge-sha>
reviewed_package_tree=<tree-recorded-in-reviewed-report>
test "$(git rev-parse "$release_commit:packages/codegen-kit-reminders")" = "$reviewed_package_tree"
git show "$release_commit:packages/codegen-kit-reminders/pyproject.toml"
git show "$release_commit:packages/codegen-kit-reminders/codegen_kit_reminders/package.yaml"
git show "$release_commit:packages/catalog.yaml"
git tag -a packages/reminders/v0.5.0 "$release_commit" -m "reminders 0.5.0"
git push origin refs/tags/packages/reminders/v0.5.0
git ls-remote origin refs/tags/packages/reminders/v0.5.0 'refs/tags/packages/reminders/v0.5.0^{}'
```

Core and package tags must be on separate commits. If the final core tag is planned on
this merge, put the package tag on the reviewed PR source commit only after verifying
its identical package tree. Never move a published tag. Publish no core tag before the
later bindings implementation and real upgrade validation.

Core 2.0 continues to select 0.3.0; core 2.1 selects 0.4.0; core 2.2 selects 0.5.0.
Preserved old source fixtures come from the actual annotated package tags, with
commit/package-tree provenance and an offline Git-object integrity test. Historical
fixtures are never made by relabeling 0.5.0. The unchanged 0.7.1 catalog loader is also
executed. Newest catalog metadata grants no capabilities to an installed old manifest.

The required `Test Copier Template / test-pytest` slow leg now exercises both the
deterministic local fixture and actual published textparse default-remote installation.
The remote case uses the exact candidate tooling pin, no wheel/catalog override, and
checks the published annotated object `4d15b0ce524b1fe90ad363d2fec935874af04e06`,
dereference `d68d997fe43a6067bfadf25bd76283141e1fc598` and package tree
`9f14cdb2e53a2fd4a34cabd437b9557540254846`. It checks dependency closure, the locked
wheel path, parsed output in the service interpreter and built image, and unchanged
backend. Missing CI configuration or receipt fails the job. Its uploaded artifact
`textparse-remote-release-smoke-<run-id>` records the candidate and evidence. This card
does not modify published textparse source or tags.

Worker validation uses the exact receipt wrapper in TASK.md once after final edits.
Read `check show` after committing those same bytes; record its content digest, counts,
workspace import provenance and exit status. That is a worker-local content receipt,
not a dispatcher exact-SHA gate or a CI image proof. Heavy, network and container
proofs run only in CI. Report their run/artifact URLs when available, and explicitly
state when they are pending downstream gates.

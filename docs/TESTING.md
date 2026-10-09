# Framework testing

Run framework checks from the repository root after `make setup`.

## Test targets

| Command | Scope |
|---|---|
| `make lint` | Ruff checks for `framework/` and framework tests |
| `make test` | Unit and tooling tests with coverage |
| `make test-copier` | Non-slow generated-project matrix |
| `make test-copier-slow` | Docker and generated-project end-to-end cases |
| `make test-all` | `make test` plus the non-slow Copier suite |

Use the project venv for a focused pytest run while editing:

```bash
.venv/bin/pytest tests/tooling/test_openapi.py -q
.venv/bin/pytest tests/copier/test_template_generation.py -k backend -v
```

## What to run

- Changes under `framework/`: focused tests, `make lint`, `make test`, and generated-product tests.
- Changes under `template/` or `copier.yml`: also run `make test-copier`.
- Changes to module selection, Dockerfiles, Compose, setup, or generated-project commands: also run
  `make test-copier-slow` when Docker is available.
- Before release: run both Copier suites and generate the supported module combinations.

## Generated projects

The root Makefile tests the framework repository. A generated project has a different command
surface:

```bash
make setup
make lint
make typecheck
make tests
```

Backend projects additionally expose `make generate-from-spec` and `make test-integration`.

## CI

`.github/workflows/ci.yml` runs the framework suite.
`.github/workflows/test-template.yml` exercises Copier generation and generated-project behavior.
`.github/workflows/runner-proof.yml` runs the fresh installed-product runner proof on every PR and
main push, as a release matrix (`fresh`, and `coexistence` with reminders) in an explicit proof
and catalog mode: the published tg-channels against the candidate's catalog snapshot on a pull
request and against the real default branch's catalog on main; see
[RUNNER_PROOF.md](RUNNER_PROOF.md) for its stages, fixtures and evidence contract.
`tests/tooling/test_package_catalog.py` checks each package's newest catalog release against its
annotated tag (Framework CI fetches tags), the package source at HEAD against that release or
its entry in `packages/pending-releases.yaml`, and each published release the runner pins
(`support.PUBLISHED_RELEASES`) against its catalog entry, its tag object, peeled commit and
package tree, and, while it is the newest and nothing is pending, the package tree at HEAD. `tests/tooling/test_package_timers.py` feeds the
timer job name generated from tg-channels' shipped manifest (`JOB_TIMERS`) to the package's own
consumer, so a consumer listening for any other name fails.
Keep required job names stable unless branch protection is updated at the same time.

`tests/copier/test_prepare_env.py` covers the generated `scripts/prepare-env.sh` with a recording
`uv` stand-in: frozen syncs per named environment, repeated runs, runtime flags without root
tooling, refusal of unknown or missing environments before any sync, a failed service sync and a
sync without an interpreter. It checks that setup, both CI jobs and every Dockerfile stage of the
three product shapes use the command, that no other `uv sync` recipe remains, and executes the
rendered main-job preparation step. `tests/unit/test_runner_proof.py` covers the runner's workflow
step execution rules, evidence parsers, key format, the fixture reader against the vendored
tg-reader OpenAPI contract, the fake Bot API, the workflow's trust, mode and evidence guards, the
proof/catalog mode pairing, the published tag check against the real remote's listing and its
fetched copy (missing tag, other object, target, type or tree), copied tag object ids, the catalog
digest, the snapshot HTTP read check and the pending-release helpers on a sample entry. The
real runner, its Docker images, registry, platform and orchestrator executor run only in CI.

`tests/copier/test_deploy_transport.py` belongs to the existing `test-pytest` fast Copier leg.
That checkout fetches history and tags so the upgrade fixture can generate exact annotated 0.6.3
(`f23460c62fa3508858c0552557b2860af09f2656`). Dirty candidates use the committed temporary snapshot
from `tests/copier/conftest.py`; no released fixture is patched. The tests execute the rendered
copy shell with controlled SSH/SCP/sleep captures, then feed its arguments to installed OpenSSH:
`ssh -G -F /dev/null` reads configuration without connecting, and `scp -S` invokes a capture
transport that always fails without connecting. These tests require OpenSSH, rather than skipping
native parsing proof. They assert the actual host and username, including the released IPv6
misparse to `2001`. The appleboy action input is checked, but the external action is not executed.

Real Copier update cases run `uv lock`, commit synthetic owned environment/application data,
read back retained bytes and the updated workflow, preserve a nonconflicting workflow name, and
report an overlapping runner change as a `.rej`. Only isolated fixture Git downloads are mapped
to the local committed source. See [the upgrade review boundary](releases/0.6.4.md).
Existing slow generation, service typechecks, logging and PostgreSQL migration proofs keep their
CI routes; a local fast broad receipt does not replace the required release matrix.

`tests/unit/test_textparse.py` is the narrow three-form English corpus. Library install
resolution/artifact refusals and released-reader regression are under `tests/tooling/`.
`tests/copier/test_textparse_install.py` checks the rendered wheel-copy Docker path in the fast
matrix. Its slow proof builds the actual Hatch wheel, installs from a local independent tag
into generated backend,tg_bot, verifies the tg_bot interpreter and dependency closure, then
builds and calls through the tg_bot image. It runs in the existing slow `test-pytest` CI leg;
workers do not run it locally under the control-host rule.

The same slow test is parameterized for the actual published textparse tag and default remote
catalog/source, using exact candidate tooling. The required `test-pytest` job executes both cases;
missing CI configuration fails the remote case. A successful remote interpreter/image proof writes
an uploaded `textparse-remote-release-smoke-<run-id>` JSON receipt. Local fixture evidence cannot
stand in for this remote receipt. No worker runs either heavy proof locally.

`tests/tooling/test_reminders_contracts.py` imports the real reminder API with inert identity/storage
dependencies and compares action inputs/outputs against FastAPI OpenAPI, resolving local refs and
retaining constraints. Deliberate operation/schema drift must fail. It validates the shipped finite
binding and its refusal paths, checks actual parser/action schema matching, and verifies preserved
old package Git trees. Fast generated activation tests admit metadata under core 2.2, exercise real
caller-owned routes and resolve the installed binding resource. The slow real Hatch wheel fixture
checks resource bytes. These contract proofs do not execute Telegram handlers or callbacks.

The package migration proof's manual crash/recovery scenario registers its `reminders.due`
reader before crashing the publisher. A live backend timer may recover a committed pending
emission before the replacement process runs; creating a new consumer group at the stream's
latest entry after that publication would skip the event. Early registration preserves the
delivery observation while retaining the crash exit, pending database state, replacement exit,
event identity and stream-count assertions. This Redis/PostgreSQL scenario runs in CI only.

The generated product's `tests/integration/test_durable_events.py` runs every backlog, live,
reclaimed, duplicate and generated-adapter case through one fixture transport boundary: its own
Redis logical database (15, never the product's), into which the generated publishers and every
reader are redirected. It deletes only its own streams, never flushes the live backend's Redis,
and asserts the identity of each delivery: the duplicate phase checks both deliveries carry the
duplicate UUID with one effect, and the adapter guard sees exactly the published event twice.
While these tests run, they publish real current-slot core-timer `job_fired` events (the
`core-timer:<job>:<slot>` identity of the first `JOB_TIMERS` entry) into the product's live
database, where the backend's packages consume them as ordinary module deliveries; none may
reach the owned stream. Products without timers skip that publication. Redis/Postgres runs are
CI-only (product CI inside the runner proof and the slow Copier integration lanes).

When a Docker-dependent test cannot run, skip it explicitly at the pytest boundary with a reason;
do not silently return from the test.

`tests/copier/test_bindings.py` is the focused non-slow bindings subset. It generates the
backend,tg_bot shape, installs actual local component sources, copies the installed default
through bind and runs generated handlers with the real parser and fake backend/clock. It covers
caller headers, settings failures, empty versus None parsing, presets/DST, bounded owned callbacks,
list/cancel, product overrides and nonmutating refusals. Generated relay unit transport uses
FastStream TestRedisBroker with fake Redis state, including startup failure cleanup; that proves
encoding/handler injection but does not prove real stream wiring.

`test_bound_product_generation_passes_its_own_drift_and_lint` also runs the ruff format,
ruff check and xenon commands read from the bound product's generated Makefile after each
of two regenerations and generated-tree drift checks. The actual reminders default binding
and real textparse library must pass the unchanged product complexity thresholds, with
byte-identical output across both runs. Xenon is a kit dev dependency for this non-slow proof.

The existing textparse `published_remote` slow lane additionally installs the independently
published reminders 0.5.0 through default remote/catalog paths, using exact candidate tooling.
It binds/regenerates, executes the same handler corpus, checks activation/resource/version,
and runs `binding_redis_scenarios.py` on the CI job's real Redis 7 service with a fake Telegram
sender. Pre-start publication, duplicate/concurrent consumers, restart, transient reclaim,
terminal poison and abandoned-claim expiry are required assertions. Its candidate/provenance/
execution receipt is uploaded as `bindings-remote-redis-smoke-<run-id>` with missing-artifact
failure. This lane retains the textparse interpreter/image proof and original artifact.
Workers do not run real Redis, Docker, image/dependency or product typecheck proofs locally;
the task packet's broad wrapper and its permitted focused subsets supply local evidence.

`tests/copier/test_core_upgrade.py` adds a CI-only slow released-product proof in the same required
`test-pytest` job, using full history/tags and `CODEGEN_CORE_UPGRADE_SHA` set to the full PR head
SHA (push: commit SHA). It clones that exact source with the original annotated 0.7.1 objects;
an isolated Git URL mapping permits faithful old/candidate tooling downloads while retaining
original URL/commit provenance. Published-component installs use the actual default remote with
that mapping removed. There is no current-tooling override on the released copy, dirty snapshot
attestation, patched baseline, skip, or fresh-copy substitute for update.

The test commits generated 0.7.1 output as-is, verifies core 2.1/old lock/runtime tooling and clean
baseline, executes real Copier update with conflict rejection, then reads candidate core 2.3,
answers, lock, installed direct URL/import origin and protected-file hashes. Product setup,
validation, generation, typecheck and unit tests precede actual reminders 0.5/textparse 0.1
installation and default bind. The existing fake-backend/clock handler corpus is reused for the
unbound-to-bound transition, confirmation, None presets, caller headers, timezone/DST, list/cancel;
the established real Redis lane stays separate. Product typecheck output is inspected as well as
its exit status because the generated Makefile loops over services.

Required upload `core-21-upgrade-smoke-<run-id>` fails if its receipt is absent. It records exact
producer/tag/tree/blob and candidate identities, baseline commit/answers, copy/update argv/output,
conflict scan, protected hashes, updated answers/lock and installed tooling provenance, all product
check output and published-component/scenario results. Core update, setup/generation and later
install/binding changes have separate hash diffs. The two existing textparse/binding artifacts
and required job names remain intact. No local slow upgrade, uv-download/typecheck, Docker or
real Redis run is authorized; use focused permitted subsets, lint and the packet's canonical broad
wrapper once after final edits. Worker-local content receipts remain distinct from the executed
dispatcher exact-SHA gate and CI artifacts. See [0.8.0 evidence limits](releases/0.8.0.md).

The non-slow `test_bound_product_handler_unit_tests_need_no_redis` runs the product's rendered
tg_bot handler unit file with reminders/textparse/default binding already installed and
`REDIS_URL=redis://redis.invalid:6379`. It catches unit tests accidentally starting the real binding
relay, as the first upgrade CI did at the final post-bind `make tests`. The template lifecycle
unit test mocks both publisher broker and binding hooks, asserting all awaits and their order;
error cases require broker cleanup and propagation. The real relay lifecycle remains covered by
the separate fake transport and CI Redis scenarios, without changing production behavior or the
genuine old released baseline.

Platform environment declarations have focused unit coverage in `test_platform_environment.py`
and `test_package_environment.py`: manifest/schema export, validation, sensitivity, deterministic
merging, conflicting package/product sources and the unchanged unspecified-source fallback.
`test_package_catalog.py` builds a real offline platform-probe wheel from its fictional-service
YAML and runs catalog-resolved `kit add` through installation/discovery and fragment generation.
Fast Copier activation tests admit the metadata under core 2.3, reject its minimum on core 2.2,
and run complete generation twice before merging all product env fragments with exact declared
data. A unit test prevents concrete platform service names from entering `framework/` or `template/`.

`tests/tooling/test_bindings_v2.py` admits the synthetic bilingual grammar and rejects invalid
locales, keys, sources, contracts and duplicates. Its pinned pre-v2 output fixture verifies v1
bindings and relay bytes. `tests/copier/test_bindings_v2.py` binds the inert binding-notes fixture
next to reminders and runs text creation, list/remove, show and localized relay delivery in RU
and EN, including language changes between list and callback. Repeated unconfigured, malformed,
5xx, timeout and transport-failed settings reads exercise commands, callbacks and silent relay
retries, log throttling and one delivery after recovery, for language and optional timezone.
The bound-product drift/lint regression is parameterized for v1, v2-only and mixed v1/v2 and
retains the generated ruff/xenon limits.
The slow `test_v2_bound_product_passes_make_typecheck` runs the product's exact `make typecheck`
and checks both service banners and all output for errors. It runs in the existing slow
Test Copier Template leg; no local product typecheck or real Redis run is authorized.
The fast `test_v2_typecheck_dispatch_uses_selected_product` mocks the subprocess and verifies
that each fixture resolves to its own product directory before the CI-only command is invoked.

`test_bound_product_integration.py` adds two CI-only slow cases for the generated backend,tg_bot
product's unchanged `make test-integration`, default-bound with exact candidate tooling:
published reminders 0.5.0/textparse 0.1.0 through the default live catalog, and the candidate
commit's tg-channels release. For channels, the test exports the candidate commit's catalog,
pending release metadata and package tree into a scratch catalog source. Only when
`packages/pending-releases.yaml` holds a tg-channels entry does it append that entry to the
scratch catalog (the public catalog lists published tags only); otherwise it uses the candidate's
committed catalog unchanged. It checks the newest entry against the package manifest, creates a
synthetic release tag only in that scratch source and installs with `--catalog-source`. That
scratch tag is a test artifact, distinct from the real published tag the mandatory runner proof
requires. Channels verifies its platform environment
declarations and, as in a real product CI, the product `.env` has no `PLATFORM_KEY` or
`PLATFORM_BASE_URL`: the package starts not configured and makes no platform request. The fast
`test_candidate_channels_source_releases_exactly_the_candidate_commit` checks that scratch
release against the real catalog reader and package-source export.
The test checks Makefile, integration Compose and test bytes before and after execution;
the required `bound-product-integration-<run-id>` artifact preserves complete command output,
exit status, candidate/component identities and contract hashes, including failed runs.
Fast Compose tests cover image-seeded workspace environment volumes and the bot wheel/sync
ordering for both backend-only and backend,tg_bot shapes. Workers do not run these Docker or
published-package proofs locally.

## Core host contract

`tests/tooling/test_host_contract.py` covers the core `language` owner in the loader and in a fresh
product, manifest redeclaration (equal schema included) with its file and line, product commands
between core built-ins and modules, every refused product declaration form, collisions with both
sources (product/module, product/core, module/module), reserved names per shape, fail-closed
registration bypasses (names, aliases, attributes, strings and unreadable files), excluded
tests/generated/environment trees, a binding naming another language key, registry drift and
`--write`, and that the shipped template registry equals the render for both bot shapes.
`tests/tooling/test_preflight.py` is the `check-install` regression matrix on a synthetic product
and the actual tg-channels 0.1.2 source: fresh product `mechanical` (deterministic payload),
language redeclaration and a product `/channel` each `glue` with their sources and actions, all
simultaneous conflicts in one sorted list, and every incompatible reason, including an old core
and an unreachable catalog. Each case compares every product file, including environments, by
size, mtime and bytes before and after. A local catalog repository proves the explicit
catalog/ref/tag route without a network. The CLI JSON and exit codes and `kit add` refusing both
conflicts before any command or write are covered with a metadata-only wheel.

`tests/copier/test_host_contract.py` repeats the matrix in a generated, synced backend,tg_bot
product with environment snapshots, then applies the returned glue through the product's own
manifest and `ProductCommand` file, regenerates, sees `mechanical`, installs the local
tg-channels source, binds the default and checks the registry order, the core `language` source,
the product's tg_bot unit tests, the bound drift/lint regression and
`command_registry_scenarios.py` in RU and EN: product, built-in and module commands and the core
unknown reply through a real PTB `Application` with only Bot API sends and the backend transport
replaced, plus fail-closed refusal of an undeclared product command and of a module catch-all.
Bypass forms fail generation before any write and `python -m framework.host_contract` with their
location; the standalone shape lints and rewrites its registry. The slow case (CI only) refuses
the real `kit add --wheel` before writes, then installs the built wheel after the glue and binds.
The runner proof adds the RU/EN unknown-input and `/channel` checks through its fake Bot API.

Review 11 repairs are covered in the same suites. Tooling tests refuse unpacked, duplicate and
extra `ProductCommand` arguments before any registry write while admitting the positional and
named forms the runtime accepts; flag `context.application.handlers` callback/clear/remove and
`getattr(application, "handlers")` but not ordinary `handlers`, `callback` or `clear` fields;
fail closed on list, invalid-YAML and incomplete manifests; and classify a retained binding that
names core language as its timezone, or another language key, as product glue at its file and
line (as a package default: incompatible). Preflight fixtures are real empty virtualenvs
(`venv`, no installs); a host venv symlinked from outside the product, an empty placeholder and
a failing interpreter are `environment_invalid`, unreadable metadata `artifact_unavailable`, and
a malformed manifest is versioned JSON with exit 4 from the CLI. `kit add` refuses a retained
conflict before any command or write. In a generated product the retained conflict refuses
generation and bind with unchanged files and environments, and the glue leads to `mechanical`,
install and bind. `command_registry_scenarios.py` dispatches a product command that tries to
replace the core fallback callback, clear, extend and shrink the registry; each attempt fails
and the core unknown reply and `/start` still answer. The product template's settings test
refuses user-scoped `language` with no stored row and round-trips product RU/EN; the runner
proof repeats the refusal against the real deployed backend.

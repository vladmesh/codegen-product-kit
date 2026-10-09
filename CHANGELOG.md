# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Runner proof (`.github/workflows/runner-proof.yml`, required on every PR and main push): a fresh
  Copier backend,tg_bot product from the exact candidate is installed with tg-channels by the
  orchestrator's real `run_install`, passes its own CI job, builds its runtime
  images with its main job's preparation and Dockerfiles, publishes them to an isolated CI
  registry and runs them by digest against the pinned platform's real auth and Caddy. Only the
  reader (contract fixture) and the Telegram Bot API transport are replaced; an unknown key is
  refused at ingress before the registered key delivers the fixture post to the chat. SHA-bound
  evidence is uploaded; fork or keyless runs fail. Other repositories call it with exact pins
  through `workflow_call`. See [the runner proof](docs/RUNNER_PROOF.md).
- Runner proof release matrix and explicit proof modes: a `coexistence` leg installs the published
  reminders first and then tg-channels, checks their settings, jobs, events and bindings side by
  side and delivers both a channel post and a reminder. `proof_mode=candidate_release` installs a
  pending package release through an isolated fixture repository (exact candidate, fixture
  catalog commit, local intended tag) and a loopback catalog HTTP fixture, with the executor,
  probe and kit unchanged; `published_release` uses the live catalog and tag. The mode and
  expected version are always explicit and recorded; there is no fallback.
- `packages/pending-releases.yaml` pins package releases prepared in source but not yet tagged;
  production readers ignore it and the public catalog lists published tags only. The newest
  catalog release of every package is now checked against its annotated tag, and the source at
  HEAD against that release or its pending entry.
- tg-channels 0.1.2 in the catalog: its tag `packages/tg-channels/v0.1.2` (object `4f5918d`,
  commit `29f481f`, package tree `676ee70`) was published on 2026-10-09 before this entry; the
  pending entry is removed and nothing is pending. New products' `kit add tg-channels` selects
  0.1.2 once this is on the default branch; existing products are not upgraded.
- Runner proof catalog modes, explicit and recorded beside the proof mode: the runner now defaults
  to `published_release` of tg-channels 0.1.2, reads the tag from the real remote first and
  requires its pinned object, commit and tree. A pull request plans and installs against the
  candidate's own committed catalog served as an isolated, prospective `HEAD`
  (`candidate_snapshot`, with the real remote's package tags by object id); a push to `main` uses
  the real default branch's catalog with no fixture (`remote_head`) and is the only proof of the
  published path. `candidate_release` remains, only explicitly, with `pending_fixture` for a
  future pending release. Planner, HTTP reads and probes must reach one catalog digest.

### Fixed

- tg-channels 0.1.2 (package release, tagged 2026-10-09 and added to the catalog afterwards): the
  timer consumer runs on `tg_channels.tick`, the job name the core generates and fires for the
  package, instead of `tg-channels.tick`, which the core never fires; 0.1.0 and 0.1.1 therefore
  never polled the reader or delivered a post. No core naming, event or template change. A
  regression feeds the name from the generated `JOB_TIMERS` of the shipped manifest to the
  package's consumer. See [tg-channels 0.1.2](docs/releases/tg-channels-0.1.2.md).
- Generated products prepare Python environments through one command,
  `sh scripts/prepare-env.sh [--runtime] ENV...`, used by `make setup`, PR CI, the main image job
  and every Dockerfile stage with the environments that stage needs. The main image job of a bound
  backend,tg_bot product now prepares `root backend tg_bot` and no longer fails cold generation
  with `BindingEnvironmentError: tg_bot environment is not installed`. The command uses frozen
  locks, refuses unknown or missing requested environments before syncing and stops at the first
  failed sync (setup previously ignored the failure of every service but the last); runtime images
  still receive runtime dependencies only. `make typecheck` now fails when any service fails, and
  the product's PR CI runs it. New products only; existing products are not migrated.

- tg-channels 0.1.1 (package release, prepared, not published): the package starts without
  `PLATFORM_BASE_URL`/`PLATFORM_KEY`, logging one warning that names the missing variables.
  Platform actions answer the existing `not_configured` error, local list/remove keep working
  and the timer makes no platform request. Configured behaviour is unchanged. The slow
  published-package lane installs the candidate commit's tg-channels release with no platform
  values in the product `.env`. No core, template or `CORE_VERSION` change. See
  [tg-channels 0.1.1](docs/releases/tg-channels-0.1.1.md).

## [0.10.1] - 2026-10-08 (prepared, not published)

### Fixed

- Bound backend,tg_bot products run generation in their integration container against
  image-built, product-owned virtualenvs. Anonymous volumes mask host environments under
  `/workspace`; the backend dev image includes the bot's locked dependencies and local library
  wheels for binding validation. The runtime image and product integration test contract remain
  unchanged. Published reminders 0.5.0/textparse 0.1.0 and tg-channels 0.1.0 default bindings
  have CI-only coverage through the product's own `make test-integration`, with retained output
  and contract hashes. Channels retains its platform declarations and uses explicit inert test
  values without platform calls. See [0.10.1 preparation](docs/releases/0.10.1.md).

## [0.10.0] - 2026-10-07 (prepared, not published)

### Added

- Finite Telegram binding v2 beside v1: RU/EN text maps, one explicit product language setting,
  plain text creation with declared action errors, optional-filter lists and remove buttons,
  no-argument result-list show, and localized events/date display. Bind declares language in
  the bot manifest; every valid update reads the product value without a default language.
  Missing/invalid values produce a bilingual setup reply. `CORE_VERSION` becomes 2.4.0;
  packages shipping v2 defaults or action error metadata require `>=2.4,<3`.
- Synthetic bilingual bound-product handler/relay coverage, v2 ruff/xenon drift regression,
  CI-only product typecheck and pinned pre-v2 generated-file equality. Reminders and every
  v1 grammar constraint remain unchanged. See [0.10.0 preparation](docs/releases/0.10.0.md).

## [0.9.0] - 2026-10-07 (prepared, not published; supersedes untagged 0.8.2)

### Added

- Generic package environment sources `platform_key` and `platform_base_url` carry service,
  scopes, quota and HTTPS endpoint data from `package.yaml` into the product env contract.
  Conflicting package or product sources fail generation; unspecified requirements retain
  their existing behavior. No concrete platform service appears in kit core. The committed
  env JSON schema adds both forms under contract v1. `CORE_VERSION` becomes 2.3.0; packages
  declaring the new block require `>=2.3,<3`, while existing compatible manifests need no change.
  [0.9.0 preparation](docs/releases/0.9.0.md) records the combined release and upgrade path.

### Fixed

- Generated Telegram bindings and relay now meet the product's existing xenon complexity
  thresholds. Command kinds, timezone validation, callback ownership and relay delivery use
  focused helpers without changing commands, retries, claims or replies. The real bound-product
  Copier regression runs the generated Makefile's ruff and xenon steps after each of two
  drift-free regenerations. No lint exclusions or thresholds change. No package release or
  `requires_core` change is needed.
  The untagged [0.8.2 preparation](docs/releases/0.8.2.md) is superseded by 0.9.0 so one core tag
  carries both this fix and the platform environment declaration.

## [0.8.1] - 2026-10-07 (prepared, not published)

### Fixed

- Generated Python is formatted again after the generator's `ruff check --fix`, so a product with
  a non-empty binding passes its own CI: regeneration, the generated-tree drift check and
  `make lint`'s format check now agree on `services/tg_bot/src/generated/bindings.py`, whose
  `pformat` parentheses UP034 had stripped into unformatted layout. A non-slow copier test
  pins the sequence twice against a real bound product, real generator and pinned ruff.
  [0.8.1 preparation](docs/releases/0.8.1.md) gives the defect, versions and update path.

## [0.8.0] - 2026-10-05

### Fixed

- Generated tg_bot lifecycle unit tests isolate binding startup/shutdown so a product with a
  default binding passes its unit leg without Redis. Broker assertions remain, with hook order
  and error cleanup coverage added; runtime and protected product code are unchanged.
- Textparse refuses textual continuations through contiguous punctuation joiners and
  the bounded approximation qualifiers `ish`, `approx`, `roughly`, `or so`, `give or take`
  and `thereabouts` before resolving any of its three forms. Exact forms and sentence
  punctuation retain their time and remainder behavior.
- The slow package crash/recovery proof registers its due-event reader before creating the
  pending emission, so a live backend timer's recovery cannot precede the reader's stream cursor.
  Crash, database-state, recovered-delivery, event-identity and stream-count assertions remain.
- Catalog parsing rejects `extends` under `packages` and directs the author to `extensions`.
  Moving an extension into the package list can no longer discard its parent requirement and
  bypass installation preconditions. Other unknown additive keys remain accepted.

### Added

- CI-only native upgrade of an untouched released 0.7.1/core-2.1 backend,tg_bot product to the
  exact candidate core 2.2/tooling revision, followed by default-remote reminders/textparse
  installation, binding, generation, typecheck and handler scenarios. Required artifact
  `core-21-upgrade-smoke-<run-id>` records provenance, clean baseline, conflict absence and
  protected-file ownership. [0.8.0 release preparation](docs/releases/0.8.0.md) gives the
  update/install sequence and separate PO publication boundary.
- `kit bind <package> --default|--file <path>` validates the installed backend package and bot
  library before creating product-owned bindings and a manifest timezone declaration. Existing
  regeneration emits caller-aware Telegram commands/callbacks and a Redis Stream relay with
  seven-day completed-delivery dedupe, retryable claims and pre-start backlog replay. Callback
  contexts expire after ten minutes; timezone values require separate `/settings/set` setup.
  The existing published-remote CI lane now proves released reminders/textparse binding and
  real Redis wiring. [Bindings increment](docs/releases/bindings-increment.md) records ownership,
  crash limits and remaining sprint boundaries; published component trees/refs are unchanged.
- Core façade 2.2.0 admits optional protocol-v1 action/default-binding metadata. Reminders
  0.5.0 declares typed create/list/cancel operations verified against real OpenAPI, names
  the existing due message's required string recipient, and ships an English binding resource.
  Its finite validator checks references, argument schemas, nonempty/null-result guards,
  exact presets and original-text context against actual installed manifests. The independent
  package is published; the core tag awaits [0.8.0 preparation](docs/releases/0.8.0.md).
- Required slow Copier CI adds published textparse default-remote install/interpreter/image
  proof, retaining local fixtures and uploading a candidate-bound provenance receipt.
  Actual reminders 0.3/0.4 source fixtures preserve old installs without relabeling 0.5.
- `codegen-kit-textparse` 0.1.0 source, the three-form English `when(text, lang, now, tz)`
  library with explicit clock/zone, elapsed UTC offsets and refusal of DST gaps/folds.
  `kit add textparse` resolves its independent tag and verifies/installs a plain dependency
  into tg_bot. The tg_bot image copies local locked wheels before sync. Narrow corpus,
  no-write artifact refusals and a CI-only real service/image proof accompany the source.
  The independent package is published: [release operation](docs/releases/textparse-0.1.0.md).
- Additive catalog v1 component signatures: package actions, curated library recommendations,
  default-binding resource references, stateless libraries with declared primary outputs, and
  extensions with parent version requirements. The public pure `primary_output_matches` API
  infers semantic compatibility from that primary output only. `kit add` checks extensions
  against the product's active installed parent before fetching/building or changing files.
- Offline compatibility coverage executes the unchanged kit 0.7.1 catalog loader. Reminders
  0.3.0/0.4.0 and core 2.0/2.1 selection are preserved. Extensions remain empty; textparse source
  and its curated recommendation are now declared. Reminders 0.5 adds actions and a binding
  resource; installed 0.3/0.4 manifests confer neither.
  Contract and deferred runtime boundary: [docs/CONTRACTS.md](docs/CONTRACTS.md#additive-component-metadata-in-v1).

## [0.7.1] - 2026-10-03 (prepared, not published)

### Fixed

- A generated product that installs a package (`kit add reminders`) passes its own unit-test leg
  (`make tests`) without Redis again. The backend's two lifespan tests in `test_job_timers.py`
  started every active package's real runtime, and reminders' consumer failed with a Redis
  `ConnectionError`; they now replace package startup and shutdown with recording no-ops and still
  assert the startup, timer-loop and shutdown order and the loop count. `CORE_VERSION` stays 2.1.0
  and no package release is needed. Upgrade facts: [0.7.1 release notes](docs/releases/0.7.1.md).

## [0.7.0] - 2026-10-02

### Added

- Package catalog `packages/catalog.yaml` with independent package releases tagged
  `packages/<name>/v<version>`. `kit add <name>` resolves, builds and verifies a released package
  from the live catalog; `--wheel` installs an explicit artifact of any catalog package. Release
  procedure: [docs/CONTRACTS.md](docs/CONTRACTS.md#package-catalog-and-releases).
- Core timer loop (`CORE_VERSION` 2.1.0): packages may declare `timers` in `package.yaml`, and the
  backend fires each declared timer job once per slot through the existing jobs core, so
  `codegen-kit-reminders` 0.4.0 emits due reminders without an external `POST /jobs/fire`.
  Products without timers run no loop. Upgrade facts and the release-tag rule:
  [0.7.0 release notes](docs/releases/0.7.0.md).

### Changed

- **Breaking for reminders callers.** The backend core verifies the caller of a package route once:
  a trusted in-product service (the tg_bot) sends `X-Identity-Capability` (the new generated secret
  `USER_IDENTITY_CAPABILITY`), `X-User-Channel` and `X-User-External-Id`, and
  `codegen_kit.caller_identity` resolves that active user to `"<channel>:<external_id>"`.
  `codegen-kit-reminders` 0.4.0 takes the owner from it: `user_ref` is gone from the reminders
  request body and query, and list and cancel act on the caller's own reminders only. The generated
  tg_bot gains `BackendClient.request_as_telegram_user`. Contract:
  [docs/CONTRACTS.md](docs/CONTRACTS.md#core-caller-identity-v1).

## [0.6.4] - 2026-09-29 (prepared, not published)

### Fixed

- Generated deployment keeps raw IPv4/IPv6 hosts for native SSH and ssh-action, and brackets IPv6
  only in native SCP destinations. Missing copy configuration and bracketed host secrets fail
  clearly; compose sources, target directory and three-attempt retry policy are preserved.
- Generated deployment pins `ubuntu-24.04`. Job names and action versions are unchanged.
- Rendered-shell and nonconnecting OpenSSH regressions cover all supported product shapes.
  Real Copier updates from exact 0.6.3 retain owned data and report workflow conflicts.
  See [0.6.4 release and upgrade notes](docs/releases/0.6.4.md); existing repositories need an
  explicit reviewed update, in addition to any orchestrator scaffolding pin change.

## [0.6.3] - 2026-09-28

### Fixed

- Generated backends persist 64-bit user IDs in `users.id`, `user_channels.user_id`, and
  `settings.subject_id`. A forward Alembic revision preserves the released 0.6.2 schema and
  widens the user sequence; downgrade refuses values or sequence state outside int32.
- Shared logging suppresses HTTP request diagnostics below WARNING and redacts Telegram token
  paths in rendered messages and exceptions, including application DEBUG and console output.
- PostgreSQL integration and generated backend/Telegram logging regressions execute in template
  CI. See [0.6.3 upgrade and release notes](docs/releases/0.6.3.md).

## [0.6.2] - 2026-09-08

### Fixed

- Generated main-push image jobs now install the committed locked backend environment before
  generation, so allowlisted package entry points are available without weakening package
  resolution or requiring a product-side CI repair.

## [0.6.1] - 2026-09-07

### Fixed

- Package environment requirements now reuse compatible product-owned typed declarations and fail
  early with the variable and unmet invariant named when an overlap is incompatible. Undeclared
  requirements retain the user-secret fallback, while repeated package requirements compose
  deterministically at the strictest requiredness.
- Package environment acceptance coverage now lives in the CI-collected `tests/unit` tree.

## [0.6.0] - 2026-09-07

### Added

- Package database access is now obtained through a caller-owned `package_database()` capability
  derived from the validated installed manifest. Callers can no longer select schemas by string.

### Changed

- **Breaking:** The generated package façade is now `2.0.0`; the arbitrary-string `package_base`
  and `package_session` functions are removed. `codegen-kit-reminders` 0.3.0 and the synthetic
  package use the owned database capability.
- Package migrations now run through Alembic's public command/environment API while retaining
  core-before-package order and one schema-local version table per package.
- Root lint now checks Ruff formatting without modifying files. Generated settings and jobs
  registries use deterministic Python literals, and generated-product lint proofs use the
  product's pinned Ruff.
- Package consumer groups are created idempotently before live and recovery readers start, and
  package setting seeds run through the activated package set in the setting transaction.

### Fixed

- Redis drain proofs now require numeric zero lag, package migration reruns prove the version row
  was untouched, and activation-refusal tests use the public discovery boundary.
- Removed the unused Telegram `INSTALL_DEV_DEPS` build argument and linked the attested package RSS
  measurement from the repository documentation index.

## [0.5.0] - 2026-09-06

### Added

- Added the separately versioned `codegen-kit-reminders` package with one-time reminder HTTP routes,
  a package-owned PostgreSQL schema, externally fired ticks, and durable stable-ID due-event
  emission. Package manifests can now declare `deployment.modes`, whose only implemented and
  accepted value is `in_process`.

### Changed

- Package manifests declaring the `container` deployment mode are now refused with
  `UnimplementedDeploymentModeError` during generation and activation, because no generated product
  implements container delivery; `in_process` stays the only accepted value and the default.

- **Breaking:** Generated products now install `codegen-kit-tooling` from an exact Git commit
  recorded in the root `uv.lock`; the copied framework source and synchronization workflow are
  removed. The import name remains `framework`, and production service images exclude generators
  and validators.

- Recast the repository as the independent `codegen-product-kit`, documenting its origin and the
  planned service/container/package vocabulary without claiming an implemented package runtime.

- Removed the placeholder frontend and demonstration notification worker from Copier selection,
  generated Compose/env/tooling, tests, and product documentation.

- CI now pins setup-uv to its immutable v7.4.0 commit, an exact uv release, and its Linux x86_64 checksum, avoiding the mutable remote version manifest while retaining download verification.

- **Breaking:** Generated backends now include the versioned, manifest-backed core jobs v1
  contract. Capability-protected `POST /jobs/fire` records a caller-supplied command identity for a
  behaviour declared in `services/<service>/manifest.yaml` and emits `job_fired`; unprotected
  `POST /jobs/evidence` reads the recorded evidence back. The core schedules nothing itself: a
  product with no declared behaviour and no provider gains no container or worker.

- The generated core jobs contract now commits a fired command before it emits `job_fired`, and
  emits it from a single place behind the committed row's lock (`SELECT ... FOR UPDATE` on
  PostgreSQL). An event is therefore never published for an uncommitted command, and concurrent
  retries of one identity produce at most one emission. No contract, schema, route or migration
  changed.

- **Breaking:** Generated backends now include the versioned, manifest-backed core settings v1
  contract. `POST /settings/get` and capability-protected `POST /settings/set` store only
  Draft 2020-12 schema-validated product or user-scoped values declared by explicit service
  manifests. Existing `services/*/spec/manifest.yaml` files remain ignored; this forward-only
  contract does not migrate earlier generated projects.

- **Breaking:** Replaced the generated public user CRUD and Telegram environment audience with a
  persisted `User`/`UserChannel` authority. Telegram now admits only identities resolved as
  `active`; `POST /users/grant` is the sole activation operation and requires the generated backend
  grant capability.
- Added the protected `POST /users/revoke` capability. It idempotently deactivates an existing
  identity through the same `User.status` admission gate and does not create unknown identities.

### Fixed

- `codegen-kit-reminders` no longer publishes `reminders.due` inside the transaction that holds the
  `due_emissions` rows: a tick now reads the unconfirmed outbox in one short transaction, publishes
  outside every row lock, and confirms each accepted publication in its own transaction, so a
  stalled Redis cannot hold PostgreSQL locks. Durability, the stable per-reminder event UUID, and
  retry of unconfirmed rows are unchanged; overlapping ticks may now add a duplicate stream entry
  carrying that same UUID, which the generated consumer's `(consumer_group, event_id)` guard
  collapses.

## [0.4.0] - 2026-08-30

This release targets newly generated projects. Updating projects generated from earlier template
versions is not supported; regenerate from the current template and port application-owned code.

### Added

- Generated backend import-boundary regression coverage, including inert package imports, explicit
  ORM model registration, ASGI startup, Alembic offline generation, and generated-project typecheck.
- A canonical broad-check workflow that snapshots dirty template worktrees so Copier tests exercise
  the exact content under review.

### Changed

- **Breaking:** Backend package initializers no longer provide application, settings, ORM, model,
  or repository re-exports. Runtime consumers must import explicit modules; declarative ORM types
  now live in `core.orm`, and handwritten models must be registered in `app.models.registry`.
- **Breaking:** Removed the unused environment-contract JSON-schema export and
  `Settings.database_url`; consumers must use explicit sync or async URLs.
- **Breaking:** Global `shared/spec/events.yaml` entries are now publisher-only.
  Removed `subscribe` metadata and operation `events.message_model` are rejected.
- **Breaking:** Removed the unused service-manifest subsystem. The generator now
  ignores legacy `services/*/spec/manifest.yaml` files.
- **Breaking:** `datamodel-code-generator` is required. Schema generation now fails before any
  later artifacts are generated when that dependency is unavailable.
- Generated-project typechecking and pre-commit formatting now use the project toolchain directly.
- Framework and template CI have distinct ownership: framework tests run once, Copier tests own the
  module contracts, and one generated backend candidate exercises setup, typecheck, and pre-commit.
- Living architecture, development, testing, contributor, and service documentation now describe
  only current commands, paths, ports, ownership boundaries, and generation contracts.

### Removed

- The orphaned service-scaffolding subsystem, dead root Compose/integration assets, and stale Copier
  exclusions.
- Historical plans, brainstorms, backlog, and issue ledgers from product documentation after their
  still-relevant technical work was extracted.
- The unused service-manifest parser/model/template path and unused global subscriber metadata.
- Compatibility shims, eager backend package imports, stale narration, generated boilerplate
  docstrings, misleading quality tests, and duplicate workflow assertions.

### Fixed

- Generated architecture documentation no longer tells operators to create
  `APP_SECRET_KEY` as a GitHub repository secret. The deployment environment
  contract correctly owns it as a generated secret.
- Generated backend imports satisfy the project-neutral typecheck contract, including projects whose
  slug is not `test_project`.

## [0.3.6] - 2026-07-27

### Added
- Bot access is declared in the tg_bot environment contract instead of being written
  per project by an engineering agent: `TG_BOT_ALLOWED_TELEGRAM_IDS` names the
  audience (empty means public) and `TG_BOT_TEST_TELEGRAM_ID` admits one temporary
  identity so a private bot can be tested. The test identity stays out of the
  audience list, and removing the value revokes it with no residual state.
- Typed environment-contract baseline fragments for infrastructure, backend and
  Telegram bot modules, with schema validation in template tests.
- Generated-project CI now checks static environment usage against contract
  fragments and uploads a commit-bound canonical contract artifact.

## [0.3.0] - 2026-07-11

Release of the dogfood sprint: two smoke runs, a 3x2 task/head matrix and a control
run, each followed by a fix wave (#27-#42). Full reports live in the operator's
control-panel repo (docs/dogfood/).

### Added
- REST router generator: a domain spec with a `rest:` section now produces a FastAPI
  router and registry wiring; `events.publish_on_success` publishes on the REST path
  (the `users` vertical does this for `user_registered`) (#40)
- Worker-mode make targets for external orchestrators and port-less runs:
  `worker-start`, `worker-stop`, `worker-clean`, `smoke-probe`, `worker-call` (#41, #42)
- Top-level Compose `name: ${COMPOSE_PROJECT_NAME:-<slug>}` — manual `docker compose`
  gets a deterministic project name; explicit `--project-name` still wins (#42)
- `compose.local.yml` layer: host port publishing split from the dev layer; worker mode
  runs base+dev with no published ports (#33)
- `make dev-clean` (full teardown incl. volumes) and `make ps` (#32, #35)
- `BACKEND_PORT` in generated `.env`, parameterized datastore host ports
  (`POSTGRES_HOST_PORT`, `REDIS_HOST_PORT`) for parallel runs (#27, #35)
- Placeholder-token idle mode for standalone `tg_bot` — the container stays up and
  logs a warning until a real token is configured (#32)
- `infra/README.md` contract for external orchestrators: service DNS names, network
  rules, compose modes (#34)

### Changed
- Module selection moved from post-generation `rm -rf` tasks to templated copier
  `_exclude`: unselected modules are never rendered, generation is quiet, and
  `copier copy` no longer requires `--trust` (#36)
- `make makemigrations` works from the host and in worker mode: db comes up without
  published ports, `upgrade head` runs before autogenerate, `SKIP_INFRA_START=1`
  supports pre-provisioned databases (#30, #41)
- `make setup` no longer fails the whole bootstrap on user-code lint errors; deptry
  config aligned with `[dependency-groups]` across modules (#38)
- Dev compose runs services on image venvs instead of broken host-venv mounts (#28)
- Standalone `tg_bot` registered with its real service type instead of
  `python-faststream` (#31)

### Fixed
- Docs brought in line with reality: bootstrap via `uvx copier` with `--vcs-ref=HEAD`
  and no `--trust` (#29, #37), standalone-aware `tg_bot` AGENTS.md and base image
  version (#39), ARCHITECTURE.md matches the generated router registry (#40),
  lint gates (xenon thresholds) and `make setup`-first workflow documented (#39)

## [0.2.0] - 2026-03-01

### Added
- `.dockerignore` in template — prevents host `.venv/` from contaminating Docker builds
- Frontend service in `compose.base.yml` — `compose.prod.yml` extends now works for full config
- Conditional broker in `lifespan.py.jinja` — backend-only no longer requires `REDIS_URL`
- 10 new regression tests in 3 classes:
  - `TestDockerReadiness`: dockerignore, login shell, lifespan broker, compose prod/dev validation, health assertion match
  - `TestCIWorkflowCorrectness`: standalone CI cleanup
  - `TestFormattingQuality`: services.yml blank lines
- Standalone tg_bot tests (`TestStandaloneGeneration`) — 7 tests for `modules=tg_bot`
- `@pytest.mark.slow` for `make setup`/`make lint` integration tests
- `make test-copier-slow` target for slow tests
- Copier tests re-enabled in pre-push hook and CI

### Fixed
- `bash -lc` → `bash -c` in `compose.dev.yml` — login shell was resetting Docker PATH
- Health integration test assertion `"healthy"` → `"ok"` to match actual endpoint
- CI Clean up step wrapped in backend conditional — no longer runs for standalone
- `services.yml.jinja` Jinja whitespace trimming — no more excessive blank lines
- Copier tests use `.venv/bin/copier` and `.venv/bin/ruff` instead of bare commands
- Copier test fixtures refactored to session-scoped (4 copier runs per session instead of ~40)

### Changed
- `lifespan.py` renamed to `lifespan.py.jinja` (now conditionally includes broker code)
- Copier tests fully rewritten — 68 fast + 5 slow tests (was 55 broken/skipped)

## [0.1.0] - Previous Version

### Added
- Initial spec-first framework with code generation
- Modular service selection (backend, tg_bot, notifications, frontend)
- Copier-based project generation
- FastAPI + PostgreSQL backend module
- Telegram bot with FastStream
- Notifications worker
- Domain operation specifications
- Client generation from manifests
- Event-driven architecture support

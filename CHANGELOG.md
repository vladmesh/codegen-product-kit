# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added the separately versioned `codegen-kit-reminders` package with one-time reminder HTTP routes,
  a package-owned PostgreSQL schema, externally fired ticks, and durable stable-ID due-event
  emission. Package manifests can now declare in-process and container deployability, while only
  in-process activation is currently implemented.

### Changed

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

# Contract changelog

## 2026-10-09

- Façade `2.5.0`, package protocol v1 unchanged: the [core host contract](CONTRACTS.md#core-host-contract-v1).
  Core owns the product-scoped `language` setting; manifests, packages and bind may not declare it.
  One generated tg_bot registry registers core, product (`ProductCommand` in
  `services/tg_bot/src/commands.py`) and bound module commands and answers unknown input in the
  core language. Generation, lint, add and bind refuse conflicts before writing; `kit check-install`
  returns result version 1 (`mechanical`, `glue`, `incompatible`) without writing.

## 2026-10-03

- No contract change. Kit 0.7.1 fixes the generated backend's lifespan unit tests, which started
  installed packages' real runtimes and so needed Redis; the façade stays `2.1.0` and package
  protocol v1 is unchanged. See the [0.7.1 notes](releases/0.7.1.md).

## 2026-10-02

- The backend core establishes a verified caller identity for package routes. The `codegen_kit`
  façade (still `2.1.0`, unreleased) exports the FastAPI dependency `caller_identity`: it requires
  exactly one `X-Identity-Capability` equal to the new backend `generated_secret`
  `USER_IDENTITY_CAPABILITY` (consumers `backend` and `tg_bot`), one `X-User-Channel` and one
  `X-User-External-Id`, answering 401 otherwise; it resolves them in `user_channels`, answers 403 for
  an unknown or inactive user, and yields `"<channel>:<external_id>"`. Reminders 0.4.0 routes depend
  on it and accept no `user_ref` input; `DELETE /reminders/{id}` of another user's reminder is 404.
  The stored `user_ref` and the `reminders.due` payload carry the canonical form. The generated
  tg_bot sends the headers through `BackendClient.request_as_telegram_user`. See
  [Core caller identity v1](CONTRACTS.md#core-caller-identity-v1).
- The kit core owns a timer loop. Package protocol v1 gains the optional `timers` manifest field
  (`[{job, every_seconds}]`, 10 s to 1 day, job arguments exactly a required date-time `at`), refused
  at the manifest model with `InvalidPackageTimerError`. Generation records active timers as
  `JOB_TIMERS` in `jobs_schemas.py`; the backend lifespan starts one loop when it is non-empty and
  fires each timer job once per slot through `JobsController.fire` with identity
  `core-timer:<job>:<slot>` and `fired_by_run=core-timer`. The façade is `2.1.0`.
  `codegen-kit-reminders` 0.4.0 declares a 60-second `tick` timer and requires core `>=2.1,<3`; the
  catalog keeps 0.3.0 for core 2.0.0. See [Core timer loop](CONTRACTS.md#core-timer-loop) and the
  [0.7.0 notes](releases/0.7.0.md).
- Packages are released independently of the kit core. `packages/catalog.yaml` (format version 1,
  validated by `framework/catalog.py`) lists every package with its summary, capabilities, settings,
  environment and released versions; a release is the annotated tag `packages/<name>/v<version>`.
  `kit add <name>` without `--wheel` reads the catalog live, picks the newest version admitting the
  product core, builds the tagged source with `uv build`, verifies the wheel against the catalog and
  runs the existing install. `kit add <name> --wheel` accepts any package of the tooling's catalog.
  See [Package catalog and releases](CONTRACTS.md#package-catalog-and-releases).

## 2026-09-08

- Generated main-push image jobs now run the root frozen tooling sync and the backend frozen runtime
  sync before generation. A generated-product CI proof builds the real reminders wheel, installs it
  through `kit add reminders`, removes the populated backend environment, reproduces the retained
  `listed package has no installed entry point` failure without the backend sync, then proves the
  rendered locked preparation restores the real entry point and leaves package artifacts current.

## 2026-09-07

- Package environment requirements now reuse compatible product-owned typed declarations, preserving
  the product's literal, allocation, derived-value, or secret source. Generation and `kit add` fail
  early with a named invariant for incompatible overlaps, while undeclared requirements retain the
  user-secret fallback and repeated package requirements collapse to the strictest requiredness.
- **Breaking:** The package façade is now `2.0.0`. `package_database()` derives an ownership
  capability from the calling installed package's validated manifest; the schema-selecting
  `package_base(schema)` and `package_session(schema)` seams are removed. The capability creates
  owned ORM bases and schema-local sessions, and unowned callers fail before database access.
  `codegen-kit-reminders` 0.3.0 adopts the capability.
- Package Alembic resources now provide a standard `env.py` and are invoked with public
  `alembic.command.upgrade`; core migrations still run first and package order, schemas, and
  version tables remain isolated.
- Package protocol v1 now supports package-owned `setting_seeds` at product scope. The existing
  capability-protected settings write dispatches an exact-key callback through the already activated
  package set and shares its transaction, so callback failure rolls back the setting and package
  state together. The façade is `1.3.0`. `codegen-kit-reminders` 0.2.0 uses the contract to create
  one deterministic past-due reminder idempotently from `reminders.reminder_owner_ref`.
- Consuming packages now own establishment of their declared fixed Redis Stream groups before live
  or recovery consumption begins. The reminders package creates `job_fired` and its
  `events:package:reminders` group with idempotent `MKSTREAM` semantics, preserving an existing
  cursor and pending entries while allowing connection and protocol failures to abort startup.

## 2026-09-06

- `deployment.modes` now refuses a declared `container` mode with `UnimplementedDeploymentModeError`
  in tooling validation and in runtime activation. No generated product creates an image, service or
  Compose entry for a package, so the manifest may not promise that delivery form. `in_process`
  remains the only accepted value and the default, and the declaration is kept so a future container
  implementation can lift the refusal. The package protocol version and `CORE_VERSION` are unchanged.
- `reminders.due` is now published outside every package transaction: the tick commits the due
  transition and its outbox rows, reads the unconfirmed rows in a short transaction, publishes with
  no row lock held, and confirms each accepted publication separately. A stalled transport no longer
  holds PostgreSQL locks. There is still exactly one outbox row and one stable event UUID per due
  reminder; overlapping ticks may add a duplicate stream entry carrying that UUID, which the
  generated `(consumer_group, event_id)` consumer guard collapses.

## 2026-09-05

- Added `kit add reminders --wheel <artifact>` and a two-product CI proof. The command installs the
  artifact, updates dependency and manifest metadata, synchronizes the backend environment, and
  regenerates the product contract. Two independently generated products use the same wheel; one
  activates it without authored source changes and the other consumes `reminders.due` through a
  generated protocol subscriber.
- Added `codegen-kit-reminders` 0.1.0 as the first independently versioned package: one-time reminder
  HTTP routes, package-owned migrations, an externally fireable `reminders.tick`, and durable
  `reminders.due` outbox emission with a stable logical event identity across backend restarts.
  Package manifests may now declare `deployment.modes`; in-process activation is implemented and
  container deployability is declaration-only without a package-protocol bump. The façade is `1.2.0`
  after adding optional stable metadata to `publish_event`.
- Removed the dead service-only settings/jobs duplicate validators. The shared service/package
  ownership registry is now the literal single refusal mechanism, so service-vs-service duplicates
  are reported under `Package contract merge failed:`. Consumed-event schema binding now documents
  its authoritative parsed JSON representation.
- Package protocol v1 now runs package Alembic revisions in exclusive PostgreSQL schemas and version
  tables, merges prefixed settings and jobs plus event/message schemas from the installed active set,
  and pins runtime activation to the package identities used by generation. The façade is `1.1.0`
  after adding compatible ORM, session, and event-publication seams.
- Package event consumers now bind to an existing product or package publisher, orphaned and
  schema-conflicting subscriptions fail generation, and normalized publisher identifiers have named
  collision refusal. Settings and jobs render the loader's single merged ownership registry, the
  build-time/runtime core versions are pinned by a regression test, and generated Python files are
  again covered by the product's Ruff format check.
- Package protocol v1 now provides the generated `codegen_kit` public façade, validated
  `package.yaml` declarations, real entry-point discovery, product allowlisting, router and lifecycle
  activation, named compatibility failures, and package import linting. Package database and merged
  settings, jobs, and events machinery remains explicitly deferred.
- The package boundary now defines `CORE_VERSION` as its own semantic façade version, rejects
  duplicate HTTP prefixes, cleans up partial lifecycle startup, and fails import linting closed on
  invalid installation metadata or site-packages paths.
- Package protocol v1 now requires `package.yaml` inside the entry point's package directory and
  refuses single-file or missing module roots during activation and import linting. The lint checks
  manifest identity and documents its entry-point-scoped scan. Lifecycle cleanup defaults to an
  empty started-package ledger and never suppresses cancellation.
- Product events now use Redis Streams with per-service consumer groups, automatic pending-message
  reclamation, generated versioned envelopes, and a PostgreSQL-backed idempotent-consumer helper.
- The kit's template workflow now builds the generated backend development image and runs the real
  Postgres/Redis durable-event integration suite.
- Root setup now installs locked project metadata with exact `ruff` and
  `datamodel-code-generator` pins.
- Generated products resolve the installable `codegen-kit-tooling` distribution from an exact Git
  commit recorded in the root lock. The Python import remains `framework`.
- Copier now creates that lock as a trusted generation task. The framework source copy and its
  synchronization commands are removed.
- Backend production images contain application dependencies only; generators and validators are
  confined to the Docker development target.

The release-oriented project history remains in the root `CHANGELOG.md`.

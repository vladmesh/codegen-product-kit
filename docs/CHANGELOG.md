# Contract changelog

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

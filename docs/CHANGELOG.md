# Contract changelog

## 2026-09-05

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

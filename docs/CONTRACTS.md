# Framework contracts

## Generated-product tooling

The repository root is the only source of the `codegen-kit-tooling` distribution. Its installed
Python package remains `framework`. Copier renders an exact Git commit requirement into each
product's root `pyproject.toml` and runs `uv lock`, so setup, generation, spec validation,
controller checks, and tooling tests all use the committed resolution. The lock-generation task is
why project bootstrap requires Copier's `--trust` flag.

The kit's template workflow passes the pull request head repository URL and head SHA as Copier data.
That commit is reachable from the contributor's remote, unlike GitHub's synthetic merge commit; push
runs use the current repository and commit. This override remains an exact Git requirement and is
used only while generating the workflow candidate. Ordinary users receive the resolved template
commit in the same requirement position.

Tooling is a development boundary, not an application runtime dependency. The backend Dockerfile's
`dev` target installs the root tooling lock for integration generation; its final `runtime` target
copies only the service environment and application sources. An audit of `template/services/`,
`template/shared/`, and generated Python sources found no runtime `framework` import.

## Package protocol v1

Package protocol version `1` is the stable boundary between an independently built Python wheel and
any generated product that installs it. The boundary is a generated in-product `codegen_kit` façade,
not a separately released runtime distribution. This avoids making the whole product core a public
runtime dependency. `CORE_VERSION` is the kit-declared semantic version of this façade and its
activation semantics; it is rendered into each product and is deliberately independent of the exact
Git SHA used to deliver `codegen-kit-tooling`. The initial v1 surface was `1.0.0`; package database,
session, and event-publication seams raise it to `1.1.0`. Backward-compatible public additions require
a minor bump, breaking changes require a major bump, and fixes that preserve the promised surface
require a patch bump. The package protocol version remains `1` across compatible additions. A
package imports `Package`, `CORE_VERSION`, `PACKAGE_PROTOCOL_VERSION`, `package_base`,
`package_session`, and `publish_event` from `codegen_kit`; product-specific `services.*`, generated
contracts, and application settings are not public API. `package_base(schema)` creates an independent
SQLAlchemy metadata registry, `package_session(schema)` exposes a transaction with a schema-local
search path through the core session factory, and
`publish_event()` uses the generated product transport. The unchanged wheel can therefore be
installed into another generated product with the same compatible core without rebuilding it.

### Package manifest

Each wheel's entry-point module must resolve to a package directory. It installs exactly one
`package.yaml` as distribution data, at `<entry-point-module>/package.yaml` inside that directory.
A single-file module, a missing module root, or a manifest installed anywhere else is not a protocol
v1 package. This location is fixed in v1 so two independently installed package module trees cannot
overwrite one shared site-packages-root manifest. Protocol v1 validates the manifest fail-closed and
rejects unknown fields. Its fields are:

| Field | Meaning in v1 | Activation enforcement |
|---|---|---|
| `protocol_version` | Integer package protocol version, exactly `1` | Enforced |
| `name`, `version` | Entry-point identity and package release identity | Enforced; missing identity has `MissingPackageIdentityError` |
| `requires_core` | PEP 440 specifier matched against `codegen_kit.CORE_VERSION` | Enforced |
| `provides`, `requires` | Provided and required logical interfaces | Enforced across the active set with named duplicate and missing-provider refusal |
| `package_dependencies` | Top-level Python imports allowed in addition to stdlib and `codegen_kit` | Enforced by the kit import lint |
| `http.prefix` | Absolute non-root mount prefix with no trailing slash, `//`, or path parameters | Enforced; malformed values have `MalformedPackagePrefixError` |
| `database.schema`, `database.migrations` | Package-owned PostgreSQL schema and `module:path` Alembic revision resource | Enforced; migrated by the core |
| `events.publishes`, `events.consumes`, `events.messages` | Package event names and inline Draft 2020-12 message schemas | Enforced and merged during generation |
| `settings_schema`, `jobs_schema` | Draft 2020-12 schemas merged under the normalized package-name prefix | Enforced and merged during generation with named duplicate refusal |
| `environment` | Named environment requirements and whether each is required | Enforced in the generated package environment-contract fragment |
| `resources` | Named distribution resource paths | Enforced as existing, non-traversing distribution resources |

The tooling API is `framework.spec.packages.load_package_manifest(path)`. Unknown fields raise
`UnknownPackageManifestFieldError`; invalid syntax and other schema errors raise
`PackageManifestError`. The runtime repeats activation-critical validation so production images do
not need the tooling distribution.

### Installation, discovery, and activation

The distribution declares one entry point whose name equals `package.yaml.name`:

```toml
[project.entry-points."codegen_kit.packages"]
weather = "weather_package:package"
```

Protocol v1 intentionally excludes dotted entry-point names: product manifests accept a name only
when replacing `-` with `_` produces a Python identifier, and the entry-point and package manifest
names must still match exactly. The module named on the right side must resolve to an installed
package directory; a top-level `.py` module and a target that resolves to nothing raise
`InvalidPackageModuleRootError` during activation and produce the same named lint violation.

The referenced object implements `codegen_kit.Package`: it exposes a FastAPI `router` and async
`startup(application)` and `shutdown(application)` methods. Install the wheel as an explicit backend
dependency, keep it in the backend lock, and add its entry-point name to
`services/backend/manifest.yaml`:

```bash
uv add --project services/backend \
  ./services/backend/packages/weather_package-1.0.0-py3-none-any.whl
```

```yaml
packages:
  - weather
```

Discovery uses installed distribution metadata, never module scanning or a catalog. The core
activates the package only when the entry point is installed and its name is listed. An installed but
unlisted package raises `InstalledPackageNotListedError`; a listed but absent entry point raises
`ListedPackageNotInstalledError`; an incompatible `protocol_version` raises
`IncompatiblePackageProtocolError`; and a `requires_core` mismatch raises
`IncompatibleCoreVersionError`. Two activated packages with the same `http.prefix` raise
`DuplicatePackageHttpPrefixError` before any package router is mounted. All abort application
startup. With `packages: []`, existing settings, jobs, events, and environment behavior is unchanged.

Generation and runtime activation are pinned to one active set: entry points installed in the
backend package environment and names listed in the backend manifest. Generation resolves that set
once, feeds it to every contract generator, and records package names, versions, and manifest
digests in
`codegen_kit._active_packages`; runtime refuses a changed manifest or wheel until generation is run
again. The backend site-packages path is explicit across the root-tooling/backend-environment split.

Package settings and jobs are emitted as `<normalized-package-name>.<local-name>`, where hyphens are
normalized to underscores. The existing service registries own duplicate detection, so collisions
between a package and a service, or between normalized package prefixes, name both declarers.
Package event names remain their declared stream names. Each published or consumed event has one
`events.messages` entry containing its generated model name and inline JSON Schema. Event-name and
message-model collisions with product or package declarations are refused before generation writes
the merged schemas and publishers. A service domain may subscribe to the package event and refer to
its model without editing `shared/spec/events.yaml` or `shared/spec/models.yaml`.

Package-provided interfaces must have one owner in the active set, and every required interface must
already have an active provider. Package environment requirements are merged into
`services/backend/packages/env.contract.yaml` as backend-consumed user-supplied secrets for local
and production. Repeated environment names are refused with both package owners named. Declared
resources must exist at their non-traversing distribution-relative paths.

`services/backend/scripts/migrate.sh` runs the core Alembic head first, then active packages in
manifest order. Each package gets its declared schema as the connection search path and its own
schema-local `alembic_version` table. Re-running the command is a no-op at every head. A product-local
wheel can be kept under `services/backend/packages/`, which is copied before dependency installation
in backend images.

The factory mounts each activated router under `http.prefix`. Once core connectivity is ready, the
backend lifespan calls package `startup` in manifest order. It calls package `shutdown` in reverse
order before closing core connectivity, but only for packages whose `startup` completed. Successfully
started packages are recorded on one application-state ledger; partial startup failure unwinds that
ledger, does not call `shutdown` on the failing or later packages, and does not let a shutdown failure
mask the original startup error. A package author therefore does not need to make `shutdown` tolerate
an incomplete `startup`. Core routers are registered first; if a package route has the same HTTP
method and fully resolved path as a core route, the core route wins, while non-colliding routes under
that package prefix remain available. Exact duplicate package prefixes are refused, while nested
prefixes such as `/a` and `/a/b` are accepted; final route collisions follow registration order. A
package connection check belongs in `startup` and must raise on failure. Package acceptance checks
must install the real wheel, resolve its real entry point, start the generated application, exercise a
prefixed route, observe lifecycle calls, run manifest validation, and run the import lint. The kit's
synthetic package performs these checks.

The generated product's `make lint` runs the installed-package import check. Package source imports
may target stdlib, `codegen_kit`, the entry point's one top-level module and its submodules, and
top-level modules named in `package_dependencies`. The scan is entry-point-scoped: it recursively
checks only the package directory named by the entry point. A second top-level module shipped in the
same distribution is not scanned; declaring it as a package dependency only permits imports of it
from the scanned tree. Importing product internals or an undeclared third-party package fails the
check. Each listed entry point must resolve to a recursively scanned package directory. A
single-file module, missing or empty source root, misplaced, missing, or ambiguous installed
`package.yaml`, manifest-to-entry-point name mismatch, missing distribution metadata, and an empty or
nonexistent explicit site-packages path fail the lint rather than producing a vacuous pass. This is
a source boundary, not a dependency resolver; normal Python packaging metadata still owns
installation of dependencies.
An editable install normally leaves sources outside site-packages, so if its distribution metadata
cannot locate the protocol directory there, import lint reports "sources could not be located" and
fails closed.

## Core settings v1

Every backend generated from this template provides the versioned core settings contract:

- `POST /settings/get` accepts `SettingGet` and returns `SettingValue`.
- `POST /settings/set` accepts `SettingSet` and returns `SettingValue`.

All three generated schemas carry `contract_version: 1`. A value is identified by its declared
`key` and by an explicit `scope`: `product` stores one product-wide value, while `user` requires a
positive local `subject_id`. Values are JSON and never inferred from prose or environment variables.
The database uniqueness boundary is `(key, scope, subject_id)`, so writing the same effective JSON
value is idempotent and user-scoped values cannot overwrite another subject's value.

Only settings declared in an explicit `services/<service>/manifest.yaml` may be written. A v1
manifest has a Draft 2020-12 `settings_schema` object with named `properties` and
`additionalProperties: false`. It is loaded separately from `services/<service>/spec/*.yaml`;
legacy `services/*/spec/manifest.yaml` remains ignored. The supported schema form deliberately has
no top-level `required` or `$ref`: each setting is independently written and no schema source may be
resolved indirectly. Duplicate keys across service manifests are invalid.

`POST /settings/set` requires exactly one `X-Settings-Capability` value matching the generated
`SETTINGS_WRITE_CAPABILITY` secret. The header is intentionally absent from generated schemas and
OpenAPI. It must not be logged, included in LLM-facing data, or used as a product setting. Reads do
not carry this deployment capability.

Environment variables remain startup, connectivity, platform, and secret configuration. A value
derived from the Product Brief or intended for a user to change belongs in a manifest-declared
setting instead.

## Durable product events v1

Every generated product event is appended to a Redis Stream. The stream name is the event name from
`shared/spec/events.yaml`, such as `job_fired`; generated code has no Redis channel pub/sub path for
product events. Each entry contains a generated envelope with `event_id` (UUID), timezone-aware
`occurred_at`, integer `schema_version` (currently `1`), and the declared message under `payload`.
Publishers create this metadata, while consumers deserialize the whole typed envelope.

Generated subscriber groups are named `events:<service>`. Each consuming service gets its own stable
group, preserving fan-out: replicas of one service compete within that service's group, while a
different service receives the same stream entry through its own group. Consumer names include the
role, hostname and process id. FastStream creates a group at `$` on its first start, so that first
start establishes the current stream tail and consumes only later entries. A deployment must start
and ready a new consuming service before allowing any event it must receive to be published; events
published before the group's first creation are deliberately not replayed. Once the group exists,
later downtime does not lose its backlog.

Every generated subscription has a live reader and a recovery reader in the same group. The recovery
reader uses FastStream's Redis `XAUTOCLAIM` support, with a configurable five-minute idle threshold
and five-second polling interval by default. Reclaim is based only on idle time: Redis cannot
distinguish a dead owner from a live handler that has run longer than the threshold. The transactional
idempotency guard, not the reclaim window, therefore guarantees that live and reclaimed deliveries
cannot both execute the effect.

The backend core provides `consume_once(session, consumer_group, event_id, effect)`. It records the
group and event UUID in the core-owned `event_consumptions` table before running the effect. The
marker and effect share the caller's database transaction, so rollback makes a failed delivery
eligible to run again, while a committed redelivery skips the effect. This exactly-once boundary
therefore applies to effects made atomically through that session; external side effects require
their own idempotency key. Generated adapters require the session factory and `consume_once` helper:
both their live and recovery readers enter the same guard before calling the controller, commit the
guard and effect together, and only then publish a success event. A duplicate that loses the guard
claim is acknowledged without running the controller or publishing success. An unguarded consumer is
not a generated default and requires a separate, explicit implementation outside this adapter.

## Core jobs v1

Every backend generated from this template provides the versioned core jobs contract:

- `POST /jobs/fire` accepts `JobFire` and returns `JobCommand`.
- `POST /jobs/evidence` accepts `JobCommandRef` and returns `JobCommand`.

All four generated schemas — including the `JobFired` event message — carry
`contract_version: 1`. A caller fires a *named* behaviour: it never names a module, a queue, a
container or a transport. The core starts no timer and runs no loop. It validates the name and the
arguments, records the command, and emits `job_fired`; whichever optional core-module declared that
it provides `jobs.fire` subscribes to that event and does the work. A product with no scheduled
behaviour and no provider therefore gains no container, no worker and no always-on process.

### Declared, never inferred

A behaviour is fireable only because the product declared it, in the same explicit
`services/<service>/manifest.yaml` that declares settings. `jobs_schema` is a Draft 2020-12 object
whose named `properties` are the fireable names, each mapped to the schema its `arguments` object
must satisfy; both the declaration and every behaviour's arguments use `additionalProperties:
false`. The field is additive: a `version: 1` manifest that declares no jobs stays valid, and the
manifest still refuses anything it does not declare. The template ships `properties: {}`, exactly as
it ships no settings. An undeclared name is refused with `404 Job name not declared`, the way an
undeclared setting key is; arguments that fail their declared schema are refused with `422`.
Duplicate job names across service manifests are invalid.

`provides` names the core capabilities a service provides — `jobs.fire` for an optional scheduler
module. It reuses the existing service, profile and manifest mechanism; it is a declaration, not a
catalogue, and the core never resolves a provider from it.

### Identity, provenance and replay

A fire carries a caller-supplied `command_id` and the `fired_by_product` / `fired_by_run`
provenance of whoever fired it. Identity is the tuple `(fired_by_product, command_id)` and storage
uniqueness on that tuple is what bounds execution: a retry or a replay of the same identity returns
the recorded evidence instead of emitting a second time, and a concurrent fire that loses the unique
constraint returns the recorded command rather than creating another. Nothing a caller does with
retries produces an unbounded number of executions.

`dispatch_status` is `dispatched` once `job_fired` has been emitted, together with `dispatched_at`;
that state is terminal, and a later fire of the same identity never emits again. A command whose
event could not be delivered is recorded as `undelivered`, so a retry of that identity re-attempts
delivery without recording a second command. Evidence is what central QA asserts on: it survives the
retry and is readable afterwards through `POST /jobs/evidence`, which returns a command only within
the product that fired it. One product's command is neither visible nor fireable as another's.

### Committed before emitted, emitted once

A `job_fired` exists only for a command whose row is already committed. The core records the
command, commits it, and only then emits: a failure between the two leaves a recorded command that a
retry completes, never a behaviour that ran with nothing recording it.

The emission itself happens in one place, behind the committed row's lock — `SELECT ... FOR UPDATE`
on the product's PostgreSQL. A concurrent retry of the same identity waits there, then reads the row
as the winner left it and returns that evidence instead of emitting beside it, so one identity emits
at most once however many callers fire it and however they interleave. The lock is released by the
commit that records the terminal evidence, which puts the remaining hazard on the safe side: a crash
between a delivered event and that commit leaves the command `undelivered` and a later retry emits a
second time, while the opposite direction is ruled out by the ordering — a command marked
`dispatched` always had its `job_fired` published, because the emission precedes the transition and
is never inverted.

That is a statement about emission, and no more. `job_fired` is appended to its Redis Stream, so
`dispatched` is evidence that the core durably emitted the event, not evidence that a provider has
already consumed it, run the behaviour or completed it. An established provider consumer group can
resume from its backlog after downtime.
Whether the behaviour actually happened is asserted by central QA against the behaviour's own
output, never inferred from dispatch evidence.

### The capability

`POST /jobs/fire` requires exactly one `X-Jobs-Capability` value matching the generated
`JOBS_FIRE_CAPABILITY` secret, compared with `compare_digest`. The header is intentionally absent
from the generated schemas and from OpenAPI. It must never be logged, placed in LLM-facing data, or
carried in a URL, an event payload or an error body. Reading evidence back does not carry it.

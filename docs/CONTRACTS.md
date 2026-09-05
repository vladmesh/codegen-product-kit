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
Git SHA used to deliver `codegen-kit-tooling`. The initial v1 surface is `1.0.0`. Backward-compatible
public additions require a minor bump, breaking changes require a major bump, and fixes that preserve
the promised surface require a patch bump. The package protocol version remains `1` across compatible
next-card additions. A package imports only `Package`, `CORE_VERSION`, and
`PACKAGE_PROTOCOL_VERSION` from `codegen_kit`; product-specific `services.*`, generated contracts,
database objects, and application settings are not public API. The unchanged wheel can therefore be
installed into another generated product with the same compatible core without rebuilding it.

### Package manifest

Each wheel installs exactly one `package.yaml` as distribution data. Protocol v1 validates it
fail-closed and rejects unknown fields. Its fields are:

| Field | Meaning in v1 | Activation enforcement |
|---|---|---|
| `protocol_version` | Integer package protocol version, exactly `1` | Enforced |
| `name`, `version` | Entry-point identity and package release identity | Enforced; missing identity has `MissingPackageIdentityError` |
| `requires_core` | PEP 440 specifier matched against `codegen_kit.CORE_VERSION` | Enforced |
| `provides`, `requires` | Provided and required logical interfaces | Declared only; resolution is next-card work |
| `package_dependencies` | Top-level Python imports allowed in addition to stdlib and `codegen_kit` | Enforced by the kit import lint |
| `http.prefix` | Absolute non-root mount prefix with no trailing slash, `//`, or path parameters | Enforced; malformed values have `MalformedPackagePrefixError` |
| `database.schema`, `database.migrations` | Package-owned PostgreSQL schema and migration resource | Declared only; schema creation and migrations are next-card work |
| `events.publishes`, `events.consumes` | Package event interface names | Declared only; event contract merge is next-card work |
| `settings_schema`, `jobs_schema` | Draft 2020-12 schemas to merge under the package prefix | Declared and structurally validated; merge, prefixing, and duplicate refusal are next-card work |
| `environment` | Named environment requirements and whether each is required | Declared only; generated environment merge is next-card work |
| `resources` | Named distribution resource paths | Declared only; consumers resolve resources with distribution APIs |

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
names must still match exactly.

The referenced object implements `codegen_kit.Package`: it exposes a FastAPI `router` and async
`startup(application)` and `shutdown(application)` methods. Install the wheel as an explicit backend
dependency, keep it in the backend lock, and add its entry-point name to
`services/backend/manifest.yaml`:

```bash
uv add --project services/backend ./dist/weather_package-1.0.0-py3-none-any.whl
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

The factory mounts each activated router under `http.prefix`. Once core connectivity is ready, the
backend lifespan calls package `startup` in manifest order. It calls package `shutdown` in reverse
order before closing core connectivity, including when a later package's startup fails. Core routers
are registered first; if a package route has the same HTTP method and fully resolved path as a core
route, the core route wins, while non-colliding routes under that package prefix remain available.
A package connection check belongs in `startup` and must raise on failure. Package acceptance checks
must install the real wheel, resolve its real entry point, start the generated application, exercise a
prefixed route, observe lifecycle calls, run manifest validation, and run the import lint. The kit's
synthetic package performs these checks.

The generated product's `make lint` runs the installed-package import check. Package source imports
may target stdlib, `codegen_kit`, the package's own modules, and top-level modules named in
`package_dependencies`. Importing product internals or an undeclared third-party package fails the
check. A missing or ambiguous installed `package.yaml`, missing distribution metadata, and an empty
or nonexistent explicit site-packages path also fail the lint rather than producing a vacuous pass.
This is a source boundary, not a dependency resolver; normal Python packaging metadata still owns
installation of dependencies.

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

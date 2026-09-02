# Framework contracts

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

### The capability

`POST /jobs/fire` requires exactly one `X-Jobs-Capability` value matching the generated
`JOBS_FIRE_CAPABILITY` secret, compared with `compare_digest`. The header is intentionally absent
from the generated schemas and from OpenAPI. It must never be logged, placed in LLM-facing data, or
carried in a URL, an event payload or an error body. Reading evidence back does not carry it.

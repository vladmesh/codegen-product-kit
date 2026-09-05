# Framework architecture

This document describes the current codegen-product-kit framework. Generated projects carry their own
module-specific `ARCHITECTURE.md` and `infra/README.md`.

## Repository planes

| Path | Owner | Purpose |
|---|---|---|
| `framework/` | framework developers | Spec models, validators, and generators |
| `template/` | template developers | Copier source for generated projects |
| `tests/unit/`, `tests/tooling/` | framework tests | Typed specs and generator behavior |
| `tests/copier/` | template tests | Rendered module combinations and updates |

The repository root builds the `codegen-kit-tooling` distribution while preserving the
`framework` Python import name. Generated products resolve that distribution from an exact Git
commit and commit the resulting root `uv.lock`.

## Spec-first generation

Backend-capable projects use three spec sources:

| Input | Generated output |
|---|---|
| `shared/spec/models.yaml` | `shared/shared/generated/schemas.py` and TypeScript types |
| `shared/spec/events.yaml` | `shared/shared/generated/events.py` |
| `services/<service>/spec/<domain>.yaml` | protocols, REST routers, router registry, event adapters, and initial controller stubs |

Service manifests are a separate, explicitly loaded input. A
`services/<service>/manifest.yaml` declares its versioned Draft 2020-12 `settings_schema` and, in
the same fail-closed form, its `jobs_schema` of fireable behaviours and the core capabilities it
`provides`; all of it is validated fail-closed and produces the backend's generated settings-schema
and job registries. It is not a domain spec, and the legacy `services/*/spec/manifest.yaml` path
remains ignored.

`framework/spec/loader.py` validates these inputs into typed models. `framework/generate.py` then
runs the generators in a fixed order. Specs should use JSON Schema concepts where practical; there
is no active migration to another implementation language.

Generated ownership is explicit:

- schemas, protocols, events, routers, registries, and event adapters are regenerated and carry a
  generated-file warning;
- controller files are created only when absent and are user-owned afterwards;
- ORM models, repositories, application wiring, migrations, and tests are manual.

The service router registry is consumed by `services/backend/src/app/api/router.py`, which also
wires manual infrastructure endpoints such as `/health`.

## Core settings

Generated backends provide `settings.get` and `settings.set` through typed v1 REST contracts. A
setting value is persisted by declared key and explicit product or user scope. The controller
validates each write against the corresponding service-manifest JSON Schema before the repository
can store it. `settings.set` is protected by one generated write capability; credentials and values
are neither environment-backed product settings nor part of generated OpenAPI.

## Core jobs

Generated backends provide `jobs.fire` and `jobs.evidence` through typed v1 REST contracts. The core
schedules nothing: `jobs.fire` validates a manifest-declared name and its arguments, records the
command under the caller-supplied `(fired_by_product, command_id)` identity, and emits `job_fired`
for whichever optional module declared that it provides `jobs.fire`. Storage uniqueness on that
identity makes a replay return the recorded evidence instead of executing again. `jobs.fire` is
protected by one generated fire capability; `jobs.evidence` is not, and neither the capability nor
any secret appears in generated OpenAPI, event payloads or error bodies.

## Operation transports

A domain operation may declare REST, events, or both:

- REST operations generate FastAPI endpoints that delegate to the controller protocol;
- subscribed event operations generate a FastStream adapter using the same controller protocol;
- success and error publications use channels declared on the operation.

Database transaction ownership remains with the injected session dependency. Controllers do not
commit. A REST or event adapter failure propagates so the dependency can roll back.

Global event declarations define message contracts and publisher functions. Operation-level event
configuration defines transport behavior for a domain operation.

## Runtime modules

Copier selects one or more modules:

| Module | Runtime |
|---|---|
| `backend` | FastAPI, PostgreSQL, Redis |
| `tg_bot` | Python Telegram bot with FastStream event integration |

Unselected module directories are excluded before copy. Projects without backend do not receive
backend specs or generated shared contracts. Adding a previously excluded predefined module to an
existing project is not currently automated.

Arbitrary containers may be added manually by creating their directory, registry entry, Compose
configuration, environment contract, and tests. There is no container-scaffolding command.

## Component direction

The planned component model distinguishes a shared platform **service**, a product-owned
**container**, and an in-process **package**. **Component** is the umbrella term. The author of a
catalog component will declare its delivery form so a product-building agent does not choose a
runtime boundary heuristically.

That model is not implemented yet. There is no package registration protocol, component catalog,
or deterministic installer in the current kit; `backend` and `tg_bot` remain Copier selections.

## Tooling and runtime

Framework development uses the root `.venv/`. Generated projects use a root tooling venv plus a
separate venv per Python service. `make setup` creates them with uv, generates backend artifacts,
formats the initial output, and configures Git hooks.

Docker is used for service runtime, integration tests, and deployment—not for framework lint or
unit tests. Generated Compose is layered:

- `compose.base.yml`: service definitions and internal dependencies;
- `compose.dev.yml`: workspace mounts and development commands;
- `compose.local.yml`: host port publication;
- `compose.prod.yml`: production images and deployment settings;
- `compose.tests.integration.yml`: isolated backend integration stack.

`template/infra/README.md` is the canonical detailed infrastructure contract. It documents Compose
service names, network ownership, port allocation, environment precedence, and worker mode.

## Environment contract

Generated environment variables are declared by service-owned `env.contract.yaml` files and
rendered into `.env.example`. Required application settings fail at runtime when absent. Defaults
are allowed in Compose interpolation and test fixtures where they express local infrastructure
behavior; those defaults do not weaken required production application settings.

## Change validation

- Framework source change: run lint, unit/tooling tests, and the generated-product checks.
- Template or Copier change: add the non-slow Copier matrix.
- Compose, setup, Docker, or module-combination change: add slow Copier/generated-project checks.

See `docs/TESTING.md` for the command matrix and `docs/DEVELOPMENT.md` for the contributor workflow.

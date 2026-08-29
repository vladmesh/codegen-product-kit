# Framework architecture

This document describes the current service-template framework. Generated projects carry their own
module-specific `ARCHITECTURE.md` and `infra/README.md`.

## Repository planes

| Path | Owner | Purpose |
|---|---|---|
| `framework/` | framework developers | Spec models, validators, and generators |
| `template/` | template developers | Copier source for generated projects |
| `template/.framework/framework/` | sync script | Embedded mirror of `framework/` |
| `tests/unit/`, `tests/tooling/` | framework tests | Typed specs and generator behavior |
| `tests/copier/` | template tests | Rendered module combinations and updates |

`scripts/sync-framework-to-template.sh` is the only supported mirror writer. The mirror must be
byte-for-byte synchronized before merge.

## Spec-first generation

Backend-capable projects use three spec sources:

| Input | Generated output |
|---|---|
| `shared/spec/models.yaml` | `shared/shared/generated/schemas.py` and TypeScript types |
| `shared/spec/events.yaml` | `shared/shared/generated/events.py` |
| `services/<service>/spec/<domain>.yaml` | protocols, REST routers, router registry, event adapters, and initial controller stubs |

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
| `notifications` | FastStream notification worker |
| `frontend` | Astro + React placeholder on port 4321 |

Unselected module directories are excluded before copy. Projects without backend do not receive
backend specs or generated shared contracts. Adding a previously excluded predefined module to an
existing project is not currently automated.

Arbitrary services may be added manually by creating their directory, registry entry, Compose
configuration, environment contract, and tests. There is no service-scaffolding command.

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

- Framework source change: sync the mirror, then run lint, unit/tooling tests, and sync check.
- Template or Copier change: add the non-slow Copier matrix.
- Compose, setup, Docker, or module-combination change: add slow Copier/generated-project checks.

See `docs/TESTING.md` for the command matrix and `docs/DEVELOPMENT.md` for the contributor workflow.

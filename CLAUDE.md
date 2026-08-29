# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A **spec-first Copier template framework** for generating microservice projects. This repo is both:
- **Framework source** (`framework/`) — generators, validators, spec parsers
- **Copier template** (`template/`) — what gets copied to new projects via `copier copy`

The `template/.framework/` directory is a mirror of `framework/` — kept in sync via `make sync-framework` and enforced in CI via `make check-sync`.

## Common Commands

### Framework Development

```bash
make setup              # Create .venv, install deps via uv
make test               # Unit + tooling tests (tests/unit + tests/tooling) with coverage
make test-copier        # Copier template tests (requires .venv/bin/copier)
make test-copier-slow   # Slow copier tests
make test-all           # All tests combined
make lint               # ruff check on framework/ and tests/
make lint-template      # ruff check on template/
make format             # ruff format + ruff check --fix
make sync-framework     # rsync framework/ → template/.framework/framework/
make check-sync         # Verify framework and template/.framework are identical
```

### Generated Project Commands (template/Makefile.jinja)

```bash
make setup                          # Bootstrap: uv venv, install deps, codegen, git hooks
make lint                           # ruff + xenon + spec validation + compliance checks
make format                         # ruff format + fix
make tests                          # All service unit tests
make tests <service>                # Single service tests
make generate-from-spec             # Regenerate code from YAML specs (backend only)
make validate-specs                 # Validate YAML specs
make dev-start / make dev-stop      # Docker Compose dev environment
```

## Architecture

### Spec-First Code Generation

YAML specs are the single source of truth. The pipeline:

1. `framework/spec/loader.py` — loads and validates all YAML specs
2. `framework/generate.py` — orchestrates generators
3. `framework/generators/` — each generator reads specs, uses Jinja2 templates to emit code

| Generator | Input Spec | Output |
|-----------|-----------|--------|
| `schemas` | `shared/spec/models.yaml` | Pydantic models (via datamodel-code-generator) |
| `protocols` | `services/*/spec/*.yaml` | Protocol classes (interfaces) |
| `controllers` | `services/*/spec/*.yaml` | Controller stubs (skip if existing) |
| `events` | `shared/spec/events.yaml` | FastStream publisher functions |
| `event_adapter` | `services/*/spec/*.yaml` | Event handler adapters |
| `routers` | `services/*/spec/*.yaml` | FastAPI routers and registry |

Jinja2 code templates live in `framework/templates/codegen/`.

### Type System

`framework/spec/types.py` — TypeSpec is a Pydantic discriminated union: PrimitiveType, ListType, DictType, OptionalType, EnumType. Handles Python type annotations and JSON Schema conversion.

### Read-Only Generated Zones

Never edit these directories manually — they are overwritten by codegen:
- `shared/shared/generated/` — schemas, events
- `services/*/src/generated/` — protocols, event adapters
- `template/.framework/` — framework mirror

### Service Types

| Type | Framework | Use |
|------|-----------|-----|
| `python` | Python | Generic Python service |
| `python-fastapi` | FastAPI + uvicorn | HTTP API |
| `python-faststream` | FastStream + Redis | Event-driven workers |
| `node` | Node.js | Frontend |
| `default` | Generic | Container placeholder |

### Copier Template System

`copier.yml` defines template variables. Key variable: `modules` (comma-separated: `backend`, `tg_bot`, `notifications`, `frontend`). Common configurations:
- Standalone: `modules=tg_bot`
- Backend only: `modules=backend`
- Fullstack: `modules=backend,tg_bot`
- Full: `modules=backend,tg_bot,notifications,frontend`

Post-copy tasks remove unselected module directories.

## Critical Rules

- **Required application env**: Never give required runtime settings a fallback. Compose and test
  fixtures may define explicit local defaults.
- **Lazy broker**: Use `get_broker()`, never module-level `broker = RedisBroker(...)`.
- **Per-service venvs**: Each service has its own `.venv/`; root `.venv/` is for dev tools only.
- **After changing framework/**: Always run `make sync-framework` before committing.
- **Ruff config**: Line length 100, target py311, double quotes. See `ruff.toml`.

## Test Structure

```
tests/
├── unit/       # Spec parsing, validation, type system
├── tooling/    # Generators and linters
├── copier/     # Template generation and generated-project checks
└── framework/  # Extended generator integration tests
```

Copier tests require `.venv/bin/copier`. The `copier_available` fixture verifies this.

# Framework development

This guide is for contributors to service-template itself. Generated products have their own
README, AGENTS, architecture, and infrastructure documents.

## Setup and verification

```bash
make setup
make lint
make test
make test-copier
make check-sync
```

See `docs/TESTING.md` for when the slow Copier suite is required.

## Repository layout

| Path | Purpose |
|---|---|
| `framework/spec/` | Typed YAML models and validation |
| `framework/generators/` | Python/codegen artifact generators |
| `framework/templates/codegen/` | Jinja templates consumed by generators |
| `template/` | Copier source tree |
| `tests/unit/`, `tests/tooling/` | Framework behavior |
| `tests/copier/` | Generated-project behavior |

## Framework mirror

Generated projects embed the framework under `template/.framework/framework/`. After changing
`framework/`, run:

```bash
make sync-framework
make check-sync
```

Do not edit only the embedded copy. `make sync-framework-preview` shows the pending mirror change.

## Current generators

| Generator | Input | Output ownership |
|---|---|---|
| Schemas | `shared/spec/models.yaml` | Regenerated Pydantic schemas |
| Protocols | domain specs | Regenerated controller protocols |
| Controllers | domain specs | Write-once editable stubs |
| Routers | REST operations | Regenerated FastAPI routers and registry |
| Events | `shared/spec/events.yaml` | Regenerated publisher helpers |
| Event adapters | subscribed operations | Regenerated FastStream adapters |
| Settings manifest registry | `services/<service>/manifest.yaml` | Regenerated backend settings-schema registry |
| Jobs manifest registry | `services/<service>/manifest.yaml` | Regenerated backend fireable-job registry |

`datamodel-code-generator` is a required framework dependency. Schema generation is always the
first pipeline stage, so a missing dependency aborts generation before any artifact is written.

OpenAPI and TypeScript exporters are separate framework entry points. The removed manifest client
generator and service scaffold are not supported extension points.

## Adding or changing a generator

1. Extend the typed spec model only if the input contract changes.
2. Implement the generator under `framework/generators/` or the relevant exporter package.
3. Reuse the shared operation context and type renderers.
4. Put emitted boilerplate in `framework/templates/codegen/`.
5. Add focused unit/tooling tests and output-level Copier coverage.
6. Sync the embedded framework mirror.

Generated files should be atomically written, deterministic, and carry the standard warning unless
they are intentionally write-once user files.

## Changing the Copier template

Treat `template/` as source. Update Jinja conditions, module exclusions, environment contracts, and
tests together. Do not hand-edit a generated fixture and treat it as the fix.

For a manual render:

```bash
uvx copier copy . /tmp/service-template-smoke \
  --data project_name=smoke \
  --data modules=backend,tg_bot \
  --defaults --vcs-ref=HEAD --overwrite
```

Then run the generated project's `make setup`, `make lint`, `make typecheck`, and `make tests`.

## Adding a predefined module

Add the service under `template/services/`, then update `copier.yml`, `services.yml.jinja`, Compose
layers, environment contracts, generated documentation, and the Copier matrix. Module names and
runtime service names may differ (`notifications` selects `notifications_worker`), so test both.

There is no supported command to add a previously excluded module to an existing generated project.

## Release

Update the Copier version and `CHANGELOG.md`, run the full validation matrix, then create and push the
release tag. Copier uses the latest tag for remote sources unless the caller selects `HEAD`.

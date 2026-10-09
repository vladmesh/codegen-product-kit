# Framework development

This guide is for contributors to codegen-product-kit itself. Generated products have their own
README, AGENTS, architecture, and infrastructure documents.

## Setup and verification

```bash
make setup
make lint
make test
make test-copier
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

## Tooling distribution

`framework/` is the sole source of the installable `codegen-kit-tooling` distribution. Generated
products import it as `framework` through the exact Git requirement and root lock produced by
Copier. Update packaging metadata and generated-product coverage with any dependency change.

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
| Telegram bindings | product `services/tg_bot/bindings/*.yaml`, installed manifests/libraries | Regenerated bot bindings and event relay; manifests/bindings remain product-owned |

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
6. Verify a generated product against the installed tooling.

Generated files should be atomically written, deterministic, and carry the standard warning unless
they are intentionally write-once user files.

## Changing the Copier template

Treat `template/` as source. Update Jinja conditions, module exclusions, environment contracts, and
tests together. Do not hand-edit a generated fixture and treat it as the fix.

For a manual render:

```bash
uvx copier copy . /tmp/codegen-product-kit-smoke \
  --data project_name=smoke \
  --data modules=backend,tg_bot \
  --defaults --trust --vcs-ref=HEAD --overwrite
```

Then run the generated project's `make setup`, `make lint`, `make typecheck`, and `make tests`.

Generated Python environments are prepared only by `template/scripts/prepare-env.sh`. A new setup
step, CI job or Dockerfile stage calls it with exactly the environments it needs (`--runtime` for
runtime images) instead of its own `uv sync`; `tests/copier/test_prepare_env.py` enforces this for
every product shape, and the CI [runner proof](RUNNER_PROOF.md) exercises it end to end.

## Adding a predefined module

Add the service under `template/services/`, then update `copier.yml`, `services.yml.jinja`, Compose
layers, environment contracts, generated documentation, and the Copier matrix.

There is no supported command to add a previously excluded module to an existing generated project.

## Release

The kit release version is its Git tag, such as `0.6.2`; prepare `CHANGELOG.md` and release notes,
run the full validation matrix, then create and push the tag from reviewed merged main in the
release operation. `_min_copier_version` is the minimum Copier tool version, not the kit version.
The tooling and generated application package versions are separate and need not change for a
template patch. Copier uses the latest tag for remote sources unless the caller selects `HEAD`.

See [0.6.4 release and upgrade notes](releases/0.6.4.md) for the prepared transport patch,
including real Copier update commands and workflow conflict review.
See [0.7.0 release and upgrade notes](releases/0.7.0.md) for the core timer loop and for the rule
that a kit core tag and a package tag never share a commit.
See [0.7.1 release and upgrade notes](releases/0.7.1.md) for the generated lifespan unit-test fix.
See [0.8.1 release and upgrade notes](releases/0.8.1.md) for formatter-stable generated bindings
under the product's own drift check and lint.
See [0.10.0 release and upgrade notes](releases/0.10.0.md) for finite RU/EN binding v2,
product language and façade 2.4.0 with pinned v1 compatibility.
See [0.10.1 release and upgrade notes](releases/0.10.1.md) for bound-product integration
generation using container-native product environments and published-package coverage.
See [0.9.0 release and upgrade notes](releases/0.9.0.md) for generic platform environment sources,
façade 2.3.0 and the included untagged 0.8.2 xenon fix.
See [reminders 0.5.0 preparation](releases/reminders-0.5.0.md) for core 2.2 action admission,
the finite default binding and independent package publication prerequisites.

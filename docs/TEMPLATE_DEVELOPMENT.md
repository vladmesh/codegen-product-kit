# Copier template development

`template/` is the source tree rendered by Copier. `copier.yml` owns questions, module exclusions,
update preservation, the exact tooling requirement, and the trusted task that writes `uv.lock`.

## Modules and names

The supported selection values are `backend` and `tg_bot`. At least one module is required.

Unselected modules are removed through conditional `_exclude` entries before copy. Every new module
must update the service registry, Compose layers, environment contract, documentation conditionals,
and Copier tests.

## Ownership on update

`_skip_if_exists` is a narrow guarantee: Copier never replaces the listed paths. It currently
protects local env files, shared model/event specs, backend application code, and controllers.

Framework-generated paths such as `shared/shared/generated/` and
`services/*/src/generated/` are owned by `make generate-from-spec`, not by manual edits.

Other template files participate in Copier's update merge. They may be edited when the product
requires it, but users must review the update diff and resolve conflicts. Do not describe an entire
service directory as read-only or user-owned.

## Jinja conditionals

Use the normalized module values from `modules`. Keep conditionals at column zero when possible.
Whitespace trimming can remove Python indentation, YAML indentation, or Make recipe tabs, so verify
rendered output instead of applying `{%-` and `-%}` mechanically.

Generated Markdown must render cleanly for backend-only, bot-only, and combined configurations.
Commands that exist only with backend must be
inside the same backend condition as the corresponding Make target.

## Validation

```bash
make lint
make test
make test-copier
```

Run `make test-copier-slow` for changes to setup, Docker, Compose, module combinations, or actual
generated-project execution. A focused render can use:

```bash
uvx copier copy . /tmp/codegen-product-kit-smoke \
  --data project_name=smoke \
  --data modules=backend,tg_bot \
  --defaults --trust --vcs-ref=HEAD --overwrite
```

Inspect the result for unresolved Jinja markers, missing paths, invalid YAML, and commands absent
from its Makefile.

## Adding a variable or module

For a variable, define its type, validation, default, and visibility in `copier.yml`; cover each
conditional output it controls.

For a module:

1. add its source under `template/services/`;
2. add conditional exclusions in `copier.yml`;
3. render its registry, Compose, env, and documentation entries;
4. add isolated and combined Copier fixtures;
5. verify update behavior for user-owned paths.

Template releases must pass both Copier suites and the supported generated-project matrix.

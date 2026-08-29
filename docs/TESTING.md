# Framework testing

Run framework checks from the repository root after `make setup`.

## Test targets

| Command | Scope |
|---|---|
| `make lint` | Ruff checks for `framework/` and framework tests |
| `make test` | Unit and tooling tests with coverage |
| `make test-copier` | Non-slow generated-project matrix |
| `make test-copier-slow` | Docker and generated-project end-to-end cases |
| `make test-all` | `make test` plus the non-slow Copier suite |
| `make check-sync` | Verifies the embedded framework mirror |

Use the project venv for a focused pytest run while editing:

```bash
.venv/bin/pytest tests/tooling/test_openapi.py -q
.venv/bin/pytest tests/copier/test_template_generation.py -k backend -v
```

## What to run

- Changes under `framework/`: focused tests, `make lint`, `make test`, and `make check-sync`.
- Changes under `template/` or `copier.yml`: also run `make test-copier`.
- Changes to module selection, Dockerfiles, Compose, setup, or generated-project commands: also run
  `make test-copier-slow` when Docker is available.
- Before release: run both Copier suites and generate the supported module combinations.

`make sync-framework` copies `framework/` into `template/.framework/framework/`. Run it after a
framework change, then use `make check-sync` to prove the copies match.

## Generated projects

The root Makefile tests the framework repository. A generated project has a different command
surface:

```bash
make setup
make lint
make typecheck
make tests
```

Backend projects additionally expose `make generate-from-spec` and `make test-integration`.

## CI

`.github/workflows/ci.yml` runs the framework suite.
`.github/workflows/test-template.yml` exercises Copier generation and generated-project behavior.
Keep required job names stable unless branch protection is updated at the same time.

When a Docker-dependent test cannot run, skip it explicitly at the pytest boundary with a reason;
do not silently return from the test.

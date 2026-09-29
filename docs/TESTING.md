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

Use the project venv for a focused pytest run while editing:

```bash
.venv/bin/pytest tests/tooling/test_openapi.py -q
.venv/bin/pytest tests/copier/test_template_generation.py -k backend -v
```

## What to run

- Changes under `framework/`: focused tests, `make lint`, `make test`, and generated-product tests.
- Changes under `template/` or `copier.yml`: also run `make test-copier`.
- Changes to module selection, Dockerfiles, Compose, setup, or generated-project commands: also run
  `make test-copier-slow` when Docker is available.
- Before release: run both Copier suites and generate the supported module combinations.

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

`tests/copier/test_deploy_transport.py` belongs to the existing `test-pytest` fast Copier leg.
That checkout fetches history and tags so the upgrade fixture can generate exact annotated 0.6.3
(`f23460c62fa3508858c0552557b2860af09f2656`). Dirty candidates use the committed temporary snapshot
from `tests/copier/conftest.py`; no released fixture is patched. The tests execute the rendered
copy shell with controlled SSH/SCP/sleep captures, then feed its arguments to installed OpenSSH:
`ssh -G -F /dev/null` reads configuration without connecting, and `scp -S` invokes a capture
transport that always fails without connecting. These tests require OpenSSH, rather than skipping
native parsing proof. They assert the actual host and username, including the released IPv6
misparse to `2001`. The appleboy action input is checked, but the external action is not executed.

Real Copier update cases run `uv lock`, commit synthetic owned environment/application data,
read back retained bytes and the updated workflow, preserve a nonconflicting workflow name, and
report an overlapping runner change as a `.rej`. Only isolated fixture Git downloads are mapped
to the local committed source. See [the upgrade review boundary](releases/0.6.4.md).
Existing slow generation, service typechecks, logging and PostgreSQL migration proofs keep their
CI routes; a local fast broad receipt does not replace the required release matrix.

When a Docker-dependent test cannot run, skip it explicitly at the pytest boundary with a reason;
do not silently return from the test.

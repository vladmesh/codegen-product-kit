# Codegen Product Kit

`codegen-product-kit` is the product foundation used by the code-generation pipeline. It generates
a Python product with deterministic contracts, runtime infrastructure, and agent instructions.

The repository was derived from `vladmesh/service-template` at commit
`40b54d87dbfe64a9fa6ec379820e43137aaba04c`. It is now an independent project; changes are not
automatically synchronized in either direction.

## Current scope

The kit currently generates two built-in product shapes:

| Selection | Result |
|---|---|
| `backend` | FastAPI, PostgreSQL, Redis, users/access, settings, jobs, env contracts, OpenAPI |
| `tg_bot` | Telegram adapter with Redis integration; it can also be generated without a backend |

The future component vocabulary is:

- **service** — an already-running platform capability shared by multiple products;
- **container** — an image deployed inside one product's Compose application;
- **package** — code imported and executed inside a product process;
- **component** — the common term for all three.

This repository does not yet implement a package runtime, a component catalog, or automatic
composition across those component types. The current `modules` Copier option selects only the two
built-in application shapes above.

## Generate a project

```bash
uvx copier copy gh:vladmesh/codegen-product-kit my-product \
  --data project_name=my-product \
  --data modules=backend,tg_bot \
  --defaults \
  --vcs-ref=HEAD
```

Then run `make setup`, copy `.env.example` to `.env`, and use `make dev-start`.

## Develop the kit

```bash
make setup
make lint
make test
make test-copier
make check-sync
```

See [architecture](docs/ARCHITECTURE.md), [development](docs/DEVELOPMENT.md), and
[testing](docs/TESTING.md) for the current contracts.

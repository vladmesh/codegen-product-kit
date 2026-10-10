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

This repository implements an in-process package runtime and a package catalog
(`packages/catalog.yaml`) from which `kit add <name>` installs released packages, but not a catalog
of services or containers or automatic composition across component types. The current `modules` Copier option selects only the two
built-in application shapes above. The catalog also declares plain libraries: `kit add textparse`
targets `services/tg_bot`, with no backend activation. Its three-form English 0.1.0 source is
prepared; independent tag publication follows merge. See the
[library contract](docs/CONTRACTS.md#library-api-and-installation).

Core façade 2.5.0 owns the product `language` setting (`ru`/`en`) and the bot's single command
registry: core built-ins, product commands declared as `ProductCommand` entries in
`services/tg_bot/src/commands.py`, bound module commands, and a core reply to unknown input.
`kit check-install <name> --json` previews an install read-only as `mechanical`, `glue` or
`incompatible`. See the [core host contract](docs/CONTRACTS.md#core-host-contract-v1).

## Generate a project

```bash
uvx copier copy gh:vladmesh/codegen-product-kit my-product \
  --data project_name=my-product \
  --data modules=backend,tg_bot \
  --defaults \
  --trust \
  --vcs-ref=HEAD
```

Then run `make setup`, copy `.env.example` to `.env`, and use `make dev-start`.

## Develop the kit

```bash
make setup
make lint
make test
make test-copier
```

See [architecture](docs/ARCHITECTURE.md), [development](docs/DEVELOPMENT.md),
[testing](docs/TESTING.md), and the attested [package RSS observations](docs/PACKAGE_RSS.md) for the
current contracts and evidence.

See [0.6.4 release and upgrade notes](docs/releases/0.6.4.md) for the prepared native IPv6
deployment fix and the explicit Copier update required for existing products.

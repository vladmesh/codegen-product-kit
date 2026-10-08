# tg-channels 0.1.1 preparation

Prepared 2026-10-08. A package-only patch release; no kit core, tooling, template or
`CORE_VERSION` change. `requires_core` stays `>=2.4,<3`.

## Defect

Sprint 1485 production acceptance installed tg-channels 0.1.0 into a Codegen product
(`install-a4284b77a68e456d9e0b8058d4d67458`). The product's own CI step "Run integration tests"
failed: the backend container of `infra/compose.tests.integration.yml` exited at startup with
`codegen_kit_tg_channels/client.py:44 from_environment -> RuntimeError: PLATFORM_BASE_URL is not
set`. Package `startup()` built the platform client unconditionally. In production the deploy
resolver supplies `PLATFORM_BASE_URL` and `PLATFORM_KEY`; a product's integration-test
environment never has them.

The kit's published-package lane missed it because the tg-channels case wrote an inert
`PLATFORM_KEY` and a `.invalid` `PLATFORM_BASE_URL` into the generated product's `.env`
(see [0.10.1](0.10.1.md), "Published-package regression"), a condition a real product CI
does not have.

## Change

- When either platform variable is unset or empty, `startup()` succeeds and logs one warning,
  `tg-channels platform access is not configured: <names> not set`, naming the missing
  variables and never values. The package runs without a platform client.
- `add` (after local username validation) and `digest` of a non-empty list answer the existing
  declared `not_configured` error, which binding v2 already maps to «Сервис не настроен.» /
  «Service is not configured.». `list`, `remove` and a digest of an empty list are local and
  keep working. No new error code, binding text or setting.
- Timer ticks still publish committed deliveries, but polling makes no platform request and
  leaves the poll state, including the durable 401/403 stop flag, unchanged.
- With both values set, behaviour is unchanged: 409 restart, 429 backoff, 401/403 durable
  stop and `not_configured`, pending, and no key in logs or exceptions.
- No runtime default URL or key is added. `ReaderClient.from_environment()` still raises for
  callers that require the values. Configuring the platform needs a restart with both values.

Unit tests (`packages/codegen-kit-tg-channels/tests/test_unconfigured.py`) cover both missing,
each one missing, and both set, including log capture showing no key or URL values.

## Published-package lane

`tests/copier/test_bound_product_integration.py` no longer injects platform values; the
tg-channels case asserts the product `.env` has no `PLATFORM_*` entry and runs the generated
product's own `make test-integration` with unchanged Makefile, Compose and test bytes. Because
the catalog's newest tg-channels entry is this unpublished release, the case exports the exact
candidate commit's catalog and package tree into a scratch catalog source, verifies the newest
entry matches the package manifest, tags it only there and installs with `--catalog-source`.
It therefore proves the candidate 0.1.1 package source, not the later published tag. The
reminders/textparse case still installs published tags and is unchanged, as are the
contract-hash checks. The retained JSON records the package source and whether the backend
output contained the not-configured warning.

Before publication, Framework CI and Test Copier Template must pass, including the slow lane.
Worker-local checks do not replace executed CI or an exact-SHA gate.

## Publication (PO)

From reviewed, merged main, in a separate operation:

1. Confirm the merge commit contains `package.yaml` and `pyproject.toml` version 0.1.1 and the
   catalog's newest tg-channels entry `0.1.1` with tag `packages/tg-channels/v0.1.1`; the
   0.1.0 entry is kept. Run the existing HEAD-consistency test.
2. Check the package tree at that commit: `git ls-tree -r HEAD -- packages/codegen-kit-tg-channels`.
3. Create the annotated tag `packages/tg-channels/v0.1.1` at that exact merge commit and push
   only that tag. Do not move or recreate `packages/tg-channels/v0.1.0`; no kit core tag.
4. Confirm `kit add tg-channels` in a core 2.4 product resolves 0.1.1 from the live catalog.

Until the tag exists, `kit add tg-channels` from the default catalog refuses the newest entry
as not published yet; publish promptly after the merge. Existing products installed with
0.1.0 upgrade with `kit add tg-channels`, then rerun their checks and `make test-integration`.

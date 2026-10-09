# Fresh installed-product runner proof

`.github/workflows/runner-proof.yml` (job `Fresh product runner proof`) proves, on a free GitHub
runner and without an LLM, BitLaunch or live Telegram, that a new order's product works end to
end: a fresh Copier backend,tg_bot product from the exact kit candidate is installed with the
published tg-channels by the orchestrator's real executor, passes its own CI, builds and publishes
its runtime images the way its main job does, and delivers a channel post to a chat through the
pinned platform's real auth and Caddy. The runner is `tests/runner/fresh_product.py`.

It runs on every pull request to `main` and every push to `main`, without path filters. It is not
green when it did not run: a fork pull request, a missing `PLATFORM_SERVICES_DEPLOY_KEY` or a
non-SHA pin fails the first step, and the last step fails unless the uploaded evidence says
`passed` for exactly the pinned revisions.

## Pinned inputs

| Input | Default | Source |
|---|---|---|
| kit candidate | PR head SHA, or the pushed SHA | `vladmesh/codegen-product-kit`, full history |
| orchestrator | `ec96fa79c84937982420f0f01388d90000bd69eb` | `vladmesh/codegen_orchestrator` (public) |
| platform | `070dfb359f9a121546d1362ae84c9d0856cabf64` | `vladmesh/codegen-platform-services` (private) |

The platform is checked out with the read-only deploy key `PLATFORM_SERVICES_DEPLOY_KEY` over SSH,
`persist-credentials: false`, into `platform/` outside the uploaded artifact path. The workflow
refuses any system/global `insteadOf` rewrite before the checkout, then verifies all three fetched
heads, that the platform origin is the SSH URL and that no credential stayed in its Git config.
The key is used only for a `push` to `main` and a same-repository pull request, also when another
repository calls the workflow (its event decides); any other event fails the first step. There is
no `pull_request_target` and no manual dispatch.

### Running it from another repository

A card in another repository runs the same proof against its own candidate by calling the
reusable workflow with exact SHAs; nothing in the product or the kit is edited:

```yaml
jobs:
  kit-runner-proof:
    uses: vladmesh/codegen-product-kit/.github/workflows/runner-proof.yml@<kit sha>
    with:
      kit_sha: <kit sha>                 # the kit candidate, reachable on GitHub
      orchestrator_sha: <orchestrator sha>  # empty: the kit's pin above
      platform_sha: <platform sha>          # empty: the kit's pin above
    secrets:
      PLATFORM_SERVICES_DEPLOY_KEY: ${{ secrets.PLATFORM_SERVICES_DEPLOY_KEY }}
```

The caller provides its own copy of the read-only deploy key secret. Locally the runner is never
executed (it needs Docker and network); in CI it is:

```bash
orchestrator/.venv/bin/python kit/tests/runner/fresh_product.py --kit-dir kit --kit-sha SHA \
    --orchestrator-dir orchestrator --orchestrator-sha SHA \
    --platform-dir platform --platform-sha SHA --output-dir "$RUNNER_TEMP/runner-proof"
```

The orchestrator environment is its own `uv sync --frozen` with `codegen-kit-tooling` replaced by
the kit candidate (`git+https://github.com/vladmesh/codegen-product-kit.git@<kit sha>`), so the
planner's typed install payload names the candidate tooling, as a production orchestrator would
after moving its kit pin. The runner refuses a planner whose tooling is not the candidate.

## Stages

1. **Revisions.** The three checkouts are at their pins; the planner tooling is the candidate.
2. **Fresh product.** `copier copy --trust --defaults --vcs-ref=<kit sha>
   gh:vladmesh/codegen-product-kit` with `modules=backend,tg_bot` and no other data, so the
   tooling requirement is the template default at the candidate. The answers' `_commit` must
   resolve to the candidate. `git init`, `make setup`, a baseline commit pushed to a local bare
   `origin`.
3. **Payload.** The orchestrator's own `KitCatalogReader` and `plan_install_payload` select
   `tg-channels` from the published catalog at `HEAD`; the payload's `tooling_commit` must be the
   candidate.
4. **Install.** `scaffolder.src.install.run_install` runs unchanged: preflight probe, `kit add`,
   `kit bind --default`, generation, validation, per-service mypy, unit tests, readback probe,
   protected-file check, commit and push of `story/runner-proof`. The published remote head must
   equal the executor result.
5. **Product CI.** A cold clone of the story branch executes the `run` steps of the product's own
   `lint-and-test` job in order (setup, environment contract, generation drift, lint, typecheck,
   tests, dev compose smoke, integration tests, `always()` clean-up). `uses` steps are provided by
   the runner job with the same pinned Python and uv, which the runner checks.
6. **Cold main regression.** Another cold clone replays the 0.10.1 main recipe (`uv sync --frozen`,
   `uv sync --project services/backend --frozen`, `make generate-from-spec`) and must fail with
   `BindingEnvironmentError: tg_bot environment is not installed`, with root and backend prepared,
   tg_bot absent and the binding installed.
7. **Main images.** A third cold clone executes the product's `build-and-push` job `run` steps
   (`.env` placeholder, `sh scripts/prepare-env.sh root backend tg_bot`, generation; the registry
   login is the only skipped step), must leave no tracked change, then builds every matrix entry
   with the same Dockerfile and context, pushes it to an isolated `registry:2.8.3` on the runner
   loopback, records the digest and deletes the local image.
8. **Integration without host environments.** A fourth cold clone with no `.venv` anywhere runs
   `make test-integration`: generation in the backend dev/integration container uses only the
   image's product-owned environments.
9. **Platform.** The auth image is built from the pinned platform source with its own Dockerfile;
   `deploy/ci/fake-secrets.sh` writes synthetic secrets outside the artifact path. The platform's
   own `deploy/compose.yml` runs postgres, postgres-init, auth-migrate, auth and Caddy with its
   Caddyfile, under a runner-named project and a runner-created `orch-link` network.
   `tests/runner/compose.platform.yml` replaces only `tg-reader` and adds `runner-admin`, which
   registers the throwaway product, its tg-reader grant (the scopes and quota the installed package
   declares in its env contract) and a synthetic key through the real admin API, reads it back,
   and gets 401 problem documents through Caddy for a missing, unknown and malformed key. The
   reader must have received no request.
10. **Deployment.** The product's `infra/compose.base.yml` and `compose.prod.yml` are laid out as
    its deploy workflow does, with a `.env` from its `.env.example` plus synthetic values and
    `PLATFORM_BASE_URL=http://caddy:8080/tg-reader`. `tests/runner/compose.product.yml` only joins
    the backend to the platform edge network and points the bot at the fake Bot API. Images are
    pulled by digest from the registry; running containers must use exactly those references.
11. **Scenario.** The product language (`en`) and the user's access are set through the product's
    own `/settings/set` and `/users/grant`. The negative control comes first: with an unknown key,
    `/channel @runner_fixture` is answered `Service is not configured.`, Caddy logs the 401s, the
    reader receives nothing and no post is sent. The backend is then recreated with the registered
    key: the same command answers `Channel added: @runner_fixture`, the package's timer, poller
    and event relay deliver the fixture post to the user's chat, and the reader must have seen
    only requests with auth's identity for the throwaway product.

## Fixtures and controlled edges

| Edge | Replacement |
|---|---|
| GitHub repository transport | a local bare `origin`; only the executor's `git remote get-url origin` is answered with `https://github.com/ci/runner-product`. Branch, commit, push and `ls-remote` readback are real. |
| Install fence | a callback recording every executor checkpoint |
| tg-reader | `tests/runner/fixtures/reader.py`: the published reader contract for one fixture channel. It answers only requests carrying auth's identity for the expected product (403 otherwise) and creates its single post on the first posts read after the channel was resolved, so a delivered post is newer than the subscription. |
| Telegram Bot API | `tests/runner/fixtures/telegram_api.py` under the network alias `api.telegram.org`, TLS from a runner CA that the bot trusts through `SSL_CERT_FILE`. A loopback control port queues a user message as an update and reads sent messages; it cannot add one. Delivery is accepted only after the watermark taken before the command. |
| Registry | `registry:2.8.3` on `127.0.0.1`; no release or production image is pushed |

Everything else is the real thing: Copier, the candidate template and tooling, the published
catalog and tg-channels release, the orchestrator executor and probes, the product's Makefile,
workflows, Dockerfiles and Compose files, the platform's auth, Postgres, Caddyfile and compose.

The runner creates only resources named with its random prefix (`rp<hex>-registry`,
`rp<hex>-orch-link`, Compose projects `rp<hex>-platform` and `rp<hex>-product`) and removes them
in a `finally` block, also after a failure; the product's own CI steps clean up their own Compose
projects. All secrets are synthetic and generated per run.

## Evidence contract

The artifact `runner-proof-<kit sha>-<attempt>` holds `runner-proof.json`, the product's
environment contract artifact and one log per command. Every known synthetic secret is replaced by
`<redacted>` in all of them, and the Caddy log must contain no product key. `runner-proof.json`:

| Key | Content |
|---|---|
| `status`, `error` | `passed` or `failed` with the redacted failure |
| `pinned`, `revisions` | kit, orchestrator and platform SHAs; planner and installed product tooling provenance |
| `template` | Copier source, `_commit`, its resolved SHA, modules, tooling requirement |
| `install_payload`, `install` | typed payload; executor stages with argv and exit codes, preflight and readback probe output, protected hashes, fence checkpoints, credential boundary |
| `module` | tg-channels version, tag, tag object/tree/target and installed distribution |
| `product_ci` | each `lint-and-test` step and its exit code |
| `cold_main_regression` | the old recipe, prepared environments, exit code and the reproduced error |
| `main_images` | each `build-and-push` step; per image Dockerfile, context, tag, digest and reference |
| `integration_without_host_environments` | the cold `make test-integration` summary |
| `platform` | auth image, throwaway product id, grant, admin read-back and ingress refusals |
| `deployment` | the digest references the running backend and bot use |
| `scenario` | initialization responses, negative control, reader requests with identity, Caddy statuses, the delivered post text and chat id |
| `commands`, `resources` | every command with cwd, exit code, duration and log; created resources and their clean-up |

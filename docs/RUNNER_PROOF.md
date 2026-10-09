# Fresh installed-product runner proof

`.github/workflows/runner-proof.yml` (job `Fresh product runner proof`) proves, on a free GitHub
runner and without an LLM, BitLaunch or live Telegram, that a new order's product works end to
end: a fresh Copier backend,tg_bot product from the exact kit candidate is installed with
tg-channels by the orchestrator's real executor, passes its own CI, builds and publishes its runtime
images the way its main job does, and delivers a channel post to a chat through the pinned
platform's real auth and Caddy. The runner is `tests/runner/fresh_product.py`.

The job is a release matrix of two legs, both on the full path below:

| Leg | `--packages` | Proves |
|---|---|---|
| `fresh` | `tg-channels` | a fresh backend,tg_bot product with tg-channels |
| `coexistence` | `reminders,tg-channels` | the published reminders (with textparse) installed first, then tg-channels: settings, jobs, events and bindings side by side, and reminders still delivering |

It runs on every pull request to `main` and every push to `main`, without path filters. It is not
green when it did not run: a fork pull request, a missing `PLATFORM_SERVICES_DEPLOY_KEY`, a
non-SHA pin, an unknown proof or catalog mode, or an own run in another catalog mode than its
event's fails the first step, and the last step fails unless the uploaded evidence says `passed`
for exactly the pinned revisions, the requested proof and catalog modes, the expected tg-channels
version and source, and the leg's packages.

## Proof modes

Two explicit, independent choices, both recorded in the evidence (`proof_mode`, `catalog_mode`):
which tg-channels release is installed, and which catalog the planner and the executor read. A
failing remote never switches either of them; there is no fallback.

- `published_release` installs a published tag. Before anything is planned the runner reads it
  from the real remote: `git ls-remote --tags` and a fetch of every `refs/tags/packages/*` into
  a bare repository under its work directory. Every copied tag must be the object id the remote
  lists, and the target tag must be the pinned release of `support.PUBLISHED_RELEASES`; for
  tg-channels 0.1.2 that is the annotated tag object `4f5918dba1a3afc7ad9012715cb8f83c33a8e1bc`,
  peeled commit `29f481f213544b0b7a5055451d5b4e55a6249b35` and package tree
  `676ee7061c6b08d17923f7c1fad3742798c9870f`. An unpinned version is refused. After the install
  the probe's own source for tg-channels must be that tag object, commit and tree, and every
  other installed component (reminders, textparse) the real remote's tag object; the backend's
  installed distribution and the committed wheel must carry the version. No tag is created.
- `candidate_release` proves a package release prepared in a candidate but not yet published:
  the pending entry of `packages/pending-releases.yaml`, through the pending fixture below. A
  green candidate run proves the prepared package source, not a published release. Nothing is
  pending now (tg-channels 0.1.2 was published and activated, see
  [tg-channels 0.1.2](releases/tg-channels-0.1.2.md)); the mode stays for a future pending
  release, is only ever invoked explicitly, and cannot satisfy this repository's own runs.

## Catalog modes

| `catalog_mode` | With | Catalog the planner and executor read | Used by |
|---|---|---|---|
| `remote_head` | `published_release` | the real catalog at the kit's default branch (`HEAD`), with no fixture and no URL rewrite | every push to `main` |
| `candidate_snapshot` | `published_release` | the candidate commit's own committed catalog, as `HEAD` of an isolated snapshot (prospective) | every pull request |
| `pending_fixture` | `candidate_release` | the candidate's catalog plus only the pending entry, in an isolated fixture | callers, for a future pending release |

A catalog change cannot be proven against the real default branch before it merges: until then
`HEAD` lists the previous catalog, and the planner (`KitCatalogReader` at `HEAD`) and the
unchanged `install_probe.py` (`read_catalog(DEFAULT_CATALOG_SOURCE, "HEAD")`) would both select
the old release. A pull request therefore proves the catalog it would publish
(`candidate_snapshot`, labelled `prospective: true`), and only the main push proves the
published path (`remote_head`). A pull request cannot attest that the real catalog was
activated, and a main push cannot fall back to a snapshot.

In every mode the runner records the catalog's commit, the SHA-256 of its bytes and its catalog
digest (the planner's and the probe's formula, `support.catalog_digest`). Each planner payload
must carry that digest; the preflight and readback probes refuse any other catalog themselves
(`catalog_changed`), so their passing is recorded as the probes' agreement.

### Real remote HEAD (`remote_head`)

Before planning, the runner clones the real `HEAD` (depth 1) and records its commit and catalog,
then waits (at most 15 minutes) until `raw.githubusercontent.com` serves the same catalog bytes
at `HEAD`: the raw service caches a push for minutes and the planner reads it. It only waits; it
never reads another source. The planner reads the raw catalog, default bindings and manifests
at `HEAD` and at the published tags, the executor fetches from GitHub, exactly as production. After
the installs the real `HEAD` is cloned again and its catalog digest must be unchanged.

### Candidate catalog snapshot (`candidate_snapshot`)

1. The bare repository holding the real remote's package tags (above) receives the exact
   candidate commit from the kit checkout as `refs/heads/main`, its `HEAD`. Its catalog blob must
   be the candidate checkout's `packages/catalog.yaml`, unchanged, and every tag that catalog
   names must be among the real remote's tags. No entry is added and no tag is created; the
   package source is the published tag's, not the candidate's tree.
2. The planner reads that repository through the loopback HTTP fixture and the executor through
   the process-scoped `HOME` below, so the planner, `kit add` and both probes read the same
   snapshot catalog, manifests and default binding bytes. Every HTTP read must succeed and every
   read of `HEAD/packages/catalog.yaml` must return the snapshot's bytes.

### Pending release fixture (`pending_fixture`)

1. A throwaway bare repository under the runner's work directory receives, by object id, the
   exact candidate commit and the kit checkout's published package tags (it fails if a catalog
   tag is missing).
2. A fixture catalog commit on top of the candidate adds only the pending `catalog_entry` to the
   package's `versions` (`support.fixture_catalog`); it becomes the fixture's `HEAD`.
3. The intended tag is created there, annotated, at the candidate. It exists nowhere else and
   is never pushed.

### Snapshot transport (`candidate_snapshot`, `pending_fixture`)

Only the source of the catalog and package tags changes; delivery is the same in every mode.

1. The orchestrator planner reads the catalog, default bindings and manifests through its real
   `KitCatalogReader` from a loopback HTTP fixture that serves raw files of the snapshot
   repository (`/<ref>/<path>`, like raw.githubusercontent.com), and builds the typed payload with
   `plan_install_payload`. Every request is recorded with its body digest.
2. While `run_install` runs, and only then, the runner's process `HOME` holds a `.gitconfig` with
   `url.<snapshot>.insteadOf https://github.com/vladmesh/codegen-product-kit.git`. The executor
   passes `HOME` to its children, so `kit add` and the subprocess `install_probe.py` fetch the
   same catalog bytes and tags from the snapshot with unchanged URLs, and the probe checks the
   payload's catalog digest, tag, package identity and binding resource itself. `.cache` and
   `.local` are linked from the real home for uv. Copier, product setup, CI, images and
   deployment use the normal home; the platform was checked out over its deploy key before the
   runner started, and no other credential or repository is rewritten.

Not patched: `run_install`, `install_probe.py`, the kit's `package_source`, catalog selection
and provenance checks, the kit, uv and git commands, their preflight/readback results and the
product sources. The template source and `_commit` come from GitHub as in production.

## Pinned inputs

| Input | Default | Source |
|---|---|---|
| kit candidate | PR head SHA, or the pushed SHA | `vladmesh/codegen-product-kit`, full history |
| orchestrator | `ec96fa79c84937982420f0f01388d90000bd69eb` | `vladmesh/codegen_orchestrator` (public) |
| platform | `070dfb359f9a121546d1362ae84c9d0856cabf64` | `vladmesh/codegen-platform-services` (private) |
| proof mode | `published_release` | this repository's runs; a caller must choose |
| catalog mode | `candidate_snapshot` on a pull request, `remote_head` on a main push | a caller must choose |
| tg-channels version | `0.1.2` | a pinned published release, or the pending entry in `candidate_release` |

The platform is checked out with the read-only deploy key `PLATFORM_SERVICES_DEPLOY_KEY` over SSH,
`persist-credentials: false`, into `platform/` outside the uploaded artifact path. The workflow
refuses any system/global `insteadOf` rewrite before the checkout, then verifies all three fetched
heads, that the platform origin is the SSH URL and that no credential stayed in its Git config.
The key is used only for a `push` to `main` and a same-repository pull request, also when another
repository calls the workflow (its event decides); any other event fails the first step. There is
no `pull_request_target` and no manual dispatch. This repository's own runs must be
`published_release` with `candidate_snapshot` on a pull request and `remote_head` on a main push;
the first step fails otherwise.

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
      proof_mode: published_release         # or candidate_release; required, no fallback
      catalog_mode: remote_head             # candidate_snapshot | pending_fixture; required
      package_version: 0.1.2                # the tg-channels version the planner must select
    secrets:
      PLATFORM_SERVICES_DEPLOY_KEY: ${{ secrets.PLATFORM_SERVICES_DEPLOY_KEY }}
```

After a catalog activation has merged, another repository proves its candidate against the
published path with `published_release` and `remote_head`; `candidate_snapshot` serves the named
kit commit's committed catalog instead. The caller provides its own copy of the read-only deploy
key secret. Locally the runner is never
executed (it needs Docker and network); in CI it is:

```bash
orchestrator/.venv/bin/python kit/tests/runner/fresh_product.py --kit-dir kit --kit-sha SHA \
    --orchestrator-dir orchestrator --orchestrator-sha SHA \
    --platform-dir platform --platform-sha SHA \
    --proof-mode published_release --catalog-mode remote_head --package-version 0.1.2 \
    --packages reminders,tg-channels \
    --output-dir "$RUNNER_TEMP/runner-proof"
```

Both matrix legs run for a caller too; `--packages` accepts companions from `COMPANIONS`
(`reminders`) followed by `tg-channels`.

The orchestrator environment is its own `uv sync --frozen` with `codegen-kit-tooling` replaced by
the kit candidate (`git+https://github.com/vladmesh/codegen-product-kit.git@<kit sha>`), so the
planner's typed install payload names the candidate tooling, as a production orchestrator would
after moving its kit pin. The runner refuses a planner whose tooling is not the candidate.

## Stages

1. **Revisions.** The three checkouts are at their pins; the planner tooling is the candidate.
   Then the release and catalog of the modes above: the published tag from the real remote and
   the candidate snapshot, or the pending fixture.
2. **Fresh product.** `copier copy --trust --defaults --vcs-ref=<kit sha>
   gh:vladmesh/codegen-product-kit` with `modules=backend,tg_bot` and no other data, so the
   tooling requirement is the template default at the candidate. The answers' `_commit` must
   resolve to the candidate. `git init`, `make setup`, a baseline commit pushed to a local bare
   `origin`.
3. **Payload.** In `remote_head` the real `HEAD` catalog is recorded first. For each package of
   the leg, in order, the orchestrator's own `KitCatalogReader` and `plan_install_payload` select
   it from the catalog at `HEAD` of the catalog mode's source; the payload's `tooling_commit` must
   be the candidate, its catalog digest the recorded one and the tg-channels version the
   expected one.
4. **Install.** `scaffolder.src.install.run_install` runs unchanged for each package on the same
   story branch: preflight probe, `kit add` (and recommended libraries), `kit bind --default`,
   generation, validation, per-service mypy, unit tests, readback probe, protected-file check,
   commit and push of `story/runner-proof`. The published remote head must equal the last
   executor result. The probe's tg-channels source must be the pinned published tag object,
   commit and tree (`published_release`) or the fixture tag object at the candidate with the
   candidate's package tree (`candidate_release`); the committed wheels are hashed. The catalog
   agreement above is checked, and in `remote_head` the real `HEAD` catalog is read again.
5. **Coexistence.** The product's own tooling (`load_specs`, `binding_files`) reports active
   packages, generated events, job timers and their owners, settings and bindings: every package
   is active, owns its timer and has its published events generated, one binding per package
   with disjoint commands, and every binding setting is declared.
6. **Product CI.** A cold clone of the story branch executes the `run` steps of the product's own
   `lint-and-test` job in order (setup, environment contract, generation drift, lint, typecheck,
   tests, dev compose smoke, integration tests, `always()` clean-up). `uses` steps are provided by
   the runner job with the same pinned Python and uv, which the runner checks.
7. **Cold main regression.** Another cold clone replays the 0.10.1 main recipe (`uv sync --frozen`,
   `uv sync --project services/backend --frozen`, `make generate-from-spec`) and must fail with
   `BindingEnvironmentError: tg_bot environment is not installed`, with root and backend prepared,
   tg_bot absent and the binding installed.
8. **Main images.** A third cold clone executes the product's `build-and-push` job `run` steps
   (`.env` placeholder, `sh scripts/prepare-env.sh root backend tg_bot`, generation; the registry
   login is the only skipped step), must leave no tracked change, then builds every matrix entry
   with the same Dockerfile and context, pushes it to an isolated `registry:2.8.3` on the runner
   loopback, records the digest and deletes the local image.
9. **Integration without host environments.** A fourth cold clone with no `.venv` anywhere runs
   `make test-integration`: generation in the backend dev/integration container uses only the
   image's product-owned environments.
10. **Platform.** The auth image is built from the pinned platform source with its own Dockerfile;
   `deploy/ci/fake-secrets.sh` writes synthetic secrets outside the artifact path. The platform's
   own `deploy/compose.yml` runs postgres, postgres-init, auth-migrate, auth and Caddy with its
   Caddyfile, under a runner-named project and a runner-created `orch-link` network.
   `tests/runner/compose.platform.yml` replaces only `tg-reader` and adds `runner-admin`, which
   registers the throwaway product, its tg-reader grant (the scopes and quota the installed package
   declares in its env contract) and a synthetic key through the real admin API, reads it back,
   and gets 401 problem documents through Caddy for a missing, unknown and malformed key. The
   reader must have received no request.
11. **Deployment.** The product's `infra/compose.base.yml` and `compose.prod.yml` are laid out as
    its deploy workflow does, with a `.env` from its `.env.example` plus synthetic values and
    `PLATFORM_BASE_URL=http://caddy:8080/tg-reader`. `tests/runner/compose.product.yml` only joins
    the backend to the platform edge network and points the bot at the fake Bot API. Images are
    pulled by digest from the registry; running containers must use exactly those references.
12. **Scenario.** Every binding setting (`language` = `en`, and `timezone` = `UTC` with
    reminders) and the user's access are set through the product's own `/settings/set` and
    `/users/grant`. The negative control comes first: with an unknown key,
    `/channel @runner_fixture` is answered `Service is not configured.`, Caddy logs the 401s, the
    reader receives nothing and no post is sent. The backend is then recreated with the registered
    key: the same command answers `Channel added: @runner_fixture`, the package's timer, poller
    and event relay deliver the fixture post to the user's chat, and the reader must have seen
    only requests with auth's identity for the throwaway product. The core timer fires the
    package's `tg_channels.tick`; nothing calls the poller or consumer directly. In the
    `coexistence` leg `/remind <text> in 1 minute` must then answer `Scheduled for ...` and the
    reminders timer and relay must deliver `Reminder: <text>` to the same chat. Last, the core
    `language` is set to `ru` and then back to `en` through the same `/settings/set`; in each
    language an unknown command and plain text must get the core registry's localized
    unknown-input reply listing `/channel`, and `/channel` without a name the binding's localized
    `on_empty` reply, all through the fake Bot API transport. A user-scoped `language` set and
    get must both be refused (422) by the real backend: the core language exists only in
    product scope.

## Fixtures and controlled edges

| Edge | Replacement |
|---|---|
| GitHub repository transport | a local bare `origin`; only the executor's `git remote get-url origin` is answered with `https://github.com/ci/runner-product`. Branch, commit, push and `ls-remote` readback are real. |
| Install fence | a callback recording every executor checkpoint |
| tg-reader | `tests/runner/fixtures/reader.py`: the published reader contract for one fixture channel. It answers only requests carrying auth's identity for the expected product (403 otherwise) and creates its single post on the first posts read after the channel was resolved, so a delivered post is newer than the subscription. |
| Telegram Bot API | `tests/runner/fixtures/telegram_api.py` under the network alias `api.telegram.org`, TLS from a runner CA that the bot trusts through `SSL_CERT_FILE`. A loopback control port queues a user message as an update and reads sent messages; it cannot add one. Delivery is accepted only after the watermark taken before the command. |
| Registry | `registry:2.8.3` on `127.0.0.1`; no release or production image is pushed |
| Catalog (`candidate_snapshot`) | the isolated snapshot repository above (the candidate's committed catalog as `HEAD`, the real remote's package tags by object id), reached by the planner over the loopback HTTP fixture and by the executor's git through a process-scoped `HOME/.gitconfig` `insteadOf` |
| Package release and catalog (`pending_fixture`) | the isolated fixture repository, fixture catalog commit and local intended tag above, over the same two transports |

Everything else is the real thing: Copier, the candidate template and tooling, the published
catalog and releases (`remote_head` has no catalog or release fixture at all; the snapshot modes
copy the published tags by object id), the orchestrator executor and probes, the product's Makefile,
workflows, Dockerfiles and Compose files, the platform's auth, Postgres, Caddyfile and compose.

The runner creates only resources named with its random prefix (`rp<hex>-registry`,
`rp<hex>-orch-link`, Compose projects `rp<hex>-platform` and `rp<hex>-product`) and removes them
in a `finally` block, also after a failure; the product's own CI steps clean up their own Compose
projects. All secrets are synthetic and generated per run.

## Evidence contract

The artifact `runner-proof-<leg>-<kit sha>-<attempt>` holds `runner-proof.json`, the product's
environment contract artifact and one log per command. Every known synthetic secret is replaced by
`<redacted>` in all of them, and the Caddy log must contain no product key. `runner-proof.json`:

| Key | Content |
|---|---|
| `schema` | `codegen-product-kit/runner-proof/2` |
| `status`, `error` | `passed` or `failed` with the redacted failure |
| `proof_mode`, `catalog_mode`, `matrix` | the modes; the leg's packages in install order and the expected tg-channels version |
| `release` | mode, package, version, tag object/target and package tree, the probe's source and planner catalog digest, the installed distribution, committed wheel SHA-256s; in `published_release` also `published_tag` (type, object, target, tree as verified on the real remote) and every real remote package tag; in `candidate_release` `published: false`, intended tag, pending metadata digest, source SHA, fixture catalog commit and digest and the copied tags |
| `catalog` | mode, `prospective` (true for both snapshot modes), `HEAD` commit, catalog bytes SHA-256 and catalog digest, the planner digest per package and each probe's acceptance; in the snapshot modes every HTTP fixture request with its body digest; in `remote_head` the real source, the raw URL waited for and the `after_install` re-read |
| `pinned`, `revisions` | kit, orchestrator and platform SHAs; planner and installed product tooling provenance |
| `template` | Copier source, `_commit`, its resolved SHA, modules, tooling requirement |
| `install_payloads`, `installs` | per package: typed payload; executor stages with argv and exit codes, preflight and readback probe output, protected hashes, fence checkpoints, credential boundary (`install_payload`, `install`: tg-channels) |
| `modules`, `module` | per package (and tg-channels): version, tag, tag object/tree/target and installed distribution |
| `coexistence` | active packages, published and generated events, job timers and owners, settings and bindings, the product's core command registry and host-contract violations; the language owner must be `core` and the registry exactly core `/start`, `/command` plus the bound modules' commands |
| `product_ci` | each `lint-and-test` step and its exit code |
| `cold_main_regression` | the old recipe, prepared environments, exit code and the reproduced error |
| `main_images` | each `build-and-push` step; per image Dockerfile, context, tag, digest and reference |
| `integration_without_host_environments` | the cold `make test-integration` summary |
| `platform` | auth image, throwaway product id, grant, admin read-back and ingress refusals |
| `deployment` | the digest references the running backend and bot use |
| `scenario` | initialization responses, negative control, reader requests with identity, Caddy statuses, the delivered post text, chat id, URL and post id; in `coexistence` the reminder command, reply and delivery; `languages`: the refused user-scoped set/get statuses, then per `ru`/`en` the language write and the unknown-command, unknown-text and `/channel` replies |
| `commands`, `resources` | every command with cwd, exit code, duration and log; created resources and their clean-up |

# tg-channels 0.1.2

Prepared 2026-10-09. A package-only patch release; no kit core, tooling, template, orchestrator or
`CORE_VERSION` change. `requires_core` stays `>=2.4,<3`.

Status:

- **Tag published** 2026-10-09 (18:09:39 UTC), before any catalog entry: the annotated tag
  `packages/tg-channels/v0.1.2`, object `4f5918dba1a3afc7ad9012715cb8f83c33a8e1bc`, at the merge
  commit `29f481f213544b0b7a5055451d5b4e55a6249b35`, package tree
  `676ee7061c6b08d17923f7c1fad3742798c9870f`. The 0.1.0 and 0.1.1 tags are unchanged.
- **Catalog activation** is the change that appends the 0.1.2 entry to `packages/catalog.yaml`,
  removes the pending entry from `packages/pending-releases.yaml` and pins the tag above in the
  runner proof. Until it is merged, `kit add tg-channels` keeps selecting 0.1.1.
- **Proof of the published path.** The activation's pull request proves the published tag against
  its own catalog served as an isolated, prospective `HEAD` (`catalog_mode=candidate_snapshot`).
  The published path itself (the real default branch's catalog and tag, no fixture) is proven
  only by the Runner Proof matrix of the push to `main` at the merge commit
  (`catalog_mode=remote_head`); see [RUNNER_PROOF.md](../RUNNER_PROOF.md).

## Defect

The kit loader names every package job `<package prefix>.<job>`, with the prefix being the package
name with `-` replaced by `_` (`framework/spec/loader.py`, `_package_prefix`). The generated
`JOB_TIMERS` of a product with tg-channels is therefore `{"tg_channels.tick": 60}` and the core
timer loop fires `job_fired` with `name: tg_channels.tick`. Published 0.1.0 and 0.1.1 consumed
only `tg-channels.tick` (`codegen_kit_tg_channels/runtime.py`, `ChannelConsumer.handle_job`), so
every tick was received, acknowledged and ignored: the package never polled the reader and no
subscribed channel post was ever delivered.

Found by the runner proof of codegen-product-kit-49 (run 37960152870 at candidate
`14d0a086834b5b53b22b7765b7cd3e4bc867a27d`): a subscription through real auth and Caddy
succeeded, the backend logged `job_fired` received/processed every minute and the fixture reader
received no posts request until the 300 s delivery timeout.

## Change

- `ChannelConsumer` runs the poller for `tg_channels.tick`, the name the core fires
  (`TICK_JOB`). The core naming contract is unchanged, and so is the declared event
  `tg-channels.post`, which keeps its own published name; no event name is normalized.
- Other jobs, another package's tick and the event-namespace spelling `tg-channels.tick` are
  still ignored (`tests/test_runtime.py`).
- Version 0.1.2 in `pyproject.toml` and `package.yaml`; README states the core job name.
- No framework, template or orchestrator branch names this package.

The regression `tests/tooling/test_package_timers.py::
test_channel_consumer_runs_the_tick_the_core_generates_from_its_manifest` loads the shipped
`package.yaml` with the kit loader over the template's core specs, generates `jobs_schemas.py`,
takes the package's timer name from the generated `JOB_TIMERS` and hands it to the package's own
`ChannelConsumer.handle_job`; the poller must run once, and a name owned by another package must
not run it. No job name is written in the test, so the 0.1.1 consumer fails it.

## Prepublication proof

Before the tag existed the runner proof ran in `proof_mode=candidate_release`
([RUNNER_PROOF.md](../RUNNER_PROOF.md)): the orchestrator's real planner and executor installed
0.1.2 from an isolated fixture repository holding the exact candidate, a fixture catalog commit
with only the pending entry appended and the intended tag created locally at the candidate. The
release matrix covers a fresh backend,tg_bot product with tg-channels and a product with the
published reminders 0.5.0 (and textparse 0.1.0) installed first, then tg-channels; both run the
product's CI, main image path, registry, real auth and Caddy and timer-driven delivery, and the
second leg also delivers a reminder through the companion module.

That proved the prepared package source (candidate matrix at `1d413dd`, then main at `29f481f`),
not a published release.

## Publication (separate operation, done)

From reviewed, merged main with a green Runner Proof matrix on that merge commit:

1. Confirm the merge commit `M` has `pyproject.toml` and `package.yaml` version 0.1.2, the
   pending entry in `packages/pending-releases.yaml`, and no 0.1.2 entry in `packages/catalog.yaml`:
   `git show M:packages/codegen-kit-tg-channels/pyproject.toml`,
   `git show M:packages/pending-releases.yaml`.
2. Record the package tree: `git rev-parse M:packages/codegen-kit-tg-channels`.
3. Create the annotated tag `packages/tg-channels/v0.1.2` at `M` and push only that tag. Do not
   move or recreate `packages/tg-channels/v0.1.0` or `v0.1.1`; no kit core tag.
4. A following code change appends the pending `catalog_entry` to the tg-channels `versions` in
   `packages/catalog.yaml`, removes the pending entry, sets the runner default to
   `proof_mode=published_release` with `package_version=0.1.2`, and proves the published path:
   prospectively on its PR (`candidate_snapshot`), on the real catalog on main (`remote_head`).

Steps 1-3 were done with `M` = `29f481f213544b0b7a5055451d5b4e55a6249b35`; step 4 is the catalog
activation above.

## Scope

New products only: after activation a new product's `kit add tg-channels` selects 0.1.2, and the
runner proof covers a fresh product and one with reminders installed first. An upgrade of an
existing product that installed 0.1.0 or 0.1.1 is neither implemented nor proven by this release,
and this note gives no upgrade procedure for one.

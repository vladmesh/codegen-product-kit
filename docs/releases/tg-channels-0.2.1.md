# tg-channels 0.2.1 preparation

A package-only patch release. No kit core, tooling, template, orchestrator or `CORE_VERSION`
change; `requires_core` stays `>=2.4,<3` and package protocol stays 1. Pending, not published:
`packages/catalog.yaml` lists 0.2.0 as newest and `packages/tg-channels/v0.2.1` does not exist.
The release is pinned in `packages/pending-releases.yaml` and supersedes 0.2.0.

## Defect

An HTTP 401/403 from the reader's posts read sets `tg_channels.poll_state.stopped`, and 0.2.0
never cleared it. A backend restarted with a corrected or rotated platform key therefore never
polled again and delivered no post. The runner proof hits this when its unknown-key negative
control overlaps a timer tick: the stop outlives the recreate with the registered key and the
fixture post is never fetched.

## Change

- Package startup with a configured platform key clears the persisted stop before the job
  consumer starts (`Store.resume_polling`). Startup without platform access leaves the poll state
  unchanged. Within one running backend a 401/403 still stops polling durably.
- Version 0.2.1 in `pyproject.toml` and `package.yaml`; the README describes the recovery.
- No API, event, manifest contract or binding change.

The regression `tests/test_unconfigured.py::test_startup_with_rotated_key_resumes_polling_stopped_by_old_key`
starts the real package lifecycle with key A (the reader answers 401 and polling stops), then
with key B over the same store; the second tick must read posts and clear the stop.

## Publication (separate operation)

From reviewed, merged main with a green Runner Proof on that merge commit `M`:

1. Confirm `M` has version 0.2.1 in `pyproject.toml` and `package.yaml`, the pending entry, and
   no 0.2.1 entry in `packages/catalog.yaml`.
2. Record the package tree: `git rev-parse M:packages/codegen-kit-tg-channels`.
3. Create the annotated tag `packages/tg-channels/v0.2.1` at `M` and push only that tag. Earlier
   tags are unchanged.
4. A following code change appends the pending `catalog_entry` to the tg-channels `versions`,
   removes the pending entry, pins the tag in `support.PUBLISHED_RELEASES` and sets the Runner
   Proof default to `package_version=0.2.1`. Until that merge, `kit add tg-channels` selects 0.2.0.

Existing products on 0.2.0 upgrade afterwards with `kit add tg-channels`.

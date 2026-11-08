# tg-channels 0.2.1

A package-only patch release. No kit core, tooling, template, orchestrator or `CORE_VERSION`
change; `requires_core` stays `>=2.4,<3` and package protocol stays 1.

Release identity: distribution `codegen-kit-tg-channels` 0.2.1, entry point `tg-channels`,
superseding 0.2.0. Published as the annotated tag `packages/tg-channels/v0.2.1`, object
`f7a49c9c8e0b42517fe36a9d42edb01ba679a619`, at commit `bfa3f84c8f4c6928527168fb3b84939508a4cfa5`,
package tree `a86492bca55e2c9418c7b7252a067ef90005bd1b`. Earlier tags are unchanged.

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

## Publication

`packages/catalog.yaml` lists 0.2.1 as the newest tg-channels version, so `kit add tg-channels`
selects it for a product core in `>=2.4,<3`. Nothing is pending for tg-channels. The runner's
`support.PUBLISHED_RELEASES` pins the tag object, commit and tree above, and the Runner Proof
defaults to `proof_mode=published_release` with `package_version=0.2.1`: a pull request against
its own catalog (`catalog_mode=candidate_snapshot`), a push to `main` against the real default
branch's catalog (`catalog_mode=remote_head`); see [RUNNER_PROOF.md](../RUNNER_PROOF.md).

Existing products on 0.2.0 upgrade with `kit add tg-channels`.

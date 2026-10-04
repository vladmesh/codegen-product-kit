# textparse 0.1.0 source and release operation

This change prepares `codegen-kit-textparse` 0.1.0 and the library install branch. It
does not publish a tag, alter reminders releases, or establish the later default binding.
The live v1 catalog recommends textparse with reminders and declares only `when`.
Released 0.7.1 readers continue to see packages and select reminders 0.3.0 for core 2.0
and 0.4.0 for core 2.1. Library installs require tooling containing this change; additive
catalog data cannot retrofit the command into pinned old tooling.

After the PR merges and main CI passes, the PO release operation must record the exact
merge commit and its `packages/codegen-kit-textparse` Git tree digest. Verify distribution,
version, catalog signature and tests at that commit; build and inspect the real wheel.
The worker report records the candidate source tree digest for comparison. Main's merge
SHA does not exist at worker time. Do not substitute a candidate SHA for the merged release.

```bash
git fetch origin main --tags
release_commit=<reviewed-merge-sha>
git show "$release_commit:packages/codegen-kit-textparse/pyproject.toml"
git rev-parse "$release_commit:packages/codegen-kit-textparse"
git show "$release_commit:packages/catalog.yaml"
git tag -a packages/textparse/v0.1.0 "$release_commit" -m "textparse 0.1.0"
git push origin refs/tags/packages/textparse/v0.1.0
```

The tag is independent of kit core tags. If a core tag is also planned at this merge,
follow CONTRACTS.md's separate-commit rule and publish the package tag on the reviewed
PR source commit with the same verified package tree. Never move a published tag.
Confirm remote tag dereference, then run `kit add textparse` using the real remote source
in a disposable generated product, import/call through its tg_bot interpreter and build
its tg_bot image in CI. Record those release receipts. Until that operation, the default
remote install reports `PackageNotPublishedError`; local CI tags prove only the candidate.

Local worker validation is limited to unit/tooling and non-slow Copier checks. The
slow Copier proof uses a local annotated release-tag fixture with the real Hatch build,
uv install, generated tg_bot interpreter and Docker image path. That proof runs in the
existing `test-pytest` slow CI leg, with no remote package release required. The later
product binding and end-to-end sprint acceptance remain separate work.

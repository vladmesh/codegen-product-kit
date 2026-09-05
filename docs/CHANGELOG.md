# Contract changelog

## 2026-09-05

- Generated products resolve the installable `codegen-kit-tooling` distribution from an exact Git
  commit recorded in the root lock. The Python import remains `framework`.
- Copier now creates that lock as a trusted generation task. The framework source copy and its
  synchronization commands are removed.
- Backend production images contain application dependencies only; generators and validators are
  confined to the Docker development target.

The release-oriented project history remains in the root `CHANGELOG.md`.

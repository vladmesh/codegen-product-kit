# Catalog fixtures

`released_0_7_1.py` is the unmodified `framework/catalog.py` from kit tag `0.7.1`:
commit `56da5c83cb8d011823ce2cb70345415b223b93ab`, Git blob
`d48db55ceefff9743e4f04ac952ca676192a8181`. Reproduce with
`git show 0.7.1:framework/catalog.py`. Tests import and execute this source offline;
they do not emulate its reader.

`components.yaml` is a contract example, not a release catalog. Its actions,
recommendation, default binding, English textparse release and synthetic extension
do not claim that those artifacts ship. Reminders' existing versions are retained
to exercise the released reader's core 2.0/2.1 selections. Subsequent cards must tie
new metadata to real release sources before publishing it in the repository catalog.

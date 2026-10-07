# V1 emission baseline

These files pin the default reminders 0.5.0 binding emission from kit main
`ea3db4ec1d4a975da85038b067fee9f5269b4940` (0.9.0), using ruff 0.16.6 and the
product's `template/ruff.toml`. Inputs are the unchanged package manifest/default binding
and `textparse -> codegen_kit_textparse`; action paths include the manifest's HTTP prefix.

The original templates have these SHA-256 digests:

- `bindings.py.j2`: `06fea487ddfc78ea3529e5837d2d53755cc17c2e9dc66c5fab1385f60dc35266`
- `binding_relay.py.j2`: `87ac5cf9cd3503544dbf706f23011f3e4b18ea0789254c701f1677a1c7b52485`

V2 leaves both templates intact. The unit generator test and actual bound-product regression
compare emitted files against these bytes. Do not refresh these files for a v2-only change.

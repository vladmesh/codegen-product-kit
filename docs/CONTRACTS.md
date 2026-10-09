# Framework contracts

## Generated-product tooling

The repository root is the only source of the `codegen-kit-tooling` distribution. Its installed
Python package remains `framework`. Copier renders an exact Git commit requirement into each
product's root `pyproject.toml` and runs `uv lock`, so setup, generation, spec validation,
controller checks, and tooling tests all use the committed resolution. The lock-generation task is
why project bootstrap requires Copier's `--trust` flag.

The kit's template workflow passes the pull request head repository URL and head SHA as Copier data.
That commit is reachable from the contributor's remote, unlike GitHub's synthetic merge commit; push
runs use the current repository and commit. This override remains an exact Git requirement and is
used only while generating the workflow candidate. Ordinary users receive the resolved template
commit in the same requirement position.

The released-product upgrade proof uses no tooling answer override. It copies genuine annotated
kit `0.7.1` (core 2.1.0, producer `56da5c83cb8d011823ce2cb70345415b223b93ab`), commits the
unmodified backend,tg_bot output, then runs native `copier update --defaults --trust
--vcs-ref=<full-candidate-SHA> --conflict=rej` against its saved answers/source. The root requirement
and lock must move from producer to candidate; installed tooling direct URL and import origin
must identify the product environment before generation/install. Protected environment/spec/app/
controller bytes are compared immediately after Copier. Rejections, backups, unresolved markers
and unmerged paths fail the proof even if Copier exits zero. This supports the unmodified released
shape, not automatic merging of arbitrary customizations or fabricated intermediate bindings.

The proposed kit core tag `0.8.0` delivers façade 2.2.0/protocol 1; tooling/application distribution
versions and minimum Copier stay unchanged. [0.8.0 preparation](releases/0.8.0.md) defines the
reviewed update/setup/add/bind sequence, explicit product timezone value and publication protocol.
No core tag is published until this card's reviewed merge has green main CI. The separate PO
operation tags that later merge, separate from immutable package release commits.
Patch `0.8.1` keeps these versions and makes every generated Python file's final bytes formatter
output, so regeneration, the generated-tree drift check and `make lint` agree; see
[0.8.1 preparation](releases/0.8.1.md).
The next prepared tag, `0.10.0`, delivers façade 2.4.0 and finite bilingual binding v2;
see [0.10.0 preparation](releases/0.10.0.md). The 0.9.0 platform environment source contract
and v1 bindings remain compatible.
Façade 2.5.0 adds the [core host contract](#core-host-contract-v1): the core-owned `language`
setting, one Telegram command registry with a core unknown-input reply, its hard lint and the
read-only `kit check-install` preflight. The façade semver is versioned independently of the kit's
Git release tags; package protocol stays 1 because its serialized manifest surface is unchanged.

Tooling is a development boundary, not an application runtime dependency. The backend Dockerfile's
`dev` target installs the root tooling lock for integration generation; its final `runtime` target
copies only the service environment and application sources. An audit of `template/services/`,
`template/shared/`, and generated Python sources found no runtime `framework` import.

## Package protocol v1

Package protocol version `1` is the stable boundary between an independently built Python wheel and
any generated product that installs it. The boundary is a generated in-product `codegen_kit` façade,
not a separately released runtime distribution. This avoids making the whole product core a public
runtime dependency. `CORE_VERSION` is the kit-declared semantic version of this façade and its
activation semantics; it is rendered into each product and is deliberately independent of the exact
Git SHA used to deliver `codegen-kit-tooling`. The initial v1 surface was `1.0.0`; package database,
session, and event-publication seams raised it to `1.1.0`. Backward-compatible public additions require
a minor bump, breaking changes require a major bump, and fixes that preserve the promised surface
require a patch bump. The package protocol version remains `1` across compatible additions. A
package imports `Package`, `CORE_VERSION`, `PACKAGE_PROTOCOL_VERSION`, `package_database`,
`publish_event`, and `caller_identity` from `codegen_kit`; product-specific `services.*`, generated contracts, and
application settings are not public API. Version `1.2.0` added stable `event_id`, `occurred_at`, and
`schema_version` publication metadata for durable package outboxes. Version `1.3.0` added the
optional `SettingSeedPackage.seed_setting(session, key, value)` callback, activated only by an owned
`setting_seeds` declaration. Version `2.0.0` removes the unowned
`package_base(schema)` and `package_session(schema)` selectors. `package_database()` takes no
identity argument: it resolves the caller to exactly one installed entry-point package directory,
revalidates that directory's manifest identity, distribution version, core compatibility, and
database declaration, then returns the only supported capability for creating an independent ORM
base or opening a schema-local transaction. An unowned caller, ambiguous installed ownership,
missing database declaration, or changed identity fails before the backend session factory is
imported. `publish_event()` uses the generated product transport. Version `2.1.0` adds the optional
`timers` manifest field, fired by the [core timer loop](#core-timer-loop), and the
`caller_identity` request dependency of the [core caller identity](#core-caller-identity-v1); a
package that declares timers or depends on the caller identity requires `>=2.1`, and a package
without them is unchanged. Version `2.2.0` admits optional `actions`, `default_binding`, and
message `recipient` metadata. Tooling validates these contracts; runtime activation executes
none of them and imports no tooling, channel or parser code. Reminders 0.5.0 requires `>=2.2,<3`.
Version `2.3.0` admits optional `environment[].source` platform grant and endpoint declarations.
Packages using these fields require `>=2.3,<3`; earlier manifests and compatible ranges are unchanged.
The unchanged wheel can therefore
be installed into another generated product with the same compatible core without rebuilding it.

### Package manifest

Each wheel's entry-point module must resolve to a package directory. It installs exactly one
`package.yaml` as distribution data, at `<entry-point-module>/package.yaml` inside that directory.
A single-file module, a missing module root, or a manifest installed anywhere else is not a protocol
v1 package. This location is fixed in v1 so two independently installed package module trees cannot
overwrite one shared site-packages-root manifest. Protocol v1 validates the manifest fail-closed and
rejects unknown fields. Its fields are:

| Field | Meaning in v1 | Activation enforcement |
|---|---|---|
| `protocol_version` | Integer package protocol version, exactly `1` | Enforced |
| `name`, `version` | Entry-point identity and package release identity | Enforced; missing identity has `MissingPackageIdentityError` |
| `requires_core` | PEP 440 specifier matched against `codegen_kit.CORE_VERSION` | Enforced |
| `provides`, `requires` | Provided and required logical interfaces | Enforced across the active set with named duplicate and missing-provider refusal |
| `package_dependencies` | Top-level Python imports allowed in addition to stdlib and `codegen_kit` | Enforced by the kit import lint |
| `http.prefix` | Absolute non-root mount prefix with no trailing slash, `//`, or path parameters | Enforced; malformed values have `MalformedPackagePrefixError` |
| `database.schema`, `database.migrations` | Package-owned PostgreSQL schema and `module:path` Alembic revision resource | Enforced; migrated by the core |
| `deployment.modes` | Optional non-empty set of deployability declarations; `in_process` is the only accepted value and the default | Enforced; a declared `container` mode has `UnimplementedDeploymentModeError` |
| `events.publishes`, `events.consumes`, `events.messages` | Package event names and inline Draft 2020-12 message schemas | Enforced and merged during generation |
| `settings_schema`, `jobs_schema` | Draft 2020-12 schemas merged under the normalized package-name prefix | Enforced and merged during generation with named duplicate refusal |
| `timers` | Optional `[{job, every_seconds}]`: local `jobs_schema` jobs the core fires once per period with the slot instant as `at` (core `2.1.0`) | Enforced at the manifest model; refusals have `InvalidPackageTimerError`; recorded in the generated `JOB_TIMERS` |
| `setting_seeds` | Optional ordered `{key, scope: product}` bindings to package-owned local setting names | Enforced against `settings_schema`; duplicate, unknown-key, unsupported-scope, malformed, and unknown nested fields are refused |
| `environment` | Named environment requirements, requiredness, and optional discriminated platform `source` (core 2.3) | Enforced in the generated package environment-contract fragment; see [Environment contract v1](#environment-contract-v1) |
| `resources` | Named distribution resource paths | Enforced as existing, non-traversing distribution resources |
| `actions` | Optional list of stable `{name, summary, operation: {method, path}, input, output}` records (core 2.2) | Tooling rejects unknown keys, duplicate/malformed names, methods/paths and invalid inline typed schemas; activation admits metadata only |
| `default_binding` | Optional non-traversing `module:path` resource (core 2.2) | Tooling validates syntax; wheel/source tests prove shipping; activation does not load it |
| `events.messages.<event>.recipient` | Optional name of a required string field in that existing message | Tooling rejects unknown, optional, nullable or non-string recipient fields; no separate emits registry |

The tooling API is `framework.spec.packages.load_package_manifest(path)`. Unknown fields raise
`UnknownPackageManifestFieldError`; invalid syntax and other schema errors raise
`PackageManifestError`. The runtime repeats activation-critical validation so production images do
not need the tooling distribution.

### Installation, discovery, and activation

The distribution declares one entry point whose name equals `package.yaml.name`:

```toml
[project.entry-points."codegen_kit.packages"]
weather = "weather_package:package"
```

Protocol v1 intentionally excludes dotted entry-point names: product manifests accept a name only
when replacing `-` with `_` produces a Python identifier, and the entry-point and package manifest
names must still match exactly. The module named on the right side must resolve to an installed
package directory; a top-level `.py` module and a target that resolves to nothing raise
`InvalidPackageModuleRootError` during activation and produce the same named lint violation.

The referenced object implements `codegen_kit.Package`: it exposes a FastAPI `router` and async
`startup(application)` and `shutdown(application)` methods. Install the wheel as an explicit backend
dependency, keep it in the backend lock, and add its entry-point name to
`services/backend/manifest.yaml`:

```bash
uv add --project services/backend \
  ./services/backend/packages/weather_package-1.0.0-py3-none-any.whl
```

```yaml
packages:
  - weather
```

For a kit package listed in the [package catalog](#package-catalog-and-releases), one tooling
command resolves the released package and performs every product mutation:

```bash
kit add reminders
```

It reads the catalog live from the kit repository, picks the newest released version whose
`requires_core` admits the product's core, fetches that version's package tag, builds the wheel
with `uv build`, and refuses the wheel unless its distribution, version and entry point equal the
catalog entry. It then copies that exact artifact under `services/backend/packages/`, adds the
backend dependency and lock entry, adds the package name to the manifest allowlist, synchronizes the
backend environment, and regenerates the active-package contract. The committed wheel stays the
product's installation boundary, so generated CI is unchanged.

An already built artifact is installed explicitly with `--wheel`; the catalog is then not read
live. The name must be a package of the catalog shipped with the installed tooling, and the wheel
file must be that package's distribution. Its version is the caller's choice:

```bash
kit add reminders --wheel /path/to/codegen_kit_reminders-0.4.0-py3-none-any.whl
```

On a main push, generated image CI repeats the committed installation boundary before generation:
it performs a frozen root tooling sync followed by a frozen `services/backend` sync, then runs
`make generate-from-spec`. The backend sync consumes only its committed project, lockfile, and
repository-local package wheels. Generation continues to resolve the allowlisted active set solely
from `services/backend/.venv`; CI does not expose runtime packages through the root environment or
provide a fallback when an entry point is absent.

Discovery uses installed distribution metadata, never module scanning or a catalog. The core
activates the package only when the entry point is installed and its name is listed. An installed but
unlisted package raises `InstalledPackageNotListedError`; a listed but absent entry point raises
`ListedPackageNotInstalledError`; an incompatible `protocol_version` raises
`IncompatiblePackageProtocolError`; and a `requires_core` mismatch raises
`IncompatibleCoreVersionError`. Two activated packages with the same `http.prefix` raise
`DuplicatePackageHttpPrefixError` before any package router is mounted. All abort application
startup. A package with any `setting_seeds` binding must expose an async method with the exact bound
signature `seed_setting(session, key, value)`; activation raises
`MissingSettingSeedCallbackError` before routing requests when the callback is absent or malformed.
A package with no binding need not expose the callback. With `packages: []`, existing settings,
jobs, events, and environment behavior is unchanged.

Generation and runtime activation are pinned to one active set: entry points installed in the
backend package environment and names listed in the backend manifest. Generation resolves that set
once, feeds it to every contract generator, and records package names, versions, and manifest
digests in
`codegen_kit._active_packages`; runtime refuses a changed manifest or wheel until generation is run
again. Installing or changing a package is therefore incomplete until the product contract is
regenerated. The backend site-packages path is explicit across the root-tooling/backend-environment
split.

Package settings and jobs are emitted as `<normalized-package-name>.<local-name>`, where hyphens are
normalized to underscores. The loader's shared ownership registry is the single duplicate-refusal
mechanism for service and package declarations; generators render its already-merged settings and
jobs registries. Collisions between a package and a service, or between normalized package prefixes,
name both declarers. Package event names remain their declared stream names, while their generated
Python publisher identifiers are normalized and claimed separately so names such as `order_placed`
and `order.placed` cannot silently produce the same function. Each published or consumed event has
one `events.messages` entry containing its generated model name and inline JSON Schema. A published
event is a declaration; a consumed event is a reference that must bind to an existing declaration in
`shared/spec/events.yaml` or an active package with the same message schema. Conflicting bindings and
consumed events with no publisher are refused with both relevant package or product sides named. A
service domain may subscribe to the package event and refer to its model without editing
`shared/spec/events.yaml` or `shared/spec/models.yaml`.

"The same message schema" means structural equality of the JSON-compatible mappings obtained after
YAML parsing. Source formatting and mapping-key order are immaterial; JSON types, constraints,
required-field order, and array order are authoritative. For a product model, the generator's
Draft 2020-12 definition is authoritative after removing only its generated top-level `title` when
that title equals the declared model name. Package consumers must serialize their inline schema to
that representation; no looser validation-equivalence or `$ref` resolution is inferred.

The `deployment` declaration records the delivery form a package is activated in. Generated products
implement only `in_process`, so a manifest declaring `container` is refused fail-closed with
`UnimplementedDeploymentModeError` at manifest validation time, in generation and at runtime alike:
a manifest must never promise a delivery form that creates no service, image, or Compose entry. The
field is kept so that a future container implementation can lift the refusal. Package protocol
version `1` is unchanged because the declaration is optional and defaults to the already implemented
in-process form.

The generated product's `make lint` deliberately overrides Ruff's configured exclusions for its
format check, so generated Python files are checked for canonical formatting as rendered while
virtual environments and migration revisions remain excluded. Generated directories remain excluded
from Ruff's diagnostic lint rules.

Package-provided interfaces must have one owner in the active set, and every required interface must
already have an active provider. A package environment entry declares only that the named value is
needed; the product owns how that value is obtained. Generation validates and deterministically
merges all product-owned `env.contract.yaml` fragments except its own stale generated package
fragment. If the product already declares the name, the package fragment reuses that exact typed
declaration only when it covers `local` and `production`, includes the `backend` consumer, and is at
least as required as every package requirement for the name. Its source may remain a literal,
allocation, derived value, or secret. An invalid or conflicting product fragment, a missing required
environment or consumer, or an optional declaration for a required package value fails generation
with the variable and unmet invariant named. There is no second declaration with different source
ownership.

When no product-owned fragment declares the name, generation retains the protocol v1 fallback: a
non-empty backend-consumed user secret for local and production with the package description and
sensitivity metadata. Multiple packages may require the same name; generation emits one stable
entry and uses the strictest requiredness, independent of package order. Declared resources must
exist at their non-traversing distribution-relative paths.

`services/backend/scripts/migrate.sh` runs the core Alembic head first, then active packages in
manifest order. Each package migration resource is a standard Alembic script directory with an
`env.py`; the core passes its existing connection and owned version-table schema through Alembic's
public configuration attributes and invokes public `alembic.command.upgrade`. Each package gets its
declared schema as the connection search path and its own schema-local `alembic_version` table.
Alembic therefore reports divergent or missing revisions through its normal explicit command
failure. Re-running the command is a no-op at every head. A product-local wheel can be kept under
`services/backend/packages/`, which is copied before dependency installation in backend images.

The factory mounts each activated router under `http.prefix`. Once core connectivity is ready, the
backend lifespan calls package `startup` in manifest order. It calls package `shutdown` in reverse
order before closing core connectivity, but only for packages whose `startup` completed. Successfully
started packages are recorded on one application-state ledger; partial startup failure unwinds that
ledger, does not call `shutdown` on the failing or later packages, and does not let a shutdown failure
mask the original startup error. A package author therefore does not need to make `shutdown` tolerate
an incomplete `startup`. Core routers are registered first; if a package route has the same HTTP
method and fully resolved path as a core route, the core route wins, while non-colliding routes under
that package prefix remain available. Exact duplicate package prefixes are refused, while nested
prefixes such as `/a` and `/a/b` are accepted; final route collisions follow registration order. A
package connection check belongs in `startup` and must raise on failure. Package acceptance checks
must install the real wheel, resolve its real entry point, start the generated application, exercise a
prefixed route, observe lifecycle calls, run manifest validation, and run the import lint. The kit's
synthetic package performs these checks.

The generated product's `make lint` runs the installed-package import check. Package source imports
may target stdlib, `codegen_kit`, the entry point's one top-level module and its submodules, and
top-level modules named in `package_dependencies`. The scan is entry-point-scoped: it recursively
checks only the package directory named by the entry point. A second top-level module shipped in the
same distribution is not scanned; declaring it as a package dependency only permits imports of it
from the scanned tree. Importing product internals or an undeclared third-party package fails the
check. Each listed entry point must resolve to a recursively scanned package directory. A
single-file module, missing or empty source root, misplaced, missing, or ambiguous installed
`package.yaml`, manifest-to-entry-point name mismatch, missing distribution metadata, and an empty or
nonexistent explicit site-packages path fail the lint rather than producing a vacuous pass. This is
a source boundary, not a dependency resolver; normal Python packaging metadata still owns
installation of dependencies.
An editable install normally leaves sources outside site-packages, so if its distribution metadata
cannot locate the protocol directory there, import lint reports "sources could not be located" and
fails closed.

### First package: `codegen-kit-reminders`

`packages/codegen-kit-reminders` is an independently versioned wheel whose entry point is
`reminders = "codegen_kit_reminders:package"`. It is not a dependency of `codegen-kit-tooling` and
is not installed into generated products by default. A product opts in by installing the wheel in
the backend environment, listing `reminders`, and regenerating its product contracts. That process
adds the `/reminders` create/list/cancel API, the package-owned `reminders` PostgreSQL schema and
Alembic head, the declared `reminders.tick` job, and the `reminders.due` message and publisher. No
product-owned Python or schema file is authored for the installation.

The package supports only one-time text reminders owned by one user and an explicit timezone-aware
instant. It performs no recurrence, snooze, media, calendar, or natural-language
parsing. From `0.4.0` the package declares `timers: [{job: tick, every_seconds: 60}]`, so the
[core timer loop](#core-timer-loop) fires `reminders.tick` once a minute with the slot instant as
`at`, and a due reminder is emitted in production without any external caller; `0.4.0` therefore
requires core `>=2.1,<3`. An explicit caller, such as central QA, can still fire `reminders.tick`
through `POST /jobs/fire` with its own `at`; both fires take the same path and the tick is
idempotent over its rows. Products on core `2.0.0` keep `0.3.0`, which declares no timer.

From `0.4.0` the owner of a reminder is the [verified caller](#core-caller-identity-v1), never a
request value. Every route depends on `codegen_kit.caller_identity` and nothing else establishes
the owner:

| Route | Input | Acts on |
|---|---|---|
| `POST /reminders` | body `{text, remind_at}`; any other field is refused with 422 | creates a reminder owned by the caller |
| `GET /reminders` | none | the caller's reminders only |
| `DELETE /reminders/{id}` | the reminder id | the caller's reminder only; another user's reminder answers 404, the same as a missing one |

A request without a verified identity is refused by the dependency with 401 or 403 before the route
runs. There is no `user_ref` in a body, query or path. The stored `user_ref` column and the
`reminders.due` payload's `user_ref` keep their names and now carry the canonical caller form
`"<channel>:<external_id>"`, for example `telegram:123456`, so a product's bot can deliver a due
message to that chat. This is a breaking change for callers of `0.3.0`: central QA, existing bots
and any other client must send the identity headers instead of `user_ref`. Stored values are not
migrated; no product carries `0.4.0` data yet.

The package declares the non-empty product setting `reminders.reminder_owner_ref` and binds it to
its setting seed. The first successful settings write inserts one reminder under a deterministic
UUID with a fixed past instant and `scheduled` state. The setting string becomes its `user_ref`
unchanged. The package never interprets it, but only the canonical caller form, for example
`telegram:<id>`, lets that user see the seeded reminder through `GET /reminders`; any other value
seeds a reminder no caller can list or cancel. Conflict handling on that UUID is deliberately a no-op: replaying the same write,
recreating the application, or writing again after the reminder advanced never creates another row
and never resets its owner, timestamps, or state. The normal `reminders.tick` path, not the seed,
moves it through due, outbox, and emitted state.

A tick locks scheduled reminders at or before that instant, changes them to due, creates one
package-owned outbox row per reminder, and commits that transaction before publication begins.
Cancelled rows are excluded from that transition. The tick then reads the unconfirmed outbox rows
in a second short transaction and publishes them as `reminders.due` while holding no row lock and
no open transaction, so a stalled transport can never keep package rows locked. Each accepted
publication is confirmed by a third short transaction that sets the emitted marker; every later
tick retries all rows that are still unconfirmed. The event UUID is derived deterministically from
the reminder's one-time occurrence. Consequently a restart after the due transition cannot lose the
notification, and a restart after Redis accepted it but before the emitted marker committed reuses
the same UUID. The envelope's `occurred_at` is the caller-supplied `reminders.tick` instant, not the
later emission time. A retry therefore republishes byte-identical event identity and occurrence
metadata.

This is exactly one logical notification per due reminder, not exactly-once transport delivery.
Because publication happens outside the outbox transaction, Redis Streams may contain duplicate
entries after a crash or when two ticks overlap; both carry the same stable UUID. There is still
exactly one outbox row per reminder, so a reminder never acquires a second logical identity. A
generated downstream consumer's transactional `(consumer_group, event_id)` guard collapses entries
carrying the stable UUID to one logical effect. External effects outside that transaction still need
their own idempotency boundary.

### Package catalog and releases

A kit package is versioned and released independently of the kit core. `packages/catalog.yaml`
lists every package and its released versions; `framework/catalog.py` is its only loader:

```yaml
format_version: 1
libraries: []                            # omitted here; textparse source entry described below
extensions: []                           # no production extension is shipped yet
packages:
  - name: reminders                       # entry point name and package.yaml name
    distribution: codegen-kit-reminders   # pyproject project.name
    path: packages/codegen-kit-reminders  # package source in this repository
    summary: One line on what the package does.
    capabilities: [remind me at a time]   # user-language phrases a planner matches a brief to
    settings:                             # package settings_schema properties
      - {name: reminder_owner_ref, summary: What the product supplies.}
    environment:                          # package.yaml environment
      - {name: REDIS_URL, required: true, summary: What it is used for.}
    versions:
      - {version: 0.3.0, tag: packages/reminders/v0.3.0, requires_core: ">=2,<3"}
      - {version: 0.4.0, tag: packages/reminders/v0.4.0, requires_core: ">=2.1,<3"}
      - {version: 0.5.0, tag: packages/reminders/v0.5.0, requires_core: ">=2.2,<3"}
```

Reminders 0.5.0 and textparse 0.1.0 are independently published immutable package tags.
Their source trees and refs are preserved by the bindings increment; a later core release
does not republish either component.

The loader refuses, with a named `CatalogError` subclass, an unknown `format_version`
(`UnsupportedCatalogFormatError`), a missing or malformed field or a tag other than
`packages/<name>/v<version>` (`InvalidCatalogEntryError`), a repeated package name
(`DuplicateCatalogPackageError`), a repeated version (`DuplicateCatalogVersionError`), and a
version that is not canonical PEP 440 (`InvalidCatalogVersionError`). Component names are unique
across all three lists; distribution names are unique after Python distribution normalization
(`DuplicateCatalogComponentError`). Function/action names and recommendation targets are unique
within their owner. All version ranges use `packaging.specifiers.SpecifierSet`; malformed core,
Python or parent ranges raise `InvalidCatalogEntryError`. Kit tests tie the newest catalog
version of every package to its `pyproject.toml` and `package.yaml` on its annotated release tag
(distribution, name, version, `requires_core`), and the package source at HEAD to that release
or to its pending release (version, tag, `requires_core`, settings names and environment names).

#### Pending releases

The catalog lists published tags only. A package release prepared in kit source is pinned in
`packages/pending-releases.yaml`, which nothing in production reads (`kit add`, the install
probe and the orchestrator planner read only the catalog). With nothing pending the document is
`{format_version: 1, releases: []}`; an entry has this shape:

```yaml
format_version: 1
releases:
  - package: tg-channels
    distribution: codegen-kit-tg-channels
    path: packages/codegen-kit-tg-channels
    version: 0.1.3                        # example
    supersedes: 0.1.2                     # the catalog's newest version
    notes: docs/releases/tg-channels-0.1.3.md
    catalog_entry: {version: 0.1.3, tag: packages/tg-channels/v0.1.3, requires_core: '>=2.4,<3'}
```

While an entry is pending, the package source at HEAD must be exactly that release and newer
than the catalog's newest version, and the catalog must not list it. The runner proof's
`candidate_release` mode (`catalog_mode=pending_fixture`) installs it from an isolated fixture
catalog that appends only `catalog_entry` ([RUNNER_PROOF.md](RUNNER_PROOF.md)). Publication is
a separate operation: the annotated tag at the reviewed merge commit, then a code change that
appends `catalog_entry` to the catalog, removes the pending entry, pins the published tag
(object, peeled commit, package tree) in the runner's `support.PUBLISHED_RELEASES` and proves
the published release. A package release never adds its catalog entry before its tag exists.

That activation change is proven in two steps, because the planner and the install probe read
the catalog at the real default branch's `HEAD`, which lists the new entry only after the merge:
its pull request runs `published_release` against the candidate's own committed catalog served
as an isolated `HEAD` (`catalog_mode=candidate_snapshot`, evidence labelled prospective), and the
push of the merge commit to `main` runs it against the real `HEAD` with no fixture
(`catalog_mode=remote_head`). Only the latter shows the activated path. tg-channels 0.1.2 was the
first release activated this way: its tag `packages/tg-channels/v0.1.2` (object
`4f5918dba1a3afc7ad9012715cb8f83c33a8e1bc` at `29f481f213544b0b7a5055451d5b4e55a6249b35`,
package tree `676ee7061c6b08d17923f7c1fad3742798c9870f`) was published on 2026-10-09 before its
catalog entry.

#### Additive component metadata in v1

The format remains v1 because released products pin tooling while reading the catalog from the
default branch. Kit 0.7.1's reader and existing orchestrator readers see only `packages`, ignore
the additive fields, and retain the old fields and version selection. Core 2.0 still selects
reminders 0.3.0; core 2.1 still selects reminders 0.4.0; core 2.2 selects reminders 0.5.0. An offline regression executes the
unchanged 0.7.1 loader, with its tag, commit and source blob recorded in
[`tests/fixtures/catalog/README.md`](../tests/fixtures/catalog/README.md), on both the repository
catalog and a populated fixture. No second catalog or compatibility adapter is involved.

`Catalog.packages` and `Catalog.get(name)` retain package-only behavior. `Catalog.libraries` and
`Catalog.extensions` default to empty tuples when their lists are absent.
`Catalog.get_installable(name)` retains package/extension-only behavior.
`Catalog.get_component(name)` additionally resolves libraries at the `kit add` boundary.
An extension has the same fields, independent release tags and installation
recipe as a package, plus required `extends: {package: <name>, versions: <PEP 440 range>}`.
Its parent must name an entry in `packages`, not another extension or a library.
The `extends` field belongs only to records in `extensions`. Any occurrence under `packages`,
including a malformed or null value, raises `InvalidCatalogEntryError` during parsing and directs
the author to `extensions`; it cannot silently become a package without a parent precondition.
Other unknown additive keys remain accepted.

Packages and extensions may additionally declare:

- `actions`: a list of `{name, input, output}` records. `input` is an object JSON Schema with
  named `properties`; `required` names must be declared properties. `output` is a JSON Schema.
  Reminders 0.5 additionally publishes `summary` and `operation: {method, path}` matching its
  manifest. Operations use uppercase GET/POST/PUT/PATCH/DELETE and an empty path or slash-prefixed
  route suffix relative to `http.prefix`. Names use `[a-z][a-z0-9_]*`. Path placeholders must
  name required inputs. Schemas are inline typed Draft 2020-12 objects/arrays/scalars; no external
  lookup, reference aliases or action executor is introduced. The stable action vocabulary is
  declared, with schemas and methods/paths structurally verified against real FastAPI OpenAPI.
  Comparison flattens body plus public path/query inputs, resolves local OpenAPI refs, strips
  only titles/descriptions, and retains formats, requiredness, enum/default and constraints.
  JSON success outputs are checked too; deliberate schema/operation drift fails the test.
- `recommended_with`: a list of `{library, why}` records naming catalog libraries. This is
  curated usefulness, independent of computed type compatibility. It triggers no installation.
- `default_binding`: `python.module:relative/resource/path` identifying a resource shipped by
  that component. It is an author-provided starting point for a product-owned binding;
  the catalog neither installs nor executes it. The loader validates its syntax, not resource
  existence. Release-source verification must establish that it actually ships.

Missing optional fields mean no declared action, recommendation or binding. Tooling must not
infer them for older packages. Catalog interface metadata does not certify an older selected
release; consumers must verify the selected installed manifest before generating bindings.
Manifest actions, finite validation, binding CLI, handler generation and relay admission share
the installed-product boundary below; validation calls no product runtime or parser.

Libraries declare `{name, distribution, path, module, summary, functions, versions}`. Each
function has `{name, input, output, value}`; `value` names one required property of its object
result, the primary output. A result may be nullable through `type: [object, 'null']` or an
`anyOf`/`oneOf` object-plus-null schema. Each library version has `{version, tag, requires_python}`,
using the same `packages/<name>/v<version>` convention but no core activation requirement.
Versions must be nonempty, unique and canonical PEP 440. Signatures are validated as Draft
2020-12 JSON Schema, with mapping schemas at nested positions; boolean property/item schemas
are outside this representation. Required named properties must exist.

Libraries are stateless language dependencies: no package entry point, allowlist, lifecycle,
database, HTTP activation or events. `kit add textparse` installs a plain dependency into
`services/tg_bot`; the library source and 0.1.0 catalog entry now exist. Its independent
published tag is `packages/textparse/v0.1.0`. Reminders recommends textparse. The real
0.5 manifest and catalog publish complete create/list/cancel schemas and the resource
`codegen_kit_reminders:bindings/default.yaml`; 0.3/0.4 remain unchanged and selectable.
The newest catalog metadata does not backport actions or binding support to old wheels:
binding admission loads the actual installed manifest/version. Historical fixtures
come from real release tags with byte/tree provenance; the 0.7.1 reader is unchanged.

#### Finite binding v1

`framework.bindings.load_binding(path)` reads typed data and `validate_binding(binding,
installed_manifest, catalog)` checks admission. Both reject unknown keys. It resolves action
names from the supplied installed manifest, not the newest catalog record. `kit bind` copies
the installed default to `services/tg_bot/bindings/reminders.yaml`. That copy becomes
product-owned; regeneration does not overwrite it. The resource remains package-owned.

The grammar is deliberately finite:

| Data | Contract |
|---|---|
| Header | `binding_version: 1`, `package`, `timezone: {key, scope: product, required: true, format: x-iana-tz}` |
| Commands | Unique bare command names, English `help`, discriminated `kind: parsed_create` or `kind: list` |
| Parsed create | `action`, `args`, `parse: {function: textparse.when, text: $text, lang: en, now: $clock, tz: $timezone}`, `nonempty_text: true`, `on_invalid_text`, `reply`, mandatory `on_empty` |
| On-empty branch | English `text`, `context: original_text`, exactly three ordered `presets` with label, explicit time, action, args and reply |
| Preset times | `{kind: offset, seconds: 300}`, `{kind: offset, seconds: 3600}`, `{kind: wall_time, days: 1, time: '09:00'}`; no free-text parse |
| List | `action`, `args`, `filter: {field: state, equals: scheduled}`, `reply_each`, `on_none`, buttons with label, action, args and reply |
| Events | Unique published `event`, `to` referencing that message's declared recipient, `reply`; no duplicate message registry |
| Reply | `parts` of literal strings or `{source: <reference>, format: month_word}`; `format` omitted for plain text |

Sources are only `$text` (unaltered text after the command), `$clock` (caller-supplied aware
instant), `$timezone` (explicit product setting), `$parsed.<field>`, `$preset.at`,
`$item.<field>`, `$event.<field>` and `$result.<field>`. Each is available only in its branch:
parse arguments use text/clock/timezone; parsed create uses parsed fields; preset callbacks
use original text and their computed instant; cancellation buttons use the listed item;
reply fields use the relevant action result, listed item or event. References must name
required fields in the real schema. Unknown/optional fields, unknown actions/functions/events,
unknown arguments, missing required arguments, incompatible schemas and duplicate commands
fail admission. Reply fields must have the declared display type. There is no eval, import
string, arbitrary expression, template interpolation, secondary parse function or scripting.
Literal reply strings are emitted as literals.

A nullable parse result takes `on_empty` exclusively and never calls create. A non-null result
maps `rest` to text and primary `at` to remind_at; empty original/parsed text takes
`on_invalid_text` before any create. That explicit guard proves the API's `minLength: 1`.
`argument_schema_matches` reuses the existing conservative matcher for plain argument types
and adds only guarded minLength support; unsupported constraints still refuse. The public
`primary_output_matches` inference remains semantic and demonstrates actual textparse `when`
matching actual reminder create's date-time, never `rest` or a secondary field.

The generated handler requires one configured product IANA timezone with no fallback;
`validate_product_timezone` refuses missing/invalid zones. The library receives the caller's
clock and explicit zone. Preset offsets use elapsed UTC seconds from the callback's caller
clock; tomorrow is the next local calendar date at 09:00 in that same zone. A wall time in a
DST gap or fold must fail explicitly, never guess an instant. Callback context retains the
original reminder text, not nullable parsed rest. `month_word` means an English month name
in the product zone, for example `7 October 2026 at 14:02`, independent of host locale.

The default `/reminders` displays only scheduled items, attaches cancellation to `item.id`,
and routes `reminders.due` to `event.user_ref` with `event.text`. Complete action and event
schemas remain in the manifest/catalog. Validators admit declarations; generated Telegram
commands, callbacks and the relay execute them in the product interpreter. Recurrence, custom preset values, filter expressions beyond equality, nullable/optional display
fields, nested field paths, arbitrary time formats, error routing and general binding workflows
are intentionally unsupported. See [reminders preparation](releases/reminders-0.5.0.md).

#### Finite binding v2

`load_binding` dispatches `binding_version: 2` to a separate finite grammar;
`validate_binding` reuses the v1 source, argument, reply-display and schema matchers against
actual installed manifests. V1 validators, parser constraints and generated output remain
unchanged. V2 needs façade 2.4 or later. A package shipping a v2 default or `actions[].errors`
must declare `requires_core: ">=2.4,<3"`; package protocol remains 1.

| Data | Contract |
|---|---|
| Header | `binding_version: 2`, `package`, `language: {key, scope: product, values: [ru, en]}`; optional v1-shaped `timezone` |
| Text | Exactly `{ru: <nonempty string>, en: <nonempty string>}` for help, literal reply parts, on_empty, on_invalid_text, on_none, error replies and button labels |
| Text create | `kind: text_create`, `command`, `help`, `action`, `args` mapping `$text` to exactly one argument, `reply`, `on_empty`, `on_invalid_text`, optional `on_error: {<declared code>: <Text>}` |
| List | `kind: list`, `command`, `help`, `action`, `args`, optional `filter: {field, equals: <string>}`, `reply_each`, `on_none`, optional `buttons` |
| Show | `kind: show`, `command`, `help`, `action`, `args`, `reply_each`, `on_none`; no filter or buttons |
| Button | Localized `label`, `action`, `args`, `reply`; ownership-bound single-use callback using the v1 bounded context machinery |
| Events | Published `event`, `to` naming its declared recipient, localized `reply` |
| Reply | Nonempty `parts` list of Text maps or `{source: <reference>, format: month_word}`; omit format for plain string display |

There are no parse functions or presets in v2. `$text` is the original text after the command,
with spacing preserved. Whitespace-only text takes `on_empty`, oversized or schema-invalid text
takes `on_invalid_text`, and neither invokes the action. The nonempty guard proves only
`minLength: 1`; stronger/unmodelled schema constraints still refuse admission conservatively.
A successful create uses `$result.<field>` in its reply. `on_error` selects a literal localized
reply only for a declared action error code. Action metadata may declare `errors: [not_found,
pending]`, a finite list of unique lower-case identifiers. The backend signals it as an HTTP
4xx body `{"detail": {"code": "not_found"}}`. Undeclared/unmapped codes, malformed errors,
5xx, transport failures and invalid output schemas use the localized generic failure reply.
Mutations are attempted once; raw error bodies never enter bot replies.

List and show are no-argument commands; extra text produces their localized help without
calling the action. Both call an action with an array output and render up to 100 items as
separate replies. Equality filters validate a required item field and their value against its
schema. Buttons use `$item.<field>` for action arguments and `$result.<field>` for replies.
Show has no callbacks. Events use `$event.<field>` for replies and their declared recipient.
Each field must be required, top-level and type-compatible. `$parsed`, `$preset`, `$clock` and
`$timezone` are unavailable argument sources in v2. Language and timezone are runtime
configuration, not implicit action arguments. There are no nested references, expressions,
interpolation, translation, arbitrary error routing or general workflows.

Unknown keys, missing/extra locales, actions/events/fields/arguments, unavailable branch sources,
incompatible schemas, duplicate command/event names and duplicate button labels in either
locale fail admission. Product preflight also refuses duplicate events/packages across bindings,
setting ownership conflicts and incompatible setting schemas; command names, reserved `start`/
`command`, the 32-character limit and collisions with product or other module commands are
resolved by the [core command registry](#core-host-contract-v1). All v2 bindings share one product
language key; all timezone declarations in v1/v2 share one product timezone key. Those two keys
must differ.

From façade 2.5 the language key must be the core-owned `language` setting
(`{type: string, enum: [ru, en]}`, product scope), present in every fresh product before any
module install. Bind references it and never declares it in `services/tg_bot/manifest.yaml`;
a binding naming another language key is refused as `binding_language_owner`. Bind never
writes a setting value. Commands, valid callbacks and events read `/settings/get` with
`contract_version: 1`, the declared key and `scope: product`, without `subject_id`.
Language is read again on a callback even if it changed after the list was displayed.
For commands and callbacks, missing (404), malformed or invalid language yields this fixed
bilingual setup reply:

> Настройте язык продукта (ru/en) через /settings/set. / Set the product language (ru/en) through /settings/set.

There is no default language, environment/host locale fallback, user scope or `/lang` command.
Settings transport errors, timeouts and 5xx responses instead yield the fixed bilingual reply:

> Настройки временно недоступны. Попробуйте ещё раз. / Settings are temporarily unavailable. Please try again.

One settings-read helper classifies both language and timezone failures for commands, callbacks
and the relay. Setup/timezone/temporary/stale-selection replies are fixed bilingual literals;
other runtime failures use RU/EN maps. Invalid callback contexts cannot recover a trustworthy binding and receive the
fixed bilingual stale-selection reply without executing an action.

`month_word` requires the optional explicit product timezone declaration. RU uses the table
`января … декабря`, EN uses `January … December`, independent of host locale. For example,
`7 октября 2026 в 21:00 MSK` and `7 October 2026 at 21:00 MSK` represent the same instant in
`Europe/Moscow`. Missing/invalid timezone gives a fixed bilingual setup reply; UTC is never
assumed. Plain-text-only bindings need no timezone.

The event relay retains v1 schema/recipient checks, bounded claims, deduplication, retries,
terminal refusal and lifecycle cleanup. It reads product language for each delivery. As in v1
with a missing timezone, configuration and settings-read failures send nothing, release the
claim and leave the original event pending for retry after recovery. Logs contain the event
identity, setting key and classified reason, without setting values, response bodies or transport
diagnostics. An expiring Redis diagnostic marker limits these logs to once per event per 60
seconds across consumers; it does not affect delivery claims or acknowledgement. V1 and v2
coexist without migrating reminders. V1-only output remains exactly `bindings.py` and `binding_relay.py`;
products with v2 add the generated `bindings_v1.py` companion for the shared runtime. Removing
v2 removes that companion. Synthetic v2 tests prove both languages using fake transports;
slow CI supplies the product typecheck and existing real-transport matrix.

#### Primary-output matching

`framework.component_matching.primary_output_matches(function: CatalogFunction,
parameter: dict) -> bool` is public and pure. Pass a validated library function and one action
input property's schema. It projects only the declared `value`; it never searches secondary
fields. `textparse.when`'s `at` matches `reminders.create.remind_at` because both are
`{type: string, format: date-time}`. `rest` is a plain string, a duration has another format,
and a recurrence whose primary value is an RRULE cannot use its secondary `first` instant to
create this edge. Duration and recurrence examples are negative schema tests, not supported
textparse functions. English `when(text, lang, now, tz) -> {at, rest} | None` is the sole
textparse 0.1 function.

The conservative matching subset is:

- `title`, `description`, `default`, `examples`, `$comment` and `$schema` annotations are ignored
  at schema positions, including nested schemas. Literal enum/const object contents are preserved.
- At the function result boundary, null means no result and is removed before projection.
  A parsed-create binding handles that branch. Null inside a primary value or parameter remains a union.
- Types and formats must agree. A target needs semantic discrimination through `format`,
  `enum`, `const`, semantic array items or a semantic required object field. A plain string
  parameter creates no inferred edge, even when the source is also a string.
- An output's enum/const values must be a subset of the parameter's allowed enum/const values.
  Both constraints are honored when present. Arrays compare item schemas recursively.
- Every required target object field must be required in the output and have a compatible
  property schema. Shared optional properties are checked too. A target with
  `additionalProperties: false` requires a closed output with no extra declared properties.
- Non-null unions (`anyOf`, `oneOf`, or multi-type `type`) infer no edge unless the normalized
  schemas are identical and semantically discriminating. Branch order is retained.
- References, numeric/string/length constraints and other unmodelled keywords produce no
  inferred edge. Matching is discovery over this subset, not general JSON Schema containment
  or runtime value validation. A false result does not forbid an explicit later binding.

Recommendations never override a failed match, and a match never installs or activates anything.

#### Library API and installation

`codegen-kit-textparse` 0.1.0 imports as `codegen_kit_textparse` and exposes only `when`.
It accepts exactly `in N minutes/hours` (positive integer, singular/plural),
`at H[:MM][am/pm]`, and `tomorrow at H[:MM][am/pm]`. Grammar is case insensitive;
bare hours use their literal 24-hour value. A clock without a date rolls to its next
strictly future occurrence in the supplied IANA zone. Tomorrow uses the next local date.
The Python API requires a caller-supplied aware `datetime` `now` and explicit `tz`.
The data-boundary catalog schema represents `now` as `string/date-time`; the Python
function performs no implicit string conversion. `at` is an aware ISO date-time string.
`rest` retains task wording, case and punctuation after removal of the time and an optional
leading reminder trigger, with whitespace normalized. Unsupported languages/text return
`None`; a naive clock raises `ValueError`, invalid zones raise `ZoneInfoNotFoundError`
or `ValueError`. There is no host clock, default zone, network or model call.

Relative minute/hour offsets use elapsed UTC arithmetic. Absolute times round-trip both
folds through zoneinfo and return `None` for nonexistent or ambiguous wall times, including
next-occurrence rollover; no DST correction or fold choice is guessed. Compound durations,
recurrence, weekdays, month dates, word numbers, fuzzy grammar, noon/midnight and part-of-day
inference are refused. All three forms share admission checks before resolution, rollover
or remainder cleanup: a contiguous punctuation joiner run followed by a word cannot
continue a supported token (`at 7pm'ish`, `in 2 hours~ish`, `at 9am...ish`), and the
bounded qualifiers `ish`, `approx`, `roughly`, `or so`, `give or take` and `thereabouts`
outside the consumed span cause refusal. No qualifier removal or substring retry is
performed. Ordinary sentence punctuation followed by whitespace/end and spaced sentence
dashes retain their behavior and are preserved in the remainder. Guards also conservatively
refuse reserved temporal vocabulary in the remaining task text. The runtime closure is
stdlib plus `tzdata` only, with no parser engine
or language registry. Narrow corpus, configuration, whitespace and dependency details are in
the [package README](../packages/codegen-kit-textparse/README.md) and its notices.

The library branch reads the same live catalog source/ref switches, selects the newest
stable release admitting the tg_bot Python interpreter, and fetches its exact independent
tag through the existing source/build pipeline. The service interpreter comes from its venv,
or read-only `uv python find --project services/tg_bot --no-python-downloads`; it must already
be available. The wheel must agree on distribution/version in METADATA and filename, satisfy
catalog and artifact `Requires-Python`, have compatible interpreter/platform tags and the
declared import module, and contain no runtime package entry-point group or `package.yaml`.
Malformed artifacts raise `PackageWheelMismatchError`. No parser is imported by tooling.

Without tg_bot the command refuses before writes, including backend-only products. After
verification it copies the wheel to `services/tg_bot/packages/`, runs `uv add --project
services/tg_bot --no-sync <wheel>`, then `uv sync --project services/tg_bot --frozen`.
The tg_bot Docker dependency stage copies that directory before frozen sync. There are no
backend allowlist changes, deptry entry-point exemptions or contract generation.
`kit add textparse --wheel <path>` uses the bundled catalog (or the Python API's supplied
catalog), verifies an explicitly chosen declared version and follows the same recipe.
uv install failures after preflight do not promise rollback. Package/extension explicit-wheel
behavior and extension parent guards remain unchanged.

Publication is prepared in [textparse 0.1.0 release notes](releases/textparse-0.1.0.md).
Until its tag exists the default remote install refuses as unpublished. CI's generated
backend,tg_bot proof builds the real wheel from a local annotated release-tag fixture,
calls it through the service's own interpreter and builds/runs the tg_bot image. That
network/container proof belongs to the existing slow Copier CI leg, not local broad checks.

#### Extension install precondition

Both catalog-resolved `kit add` and explicit `kit add --wheel` check an extension's parent
before package-source fetch/build, wheel copy, dependency/lock changes, allowlist changes,
synchronization or generation. The catalog itself must first be read to identify the extension.
The explicit artifact form uses the tooling's bundled catalog, as for existing packages;
the Python `add_package` API also accepts an explicit validated catalog.

The parent must be in the backend manifest allowlist and resolve through the existing
`resolve_active_packages` path in the product backend environment. That validates installed
entry points, package roots, manifest identity, core compatibility and manifest/distribution
version agreement. The product's own backend environment is required; the tooling host's
development fallback cannot satisfy this guard. The validated installed manifest version must
satisfy `extends.versions`. A catalog's newest release, a wheel filename, a lock entry or an
allowlist name alone is insufficient. Failure raises `ExtensionPreconditionError` and leaves
product files unchanged. Admission reuses the existing package recipe below; it neither installs
nor upgrades the parent implicitly. No production extension is shipped in this increment.

Products receive the tooling from Git at the kit core ref they were generated from, so a catalog
inside that tooling is frozen at the product's pin. `kit add <name>` therefore reads
`packages/catalog.yaml` live, by default from `https://github.com/vladmesh/codegen-product-kit.git`
at the remote's default branch (`HEAD`). `--catalog-source` or `KIT_CATALOG_SOURCE`, and
`--catalog-ref` or `KIT_CATALOG_REF`, point it at another Git repository or ref, such as a stand
mirror or a local test repository. The packages and versions it can resolve are those of the live
catalog; runtime packages/extensions filter by `requires_core`, libraries by target Python.

`kit add <name>` refuses with a non-zero exit, before any product file changes, when the name is
not in the catalog (`UnknownPackageError`, listing the known names), when no version admits the
product's core (`IncompatibleCatalogVersionError`, naming each version's requirement and the core
version), when the catalog source or ref cannot be fetched (`CatalogSourceUnreachableError`), when
the chosen version's tag does not exist at the source (`PackageNotPublishedError`, "not published
yet"), when the build fails (`PackageBuildError`), and when the built wheel's distribution, version
or entry point differs from the catalog (`PackageWheelMismatchError`). The product's manifest,
pyproject, lock and `services/backend/packages/` are untouched by any of these refusals.

A package release tag is the annotated tag `packages/<name>/v<version>` at the commit whose package
sources declare that version. It is not a PEP 440 version, so it can never be read as a kit core
version: Copier's latest-tag selection and its template version both skip it. Copier may still
record it in `_commit` as a `git describe` string such as `packages/reminders/v0.3.0-2-g1a2b3c4`,
which Git resolves to the exact commit.

Releasing a package version takes two steps:

1. One reviewed pull request bumps the version in the package's `pyproject.toml` and `package.yaml`
   (and `requires_core` when it changes) and appends the matching entry under the package's
   `versions` in `packages/catalog.yaml`. Adding a new package adds its whole catalog entry the same
   way. The catalog-to-source test fails if either side is left behind.
2. After it merges, a PO release operation publishes the annotated tag `packages/<name>/v<version>`
   at that merge commit. Until then `kit add <name>` refuses the version as not published yet.

A kit core tag and a package tag must never be on the same commit. Copier records the template
revision in `.copier-answers.yml` `_commit` with `git describe --tags`, and the orchestrator's
template smoke compares that value with its pinned kit core tag; a package tag on the core's commit
can be the one `git describe` reports. When one pull request carries both a kit core release and a
package release, the core tag goes on the merge commit and the package tag goes on the pull
request's head commit, whose package path is byte-identical to the merge commit's (check with
`git diff --quiet <head> <merge> -- <package path>` before tagging).

Neither step creates a kit core tag or moves the orchestrator's kit pin
(`scheduler.service_template_ref`): every product whose tooling has catalog `kit add` resolves the
new version from the live catalog. Tooling at kit core `0.6.4` and earlier has only the `--wheel`
form; catalog resolution reaches products from the first kit core release that contains it. A published package tag is never moved or replaced; a fix is a new version.

## Core caller identity v1

The backend core establishes the calling user's identity once, at the request boundary, for every
package route that asks for it. This is service-level trust inside the product: the trusted caller
is an in-product service, today the generated tg_bot, which holds a capability and names the user it
acts for from that user's real Telegram update. There is no end-user authentication, such as web
login or per-user tokens; a request without the capability is not trusted because of where it comes
from.

A package route depends on `codegen_kit.caller_identity`, a FastAPI dependency. It is the only
enforcement place: a package adds no identity check of its own, and no global middleware changes
the product's own routes. The dependency, in order:

1. Requires exactly one `X-Identity-Capability` header, non-empty printable ASCII, equal to the
   generated `USER_IDENTITY_CAPABILITY` secret under `compare_digest`. Otherwise **401**.
2. Requires exactly one `X-User-Channel` (at most 64 characters, no `:`) and exactly one
   `X-User-External-Id` (at most 256 characters), each non-empty printable ASCII. Otherwise
   **401**.
3. Resolves `(channel, external_id)` in `user_channels`. An unknown identity is **403**.
4. Requires that identity's user to be `active`. An inactive user is **403**.
5. Returns the canonical `user_ref`, `"<channel>:<external_id>"`, for example `telegram:123456`.

The resolution reads the request's core database session; the users model and its admission
decision are unchanged, so `POST /users/grant` and `POST /users/revoke` decide who may act. The
headers are intentionally absent from generated schemas and OpenAPI, like the other capabilities.
The capability must never be logged, placed in LLM-facing data, or carried in a URL, an event
payload or an error body.

`USER_IDENTITY_CAPABILITY` is a backend `generated_secret` whose consumers are `backend` and, when
the product has one, `tg_bot`. The deployment secret resolver generates and persists it like every
other `generated_secret`; local `.env` files carry `local-identity-capability-not-for-production`.
The generated tg_bot calls package routes through
`BackendClient.request_as_telegram_user(method, path, telegram_id, ...)`, which adds the three
headers with the capability read from its environment and fails closed without it.

## Core settings v1

Every backend generated from this template provides the versioned core settings contract:

- `POST /settings/get` accepts `SettingGet` and returns `SettingValue`.
- `POST /settings/set` accepts `SettingSet` and returns `SettingValue`.

All three generated schemas carry `contract_version: 1`. A value is identified by its declared
`key` and by an explicit `scope`: `product` stores one product-wide value, while `user` requires a
positive local `subject_id`. Values are JSON and never inferred from prose or environment variables.
The database uniqueness boundary is `(key, scope, subject_id)`, so writing the same effective JSON
value is idempotent and user-scoped values cannot overwrite another subject's value.

Only settings declared in an explicit `services/<service>/manifest.yaml` may be written. A v1
manifest has a Draft 2020-12 `settings_schema` object with named `properties` and
`additionalProperties: false`. It is loaded separately from `services/<service>/spec/*.yaml`;
legacy `services/*/spec/manifest.yaml` remains ignored. The supported schema form deliberately has
no top-level `required` or `$ref`: each setting is independently written and no schema source may be
resolved indirectly. Duplicate keys across service manifests are invalid.

An active package may bind one of its own local `settings_schema` keys in `setting_seeds`, currently
only at `product` scope. After the endpoint has schema-validated the value and flushed the core
setting row, it invokes the one activated package callback for the exact normalized key. The
callback receives the validated Python value and the request's existing `AsyncSession`; callbacks
are not discovered again and manifests are not reread per request. User-scoped writes, unrelated
keys, and packages without bindings do nothing beyond the existing setting write. Since ownership
makes the matching callback unique, declaration order has no cross-package ambiguity. Within a
write the order is core upsert, package callback, then the dependency-owned commit. Any callback
exception propagates unchanged and the request dependency rolls back both core and package state.
Package callbacks must make retries idempotent because every successful replay of the declared
setting invokes the callback again.

`POST /settings/set` requires exactly one `X-Settings-Capability` value matching the generated
`SETTINGS_WRITE_CAPABILITY` secret. The header is intentionally absent from generated schemas and
OpenAPI. It must not be logged, included in LLM-facing data, or used as a product setting. Reads do
not carry this deployment capability.

Environment variables remain startup, connectivity, platform, and secret configuration. A value
derived from the Product Brief or intended for a user to change belongs in a manifest-declared
setting instead.

Core-owned settings are declared by the kit, not by a manifest. Façade 2.5 owns one:
`language`, `{type: string, enum: [ru, en]}`, read and written at `product` scope. The spec loader
registers it first with source `core`, so a fresh product's generated `SETTINGS_SCHEMAS` carries
it before any module install, and the ordinary `/settings/set` and `/settings/get` contract writes
and reads its value. There is no default value, no seeding and no environment fallback. A service
manifest or package that declares the same key, even with an equal schema, is a competing owner
and fails generation with its source; bindings reference the core key. Generation also emits
`SETTINGS_SCHEMA_SCOPES` (`{"language": "product"}`) from the same core metadata, and the
generated settings router (`services/backend/src/generated/routers/settings.py`) refuses a core
setting in any other scope with 422 on both get and set, before the product-owned controller or
the repository is reached; other settings keep both scopes. The check lives in generated code
because Copier keeps a product's controllers on update, so an upgraded product enforces it too.

## Core host contract v1

`framework/host_contract.py` is the single authority for the two hard host invariants of façade
2.5: core setting ownership and Telegram command registration. Generation
(`make generate-from-spec`), `kit add` and `kit bind` admission, the product's `make lint` and the
read-only `kit check-install` evaluate the same contract. The order is: collect typed declarations
with their source files and lines, resolve core ownership and exclusive command claims, report
every violation, and only then write generated artifacts. The generated bot registry renders the
same validated claims, so lint, preflight and runtime registration cannot disagree.

### Command registry

`services/tg_bot/src/generated/commands.py` is the only place the bot registers handlers. Its
`register(application, access=..., builtins=..., bindings=..., client_factory=...)` adds the
access check in group -1, then in group 0 and in this order: core `/start` (and `/command` in
backend shapes), product commands, bound module commands in binding-file order, and last the
core unknown-input reply. Module bindings are admitted through a guard that accepts exactly the
registry's next module command or the `b1:` callback handler and refuses anything else. At
startup the registry refuses product declarations that differ from the generated registry.
`start` and `command` are reserved in every shape. Names match `[a-z][a-z0-9_]*` with at most
32 characters. A name claimed by two owners fails with the command and both source locations.

The admitted product contribution form is one module-level tuple in
`services/tg_bot/src/commands.py` (product-owned, never overwritten by Copier):

```python
from services.tg_bot.src.generated.commands import ProductCommand

async def handle_ping(update, context) -> None: ...

COMMANDS: tuple[ProductCommand, ...] = (ProductCommand("ping", handle_ping),)
```

Each entry is `ProductCommand("<literal name>", <function name>)`, or the same two arguments
named (`name=`, `handler=`), exactly the shapes the runtime constructor accepts. The lint reads
the file as data and fails closed on any other form before anything is written: a non-literal
name, unpacked (`*`/`**`) or extra arguments, an argument given twice, a non-tuple value,
reassignment or augmentation. Product tg_bot code outside `src/generated/`, `tests/`, `packages/`
and environments may not mention `BaseHandler`, `CommandHandler`, `ConversationHandler`,
`MessageHandler`, `PrefixHandler`, `StringCommandHandler`, `StringRegexHandler`, `TypeHandler`,
`add_handler`, `add_handlers` or `remove_handler` as a name, attribute, import or string, nor
reach the registry itself as `<application>.handlers` (or `getattr(<application>, "handlers")`),
where the chain names `application` or `app`, as `context.application` does; these would register,
replace or remove a command, the access check or the unknown-input reply. Other attributes
called `handlers`, `callback` or `clear` in product logic are not restricted. At runtime the
registry is final: after registration every handler group is a fixed tuple and every registered
handler is sealed, so adding, removing or clearing handlers, or replacing a handler's callback,
raises (`CommandRegistryError`, or `AttributeError` from PTB's list operations) inside the
offending handler and the core access, commands and unknown-input reply keep working. This
protects the registry; it is not a Python sandbox. Product callbacks and other update types are not
contributable in this version. Other product logic is not linted by this contract.

Unknown commands and text reach the core reply, which lists the registry's commands in the core
`language`: `I don't understand this message. Available commands: /start, …` or
`Не понимаю это сообщение. Доступные команды: /start, …`. When the language is unset or the
settings read fails, both lines are sent; a backend-less bot always sends both.
`commands.language(client_factory)` gives product handlers the same read.

`make lint` runs `python -m framework.host_contract` in every shape and fails on any violation or
when the committed registry differs from its render (`registry_stale`). Backend shapes regenerate
it with `make generate-from-spec`; a backend-less bot's `make generate-from-spec` runs
`python -m framework.host_contract --write`. There is no warning-only mode.

### Read-only install preflight

```bash
kit check-install tg-channels --json --package-source /path/to/codegen-kit-tg-channels
kit check-install tg-channels --json --catalog-source https://github.com/vladmesh/codegen-product-kit.git \
  --catalog-ref <commit-or-tag> [--version 0.1.2]
```

The typed Python API is `framework.preflight.check_install(root, name, *, package_source=None,
catalog_source=None, catalog_ref=None, version=None)`, returning `CheckInstallResult`. It reads
the product's files and the exact package version's `package.yaml` and default binding as data:
from a package source directory, or from the tag a catalog at an explicit source and ref selects
for the product's façade (or `--version`). There is no live catalog default and no environment
variable fallback. It never imports package runtime, never builds or installs, and never writes
to the product, its environments, locks, settings or database. Evaluation order is the same for
check-install, `kit add` and `kit bind`: the product's service environments are validated with
the read-only ownership/provenance check normal bind uses (the virtualenv must live inside the
product and its interpreter must answer an isolated, bytecode-free query; a host venv, a
placeholder or an unusable interpreter is refused; check-install and `kit bind` also refuse a
missing one, while `kit add`, which prepares a missing service environment itself, validates
those that exist), then the effective binding is chosen (a
retained `services/tg_bot/bindings/<package>.yaml` wins over the package default) and validated
against the exact package manifest, then core language and timezone references (through
`binding_settings`) and command claims are resolved. No environment is created or synced.

Result version 1, printed with sorted keys:

| Field | Meaning |
|---|---|
| `result_version` | `1` |
| `package` | requested package name |
| `status` | `mechanical`, `glue` or `incompatible` |
| `product_core` | the product façade `CORE_VERSION`, when read |
| `target` | `route` (`package_source`, `catalog`), `path` or `catalog_source`/`catalog_ref`/`tag`, `version`, `requires_core`, `metadata_sha256` |
| `glue` | sorted list; empty unless `status` is `glue` |
| `incompatible` | `{code, explanation}` when `status` is `incompatible`, else `null` |

Each glue item has `code`, `path`, `line` (or `null`), `owner`, `symbol`, `key`, `command`,
`conflict`, `action` and `other` (`{owner, path, line, symbol}` of the other claimant or `null`).
Codes are `core_setting_redeclared`, `command_collision`, `reserved_command`, `invalid_command`,
`unsupported_product_command`, `registration_bypass` and `library_required`, plus
`binding_language_owner` and `binding_setting_conflict` when the offending binding is a retained
product file (owner `product`, with that file and line). Command glue is
anchored on the product-editable side. Prospective module paths are the product binding path
`services/tg_bot/bindings/<package>.yaml` the default bind would write. Incompatible codes are
`provenance_required`, `catalog_unavailable`, `unknown_component`, `unsupported_component`,
`artifact_unavailable`, `package_metadata_invalid`, `package_mismatch`, `product_shape`,
`environment_missing`, `environment_invalid`, `manifest_invalid`, `core_unsupported`,
`core_range`, `binding_invalid`, `binding_language_owner` and `binding_setting_conflict` (the
last two when a package default breaks the core references). A malformed or unreadable service
manifest (`- language`, invalid YAML, a missing `settings_schema`) is `manifest_invalid`;
generation and lint fail on it too instead of skipping it. A prospective conflict or an expected
input failure is a result, never a traceback; programming errors stay visible.

`mechanical` means the version, source and core range were verified and the prospective product
satisfies the contract as is. Exit status is 0 for `mechanical`, 3 for `glue`, 4 for
`incompatible` and 2 for a usage error; consumers should read `status`. Example glue item:

```json
{"action": "rename the product command /channel at services/tg_bot/src/commands.py:16 (its ProductCommand entry) to an unused name and run make generate-from-spec",
 "code": "command_collision", "command": "channel",
 "conflict": "/channel is claimed by both package:tg-channels and product",
 "key": null, "line": 16, "owner": "product",
 "other": {"line": 6, "owner": "package:tg-channels", "path": "services/tg_bot/bindings/tg-channels.yaml", "symbol": "text_create"},
 "path": "services/tg_bot/src/commands.py", "symbol": "handle_channel"}
```

`kit add` (wheel or catalog tag) and `kit bind` run the same contract evaluation before any
product write and refuse with `HostContractError`, leaving owned files and locks unchanged for
these conflicts. This is not a general install recovery mechanism.

Façade 2.5.0 is a minor version: the host surface and `ProductCommand` API are additive, the
published tg-channels 0.1.2 (`>=2.4,<3`, binding key `language`) and reminders 0.5.0
(`>=2.2,<3`, binding v1 without language) admit it unchanged, and package protocol stays 1.
Products generated earlier keep their pinned tooling; there is no automatic upgrade.

## Environment contract v1

`framework/contracts/env_contract.py` is the self-contained, exportable contract model.
`EnvContractFragment.model_json_schema()` produces the committed
`tests/fixtures/env-contract.schema.json`. `ENV_CONTRACT_VERSION` remains `"1"`: the new sources
are additive alternatives in the existing discriminated union. Consumers must support a source
before resolving it; an unknown source fails closed.

A package can declare a platform grant and endpoint entirely in its own `package.yaml`:

```yaml
environment:
  - name: PLATFORM_KEY
    required: true
    source:
      kind: platform_key
      service: example-service
      scopes: ["example-service:read"]
      quota: {requests_per_day: 100}
  - name: PLATFORM_BASE_URL
    required: true
    source:
      kind: platform_base_url
      service: example-service
      url: https://platform.example.test/example-service
```

Generation writes `services/backend/packages/env.contract.yaml` with `source: platform_key`
or `source: platform_base_url` and the exact declared data fields. Both entries carry
`environments: [local, production]`, `consumers: [backend]`, the merged requiredness and a package
description. Keys are always sensitive; endpoints are always non-secret. The key form requests
orchestrator issuance of a product Bearer credential for the declared grant, rather than user
input. No credential value, key prefix, concrete service registry, endpoint or grant default
is built into core. Resolution and issuance belong to the orchestrator.

`service` is a string matching `^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`, not a known-service enum.
`scopes` is an explicit list of nonempty strings, and `quota` is an explicit mapping of nonempty
names to non-negative integers; booleans, strings and fractional values are refused. Empty lists
and maps declare an empty grant rather than an inferred grant. `url` must be an absolute HTTPS
URL without credentials. HTTPS is also required in local and test declarations; fixtures use
reserved test hosts and do not require an HTTP exception. Unknown fields or source kinds fail.

An absent or null package `source` preserves existing behavior: reuse a compatible product entry,
or emit `user_secret` if none exists. Unspecified requirements may share a variable with an
explicit platform declaration. Explicit sources for the same variable must match exactly across
packages, including service, scopes, quota or URL; requiredness is combined with logical OR.
An existing product entry must cover local and production, include backend as a consumer, and
satisfy requiredness. For an explicit platform requirement it must also match every source data
field. A differently classified or differently granted product variable is refused rather than
overwritten. Compatible product entries are copied unchanged so canonical merging still sees
identical declarations. Output is deterministic and conflicts fail before the packages fragment
is written.

Required application environment values still have no runtime defaults. Package protocol stays
`1`; the new source block requires façade `CORE_VERSION = "2.3.0"` and a corresponding package
`requires_core` lower bound. Existing packages without the block need no new release.

## Durable product events v1

Every generated product event is appended to a Redis Stream. The stream name is the event name from
`shared/spec/events.yaml`, such as `job_fired`; generated code has no Redis channel pub/sub path for
product events. Each entry contains a generated envelope with `event_id` (UUID), timezone-aware
`occurred_at`, integer `schema_version` (currently `1`), and the declared message under `payload`.
Publishers create this metadata, while consumers deserialize the whole typed envelope.

Generated backend adapter subscriber groups are named `events:<service>`. Each consuming service gets its own stable
group, preserving fan-out: replicas of one service compete within that service's group, while a
different service receives the same stream entry through its own group. Consumer names include the
role, hostname and process id. Those adapters let FastStream create a group at `$` on its first start, so that first
start establishes the current stream tail and consumes only later entries. A deployment must start
and ready a new consuming service before allowing any event it must receive to be published; events
published before the group's first creation are deliberately not replayed. Once the group exists,
later downtime does not lose its backlog.

A consuming package owns establishment of every fixed consumer group declared by its runtime. Its
startup creates the stream and group with `MKSTREAM` semantics before any live or recovery reader
starts. Repeated startup accepts the existing group without resetting its cursor or pending entries;
only Redis's `BUSYGROUP` already-exists response is accepted, and every other Redis failure aborts
package startup.

Every generated subscription has a live reader and a recovery reader in the same group. The recovery
reader uses FastStream's Redis `XAUTOCLAIM` support, with a configurable five-minute idle threshold
and five-second polling interval by default. Reclaim is based only on idle time: Redis cannot
distinguish a dead owner from a live handler that has run longer than the threshold. The transactional
idempotency guard, not the reclaim window, therefore guarantees that live and reclaimed deliveries
cannot both execute the effect.

There is one recovery reader, and therefore one bounded `XAUTOCLAIM` poll, per declared event per
consuming service. Empty recovery polls occur no more often than the configured interval. Combining
those polls would be performance hardening only: the current reader cannot lose an event, and a
concurrent or reclaimed duplicate is rejected by the database claim before its effect. That
optimization is outside the current release-safety sprint.

The backend core provides `consume_once(session, consumer_group, event_id, effect)`. It records the
group and event UUID in the core-owned `event_consumptions` table before running the effect. The
marker and effect share the caller's database transaction, so rollback makes a failed delivery
eligible to run again, while a committed redelivery skips the effect. This exactly-once boundary
therefore applies to effects made atomically through that session; external side effects require
their own idempotency key. Generated adapters require the session factory and `consume_once` helper:
both their live and recovery readers enter the same guard before calling the controller, commit the
guard and effect together, and only then publish a success event. A duplicate that loses the guard
claim is acknowledged without running the controller or publishing success. An unguarded consumer is
not a generated default and requires a separate, explicit implementation outside this adapter.

## Core jobs v1

Every backend generated from this template provides the versioned core jobs contract:

- `POST /jobs/fire` accepts `JobFire` and returns `JobCommand`.
- `POST /jobs/evidence` accepts `JobCommandRef` and returns `JobCommand`.

All four generated schemas — including the `JobFired` event message — carry
`contract_version: 1`. A caller fires a *named* behaviour: it never names a module, a queue, a
container or a transport. The core validates the name and the arguments, records the command, and
emits `job_fired`; whichever optional core-module declared that it provides `jobs.fire` subscribes
to that event and does the work. The core's only schedule is the package-declared timers of the
[core timer loop](#core-timer-loop), which run inside the backend process. A product with no timer
and no provider therefore gains no container, no worker and no loop.

### Declared, never inferred

A behaviour is fireable only because the product declared it, in the same explicit
`services/<service>/manifest.yaml` that declares settings. `jobs_schema` is a Draft 2020-12 object
whose named `properties` are the fireable names, each mapped to the schema its `arguments` object
must satisfy; both the declaration and every behaviour's arguments use `additionalProperties:
false`. The field is additive: a `version: 1` manifest that declares no jobs stays valid, and the
manifest still refuses anything it does not declare. The template ships `properties: {}`, exactly as
it ships no settings. An undeclared name is refused with `404 Job name not declared`, the way an
undeclared setting key is; arguments that fail their declared schema are refused with `422`.
Duplicate job names across service manifests are invalid.

`provides` names the core capabilities a service provides — `jobs.fire` for an optional scheduler
module. It reuses the existing service, profile and manifest mechanism; it is a declaration, not a
catalogue, and the core never resolves a provider from it.

### Identity, provenance and replay

A fire carries a caller-supplied `command_id` and the `fired_by_product` / `fired_by_run`
provenance of whoever fired it. Identity is the tuple `(fired_by_product, command_id)` and storage
uniqueness on that tuple is what bounds execution: a retry or a replay of the same identity returns
the recorded evidence instead of emitting a second time, and a concurrent fire that loses the unique
constraint returns the recorded command rather than creating another. Nothing a caller does with
retries produces an unbounded number of executions.

`dispatch_status` is `dispatched` once `job_fired` has been emitted, together with `dispatched_at`;
that state is terminal, and a later fire of the same identity never emits again. A command whose
event could not be delivered is recorded as `undelivered`, so a retry of that identity re-attempts
delivery without recording a second command. Evidence is what central QA asserts on: it survives the
retry and is readable afterwards through `POST /jobs/evidence`, which returns a command only within
the product that fired it. One product's command is neither visible nor fireable as another's.

### Committed before emitted, emitted once

A `job_fired` exists only for a command whose row is already committed. The core records the
command, commits it, and only then emits: a failure between the two leaves a recorded command that a
retry completes, never a behaviour that ran with nothing recording it.

The emission itself happens in one place, behind the committed row's lock — `SELECT ... FOR UPDATE`
on the product's PostgreSQL. A concurrent retry of the same identity waits there, then reads the row
as the winner left it and returns that evidence instead of emitting beside it, so one identity emits
at most once however many callers fire it and however they interleave. The lock is released by the
commit that records the terminal evidence, which puts the remaining hazard on the safe side: a crash
between a delivered event and that commit leaves the command `undelivered` and a later retry emits a
second time, while the opposite direction is ruled out by the ordering — a command marked
`dispatched` always had its `job_fired` published, because the emission precedes the transition and
is never inverted.

That is a statement about emission, and no more. `job_fired` is appended to its Redis Stream, so
`dispatched` is evidence that the core durably emitted the event, not evidence that a provider has
already consumed it, run the behaviour or completed it. An established provider consumer group can
resume from its backlog after downtime.
Whether the behaviour actually happened is asserted by central QA against the behaviour's own
output, never inferred from dispatch evidence.

### Core timer loop

Core `2.1.0` owns one timer loop. It is the only schedule in the core and it fires only what an
installed package declared; there is no platform cron, no per-product interval setting and no cron
expression.

**Protocol.** A package declares timers in `package.yaml`:

```yaml
timers:
  - job: tick          # a local jobs_schema job; fired as <package>.tick
    every_seconds: 60  # 10 <= every_seconds <= 86400
```

The manifest model (`framework.spec.packages.PackageManifest`) is the single validation place. It
refuses, with `InvalidPackageTimerError`, a timer naming a job its `jobs_schema` does not declare, a
job named by two timers, an interval outside 10 seconds to one day, and a job whose arguments schema
is anything but an object whose only property is a required `at` of `{type: string, format:
date-time}`. A non-integer interval or an unknown timer field is refused by the same model. A
package without `timers` is unchanged. Generation records the active packages' timers as
`JOB_TIMERS: dict[str, int]` (normalized job name to period) in
`services/backend/src/generated/jobs_schemas.py`, next to `JOB_SCHEMAS`; the runtime reads that
generated map and never package metadata. Runtime activation only admits the field, and the
generated manifest digest already refuses a manifest changed since generation.

**Loop.** The backend lifespan starts exactly one loop after every package `startup` and stops it
before any package `shutdown`, and only when `JOB_TIMERS` is non-empty; a product with no package,
or with packages but no timers, starts none. Each timer has a fixed grid of slots, the multiples of
its period since the Unix epoch. On start and then at every slot boundary the loop fires each timer
whose current slot it has not attempted, calling `JobsController.fire` in its own database session:
the same argument validation, `job_commands` record, commit and `_emit_once` as `POST /jobs/fire`.
The fire is:

| Field | Value |
|---|---|
| `name` | the timer job, such as `reminders.tick` |
| `arguments` | `{"at": "<slot instant, UTC, e.g. 2026-10-02T19:43:00Z>"}` |
| `command_id` | `core-timer:<job>:<slot instant>`, deterministic per job and slot |
| `fired_by_product` | the backend's `APP_NAME` |
| `fired_by_run` | `core-timer`, so evidence shows the core timer fired it |

**Dedup.** Because the identity is deterministic, a restart inside the same slot, or a second backend
process (a replica or an overlapping deploy) firing the same slot, finds the recorded
`(fired_by_product, command_id)` row and emits nothing new, exactly like a replayed `POST /jobs/fire`.
Replicas must share `APP_NAME` for this; they already share the database.

**Failures.** A fire that raises, for example on a database error, is logged as
`core_timer_fire_failed` and the loop waits for the next slot; a fire whose `job_fired` could not be
delivered is recorded `undelivered` and logged as `core_timer_fire_undelivered`. Neither stops the
loop or the backend. A slot missed while the process was down, or one whose fire failed, is not
replayed one by one: the next slot's later `at` covers it, because a timer job evaluates everything
up to `at` (the reminders tick selects `remind_at <= at`). A timer job must therefore be written so
that a later `at` subsumes an earlier one.

**Growth.** Every slot that fires records one `job_commands` row, which the core never prunes:
`86400 / every_seconds` rows per timer per day. Reminders `0.4.0` (60 seconds) adds 1,440 rows a day,
about 525,600 a year; the 10-second minimum would add 8,640 a day.

### The capability

`POST /jobs/fire` requires exactly one `X-Jobs-Capability` value matching the generated
`JOBS_FIRE_CAPABILITY` secret, compared with `compare_digest`. The header is intentionally absent
from the generated schemas and from OpenAPI. It must never be logged, placed in LLM-facing data, or
carried in a URL, an event payload or an error body. Reading evidence back does not carry it.


### Product Telegram bindings

Install into a generated `backend,tg_bot` product using its pinned candidate tooling:

```bash
kit add reminders --product-root /path/to/product
kit add textparse --product-root /path/to/product
kit bind reminders --default --product-root /path/to/product
# Explicit override, including replacement of an existing product binding:
kit bind reminders --file /path/to/custom-reminders.yaml --product-root /path/to/product
# Subsequent product edits:
cd /path/to/product
make generate-from-spec
```

`framework.binding_product.validate_product_bindings` is the shared whole-product boundary
for bind and regeneration. The backend and bot must have their own installed virtualenvs.
Isolated interpreter queries read only stdlib metadata; a host interpreter/site directory,
a catalog claim or an allowlist alone cannot establish installation. Backend resolution
checks the actual package entry point, manifest identity/version, tooling and product-facade core compatibility and
resources without loading its runtime. The default must resolve inside that installed entry
point module. Bot library admission combines the tooling catalog's function schema with
installed distribution/version, RECORD module evidence and the finite export's static
signature. No library or package runtime code is imported/executed by the tooling.

The complete binding set is validated before any generated output changes. Unknown mappings,
missing actions/events/recipients/libraries, incompatible settings, repeated package bindings,
commands/events and callback button labels fail explicitly. Command names are claimed in the
core registry: `start` and `command` are reserved, commands have Telegram's 32-character limit
and a collision with a product or another module command names both sources. No old package obtains actions from newer catalog
metadata. A repeated byte-identical default is idempotent. A differing existing file is retained
and refused with `BindingOwnedFileError`; use `--file` for an explicit replacement. Generation
owns `services/tg_bot/src/generated/bindings.py` and `binding_relay.py`; product bindings and
service manifests stay product-owned. Removing every binding emits an inert seed and removes
the generated relay companion. Backend-less bots retain the inert seed and fail-closed admission,
without a parser import, relay startup or additional Redis environment requirement.

Bind adds a missing timezone declaration to `services/tg_bot/manifest.yaml` through the current
service settings registry: `{type: string, format: x-iana-tz}` at the binding's key. A matching
existing product-owned declaration is reused; unrelated declarations survive. Package ownership,
duplicate owners or a different schema fail. This declares a requirement, never a value.
Before scheduling, an operator separately writes the explicit product value through the existing
backend contract, using the privately held `SETTINGS_WRITE_CAPABILITY`:

```bash
curl --fail-with-body -X POST "$BACKEND_API_URL/settings/set" \
  -H "Content-Type: application/json" \
  -H "X-Settings-Capability: $SETTINGS_WRITE_CAPABILITY" \
  -d '{"contract_version":1,"key":"timezone","scope":"product","value":"America/New_York"}'
```

Handlers read `POST /settings/get` with `contract_version: 1`, the declared key and
`scope: product`, without a subject id. Missing (404), unavailable, malformed or non-IANA
values give an explicit setup reply before create. No environment/host/UTC default or user
setting participates. `month_word` uses an explicit English month table and the product zone.

The generated bindings module's `register(application, BackendClient)` is called by the core
command registry, which admits exactly its registry commands and the callback handler after
the access check and before the core unknown-input reply. `start`/`stop` run from the existing
`post_init`/`post_shutdown` and own the relay's
subscriber broker/client; startup failure closes those resources and the existing publisher.
The generated module receives the client factory, avoiding an import of bot main. The real
parser is called only in generated product bot code with the original text, `lang=en`, an
aware clock and the product zone. Empty original or parsed rest gives `on_invalid_text` and
never create. Only None offers the three declared presets, retaining original text. Relative
presets use elapsed UTC arithmetic; tomorrow means product-local 09:00 on the next date, with
round-trip zone validation rejecting gaps/folds. Parser refusal policy is preserved.

Action methods, prefix/path, input/output schemas, mappings and replies come from the validated
binding/installed manifest. The default uses POST `/reminders` with only text/remind_at,
GET `/reminders`, and DELETE `/reminders/{reminder_id}`. All use
`request_as_telegram_user` with the real command/callback update's user id. BackendClient sends
its capability and canonical Telegram identity headers; body/query/callback identities are never
trusted. Generated mutations use one attempt: an uncertain mutating HTTP result cannot safely be retried
without a package action idempotency key. HTTP/configuration/schema failures give a bounded
reply that advises checking access/configuration and listing items before another attempt;
capabilities and backend exception bodies are never echoed.

Selections use `b1:<24-character-random-token>:<index>`, below 64 bytes, with no task or identity
in callback data. The process-local store retains at most 1,024 contexts for 600 seconds, with
at most 65,536 encoded context bytes and 100 choices each; original tasks/replies are capped at
4,000 characters; replies stay within 4,000 UTF-16 code units. List contexts retain only fields needed by the button's validated arguments.
Selections are bound to initiating user/chat and the declared binding/action, consumed before
any await, and cannot be reused, transferred or forged. Expired, evicted, restarted or previously
used selections explicitly require running the command again. An uncertain failed request does
not reopen its selection. These tokens provide no durable cross-restart callback workflow.

The relay consumes the actual declared stream name, `reminders.due` for the released default,
in stable group `events:tg_bot`. Live and XAUTOCLAIM recovery readers have process/instance-unique
consumer names. It creates each stream/group at `0-0` with MKSTREAM before readers start, accepting
only BUSYGROUP on repeat, preserving existing cursors and replaying due events published before
first startup. This deliberately differs from the generic backend adapter's first-start tail.
Transport is the existing EventEnvelope and BinaryMessageFormatV1. Envelope version/UUID/aware
time, the actual payload schema and declared recipient are validated; only canonical positive
`telegram:<id>` recipients are routed to `send_message`, with the reply rendered from binding data.

Redis SET NX EX establishes an atomic 120-second claim on `bindings:events:tg_bot:<event_id>`
shared across replicas/restarts. Delivery has a 45-second timeout. Token-checked Lua completion
replaces the owned claim with `done` for seven days (604,800 seconds); completed duplicates ACK
without sending, including concurrent stream entries. A claim held by another delivery leaves
that entry pending. A transient failure releases only its owned claim and leaves the stream
entry pending for XAUTOCLAIM (five-second idle threshold, one-second polling). A crash before
completion permits retry after lease expiry. Forbidden/deactivated/missing chats complete a
terminal marker and ACK; invalid envelopes/recipients ACK immediately with a fixed bounded
warning, without retry or payload logging. Other errors retry. Owned subscribers and Redis
connections close on startup failure/shutdown.

This is bounded dedupe over retryable stream delivery, not exactly-once Telegram delivery.
A crash or an ambiguous Telegram timeout after Telegram accepts a send but before the completed
marker can duplicate it. A process suspended beyond its lease may also lose exclusivity;
Redis persistence/availability and untrimmed backlog are prerequisites. Completed-marker expiry
allows a very late duplicate to send again. There is no bot database, second outbox or external
API idempotency promise.

The existing `published_remote` slow Copier lane installs the actual reminders 0.5.0 and textparse
0.1.0 independent tags through default catalog/remote paths with exact candidate tooling, binds
and regenerates without handwritten handlers, and runs controlled-clock fake-backend handler
scenarios. The same lane runs a real Redis stream with a fake Telegram sender, covering pre-start
publication, normal/duplicate ids, concurrent readers, restart, transient retry, terminal refusal,
invalid envelopes/recipients and abandoned-claim expiry. Uploaded
`bindings-remote-redis-smoke-<run-id>` records candidate, immutable tag targets/trees, installed
resource/version/activation, generated hash and execution evidence. Local metadata/transport
fixtures do not establish that remote/real-Redis result. A separate CI-only released core 2.1
upgrade proof now requires `core-21-upgrade-smoke-<run-id>`, covering the genuine old baseline,
native conflict-free update to the exact candidate, protected-file hashes, updated answers/lock/
installed tooling, setup/generation/typecheck/unit tests and subsequent actual published-component
install/bind/scenarios. The existing Redis matrix is retained. Artifact success must be established
on the candidate; local broad checks do not attest it. Final core publication, orchestrator
installation and live stand acceptance remain separate boundaries.

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
without them is unchanged. The unchanged wheel can therefore
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
| `environment` | Named environment requirements and whether each is required | Enforced in the generated package environment-contract fragment |
| `resources` | Named distribution resource paths | Enforced as existing, non-traversing distribution resources |

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
```

The loader refuses, with a named `CatalogError` subclass, an unknown `format_version`
(`UnsupportedCatalogFormatError`), a missing or malformed field or a tag other than
`packages/<name>/v<version>` (`InvalidCatalogEntryError`), a repeated package name
(`DuplicateCatalogPackageError`), a repeated version (`DuplicateCatalogVersionError`), and a
version that is not canonical PEP 440 (`InvalidCatalogVersionError`). Component names are unique
across all three lists; distribution names are unique after Python distribution normalization
(`DuplicateCatalogComponentError`). Function/action names and recommendation targets are unique
within their owner. All version ranges use `packaging.specifiers.SpecifierSet`; malformed core,
Python or parent ranges raise `InvalidCatalogEntryError`. A kit test ties the newest
catalog version of every package to its `pyproject.toml` and `package.yaml` at HEAD: distribution,
name, version, `requires_core`, settings names and environment names must agree.

#### Additive component metadata in v1

The format remains v1 because released products pin tooling while reading the catalog from the
default branch. Kit 0.7.1's reader and existing orchestrator readers see only `packages`, ignore
the additive fields, and retain the old fields and version selection. Core 2.0 still selects
reminders 0.3.0; core 2.1 still selects reminders 0.4.0. An offline regression executes the
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
  These are catalog signatures, not runtime manifest fields or endpoints inferred from OpenAPI.
- `recommended_with`: a list of `{library, why}` records naming catalog libraries. This is
  curated usefulness, independent of computed type compatibility. It triggers no installation.
- `default_binding`: `python.module:relative/resource/path` identifying a resource shipped by
  that component. It is an author-provided starting point for a later product-owned binding;
  the catalog neither installs nor executes it. The loader validates its syntax, not resource
  existence. Release-source verification must establish that it actually ships.

Missing optional fields mean no declared action, recommendation or binding. Tooling must not
infer them for older packages. Catalog interface metadata does not certify an older selected
release; consumers must verify the selected installed manifest before generating bindings.
Runtime manifest actions, their validation and binding generation belong to subsequent work.

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
tag remains a post-merge release operation. Reminders recommends textparse, without adding
actions or a default binding to its existing releases.

The reminders actions/default binding below remain future contract data. The textparse
signature matches its actual source, without announcing a published tag. The complete reminders create/list/cancel,
English-only textparse `when`, version records and test-only extension appear in
[`tests/fixtures/catalog/components.yaml`](../tests/fixtures/catalog/components.yaml).
The repository catalog retains empty extensions and no reminders actions/default binding.

```yaml
# Future optional fields under the reminders package record:
actions:
  - name: create
    input:
      type: object
      properties:
        text: {type: string}
        remind_at: {type: string, format: date-time}
      required: [text, remind_at]
      additionalProperties: false
    output:
      type: object
      properties: {id: {type: string, format: uuid}}
      required: [id]
recommended_with:
  - {library: textparse, why: Parses an English time phrase for remind_at.}
default_binding: codegen_kit_reminders:bindings/default.yaml

# Actual source entry under libraries (tag publication follows merge):
name: textparse
distribution: codegen-kit-textparse
path: packages/codegen-kit-textparse
module: codegen_kit_textparse
summary: English time phrase to an instant and remaining text, or no result.
functions:
  - name: when
    input:
      type: object
      properties:
        text: {type: string}
        lang: {type: string, const: en}
        now: {type: string, format: date-time}
        tz: {type: string, format: x-iana-tz}
      required: [text, lang, now, tz]
      additionalProperties: false
    output:
      type: [object, 'null']
      properties:
        at: {type: string, format: date-time}
        rest: {type: string}
      required: [at, rest]
      additionalProperties: false
    value: at
versions:
  - {version: 0.1.0, tag: packages/textparse/v0.1.0, requires_python: '>=3.11'}
```

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
  A later binding handles that branch. Null inside a primary value or parameter remains a union.
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
inference are refused. Guards also conservatively refuse reserved temporal vocabulary in
the remaining task text. The runtime closure is stdlib plus `tzdata` only, with no parser engine
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

## Durable product events v1

Every generated product event is appended to a Redis Stream. The stream name is the event name from
`shared/spec/events.yaml`, such as `job_fired`; generated code has no Redis channel pub/sub path for
product events. Each entry contains a generated envelope with `event_id` (UUID), timezone-aware
`occurred_at`, integer `schema_version` (currently `1`), and the declared message under `payload`.
Publishers create this metadata, while consumers deserialize the whole typed envelope.

Generated subscriber groups are named `events:<service>`. Each consuming service gets its own stable
group, preserving fan-out: replicas of one service compete within that service's group, while a
different service receives the same stream entry through its own group. Consumer names include the
role, hostname and process id. FastStream creates a group at `$` on its first start, so that first
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

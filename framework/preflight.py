"""Read-only install preflight: ``kit check-install`` and the admission ``kit add`` reuses.

The preflight evaluates the actual product and the exact package metadata (``package.yaml``
and its default binding) read as data from an explicit source: a package source directory,
a catalog source and ref, or a wheel being installed. It never imports package runtime and
never writes to the product, its environments, locks, settings or database.

The versioned result is one of:

* ``mechanical``: the target version, source and core compatibility are verified and the
  prospective product satisfies the core host contract as is;
* ``glue``: a nonempty, deterministic list of product-side conflicts, each with its file,
  line, owner, symbol/key/command, the conflict and a concrete action;
* ``incompatible``: one stable reason code and explanation the product cannot resolve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Literal
import zipfile

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
import yaml

from framework import host_contract
from framework.binding_product import (
    binding_files,
    binding_sources,
    library_evidence,
    product_core_version,
    require_binding_product,
    require_service_environment,
)
from framework.bindings import BindingError, ParsedCreate, load_binding, validate_binding
from framework.bindings_v2 import BindingV2
from framework.catalog import (
    Catalog,
    CatalogError,
    CatalogLibrary,
    IncompatibleCatalogVersionError,
    UnknownPackageError,
    bundled_catalog,
)
from framework.spec.packages import PackageManifest, PackageManifestError, parse_package_manifest

if TYPE_CHECKING:
    from framework.bindings import Binding

RESULT_VERSION = 1
Status = Literal["mechanical", "glue", "incompatible"]
EXIT_CODES: dict[str, int] = {"mechanical": 0, "glue": 3, "incompatible": 4}
#: Stable incompatibility reason codes.
INCOMPATIBLE_CODES = (
    "provenance_required",
    "catalog_unavailable",
    "unknown_component",
    "unsupported_component",
    "artifact_unavailable",
    "package_metadata_invalid",
    "package_mismatch",
    "product_shape",
    "environment_missing",
    "environment_invalid",
    "manifest_invalid",
    "binding_setting_conflict",
    "core_unsupported",
    "core_range",
    "binding_invalid",
    "binding_language_owner",
)


class PreflightIncompatibleError(ValueError):
    """The prospective install cannot be completed by product-side glue."""

    def __init__(self, code: str, explanation: str) -> None:
        self.code = code
        self.explanation = explanation
        super().__init__(f"{code}: {explanation}")


@dataclass(frozen=True)
class PackageMetadata:
    """One exact package version's install-relevant metadata, read as data."""

    manifest: PackageManifest
    binding: Binding | BindingV2 | None
    binding_text: str | None
    provenance: dict[str, Any]


@dataclass
class CheckInstallResult:
    package: str
    status: Status
    product_core: str | None = None
    target: dict[str, Any] | None = None
    glue: list[dict[str, Any]] = field(default_factory=list)
    incompatible: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "result_version": RESULT_VERSION,
            "package": self.package,
            "status": self.status,
            "product_core": self.product_core,
            "target": self.target,
            "glue": self.glue,
            "incompatible": self.incompatible,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]


def _digest(*parts: str | None) -> str:
    return sha256("\0".join(part or "" for part in parts).encode()).hexdigest()


def _parse_metadata(
    name: str, manifest_text: str, binding_reader: Any, provenance: dict[str, Any]
) -> PackageMetadata:
    try:
        manifest = parse_package_manifest(yaml.safe_load(manifest_text))
    except (PackageManifestError, ValueError, yaml.YAMLError) as error:
        raise PreflightIncompatibleError(
            "package_metadata_invalid", f"package.yaml of {name!r} is invalid: {error}"
        ) from error
    if manifest.name != name:
        raise PreflightIncompatibleError(
            "package_mismatch", f"the source declares package {manifest.name!r}, not {name!r}"
        )
    binding = binding_text = None
    if manifest.default_binding:
        module, _, relative = manifest.default_binding.partition(":")
        binding_text = binding_reader(module, relative)
        if binding_text is None:
            raise PreflightIncompatibleError(
                "artifact_unavailable",
                f"default binding {manifest.default_binding} is missing from the source",
            )
        with TemporaryDirectory(prefix="kit-preflight-") as scratch:
            path = Path(scratch) / "default.yaml"
            path.write_text(binding_text)
            try:
                binding = load_binding(path)
            except BindingError as error:
                raise PreflightIncompatibleError(
                    "binding_invalid", f"default binding is invalid: {error}"
                ) from error
        if binding.package != name:
            raise PreflightIncompatibleError(
                "binding_invalid", f"default binding names package {binding.package!r}"
            )
    provenance = provenance | {
        "version": manifest.version,
        "requires_core": manifest.requires_core,
        "metadata_sha256": _digest(manifest_text, binding_text),
    }
    return PackageMetadata(manifest, binding, binding_text, provenance)


def metadata_from_source(name: str, project: Path, provenance: dict[str, Any]) -> PackageMetadata:
    """Read package metadata from one package source directory, never importing it."""

    manifests = sorted(project.glob("*/package.yaml")) if project.is_dir() else []
    if len(manifests) != 1:
        raise PreflightIncompatibleError(
            "artifact_unavailable",
            f"{project} must contain exactly one <module>/package.yaml, found {len(manifests)}",
        )

    def read(module: str, relative: str) -> str | None:
        root = (project / Path(*module.split("."))).resolve()
        path = (root / relative).resolve()
        if root != manifests[0].parent.resolve() or not path.is_relative_to(root):
            return None
        return path.read_text() if path.is_file() else None

    try:
        return _parse_metadata(name, manifests[0].read_text(), read, provenance)
    except (OSError, UnicodeError) as error:
        raise PreflightIncompatibleError(
            "artifact_unavailable", f"package metadata in {project} is unreadable: {error}"
        ) from error


def metadata_from_wheel(name: str, wheel: Path) -> PackageMetadata:
    """Read package metadata from a built wheel archive, never installing or importing it."""

    try:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            manifests = [
                item for item in names if PurePosixPath(item).parts[1:] == ("package.yaml",)
            ]
            if len(manifests) != 1:
                raise PreflightIncompatibleError(
                    "artifact_unavailable", f"{wheel.name} has {len(manifests)} package.yaml files"
                )
            module_root = PurePosixPath(manifests[0]).parent

            def read(module: str, relative: str) -> str | None:
                member = PurePosixPath(*module.split("."), relative)
                if PurePosixPath(*module.split(".")) != module_root or ".." in member.parts:
                    return None
                return archive.read(str(member)).decode() if str(member) in names else None

            return _parse_metadata(
                name,
                archive.read(manifests[0]).decode(),
                read,
                {"route": "wheel", "artifact": wheel.name},
            )
    except (zipfile.BadZipFile, OSError, UnicodeError) as error:
        raise PreflightIncompatibleError(
            "artifact_unavailable", f"{wheel.name} is not a readable wheel: {error}"
        ) from error


def metadata_from_catalog(
    name: str, core_version: str, source: str, ref: str, version: str | None = None
) -> PackageMetadata:
    """Resolve an exact version from an explicit catalog source/ref and read its tag."""

    from framework.package_source import fetch_package_source, read_catalog

    try:
        catalog = read_catalog(source, ref)
    except CatalogError as error:
        raise PreflightIncompatibleError("catalog_unavailable", str(error)) from error
    try:
        package = catalog.get_component(name)
    except (UnknownPackageError, CatalogError) as error:
        raise PreflightIncompatibleError("unknown_component", str(error)) from error
    if isinstance(package, CatalogLibrary):
        raise PreflightIncompatibleError(
            "unsupported_component",
            f"{name!r} is a tg_bot library; check-install covers runtime packages",
        )
    try:
        if version is None:
            selected = package.select(core_version)
        else:
            selected = next(item for item in package.versions if item.version == version)
    except IncompatibleCatalogVersionError as error:
        raise PreflightIncompatibleError("core_range", str(error)) from error
    except StopIteration as error:
        raise PreflightIncompatibleError(
            "unknown_component", f"catalog {source}@{ref} lists no {name} {version}"
        ) from error
    provenance = {
        "route": "catalog",
        "catalog_source": source,
        "catalog_ref": ref,
        "tag": selected.tag,
    }
    with TemporaryDirectory(prefix="kit-preflight-") as scratch:
        try:
            project = fetch_package_source(source, package, selected, Path(scratch))
        except CatalogError as error:
            raise PreflightIncompatibleError("artifact_unavailable", str(error)) from error
        metadata = metadata_from_source(name, project, provenance)
    if metadata.manifest.version != selected.version:
        raise PreflightIncompatibleError(
            "package_mismatch",
            f"tag {selected.tag} carries {name} {metadata.manifest.version}, catalog lists "
            f"{selected.version}",
        )
    return metadata


def _core_compatibility(core_version: str, metadata: PackageMetadata) -> None:
    manifest = metadata.manifest
    try:
        admitted = Version(core_version) in SpecifierSet(manifest.requires_core)
    except (InvalidSpecifier, InvalidVersion) as error:
        raise PreflightIncompatibleError("package_metadata_invalid", str(error)) from error
    if not admitted:
        raise PreflightIncompatibleError(
            "core_range",
            f"{manifest.name} {manifest.version} requires core {manifest.requires_core}; the "
            f"product façade is core {core_version}",
        )
    if isinstance(metadata.binding, BindingV2) and Version(core_version) < Version("2.4"):
        raise PreflightIncompatibleError(
            "core_range", "the default binding v2 requires product core >=2.4"
        )


def _product_shape(root: Path, metadata: PackageMetadata) -> None:
    """Read-only, the same ownership/provenance validation normal bind applies."""

    for service in ("backend", *(("tg_bot",) if metadata.binding else ())):
        if not (root / f"services/{service}/pyproject.toml").is_file():
            raise PreflightIncompatibleError(
                "product_shape", f"{metadata.manifest.name} needs a product with {service}"
            )
        if not (root / f"services/{service}/.venv/bin/python").is_file():
            raise PreflightIncompatibleError(
                "environment_missing",
                f"services/{service}/.venv is not installed; run the product's make setup",
            )
        try:
            require_service_environment(root, service)
        except BindingError as error:
            raise PreflightIncompatibleError("environment_invalid", str(error)) from error


def effective_binding(
    root: Path, metadata: PackageMetadata
) -> tuple[Binding | BindingV2 | None, bool]:
    """The binding the install will use: a retained product file wins over the default."""

    retained = root / host_contract.TG_BOT / "bindings" / f"{metadata.manifest.name}.yaml"
    if metadata.binding is None:
        return None, False
    if retained.is_file():
        return load_binding(retained), True
    return metadata.binding, False


def prospective_bindings(
    root: Path, metadata: PackageMetadata, binding_source: Path | None = None
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Product bindings after a default bind; a retained product file wins."""

    bindings = binding_files(root)
    sources = binding_sources(root, bindings)
    filename = f"{metadata.manifest.name}.yaml"
    if metadata.binding is not None and filename not in bindings:
        bindings[filename] = metadata.binding
        if binding_source is not None:
            sources[filename] = binding_source
    return bindings, sources


def _evaluate(root: Path, metadata: PackageMetadata) -> host_contract.HostContract:
    with TemporaryDirectory(prefix="kit-preflight-") as scratch:
        source = None
        if metadata.binding_text is not None:
            source = Path(scratch) / "default.yaml"
            source.write_text(metadata.binding_text)
        bindings, sources = prospective_bindings(root, metadata, source)
        return host_contract.evaluate(root, bindings, sources)


def admit(root: Path, metadata: PackageMetadata) -> None:
    """Refuse an install whose product would break the host contract, before any write.

    The order is the preflight's: environment, effective (retained or default) binding,
    then core language references and command claims.
    """

    host_contract.evaluate(root).require_valid()
    if metadata.binding is not None and (root / host_contract.TG_BOT).is_dir():
        require_binding_product(root)
        binding, _ = effective_binding(root, metadata)
        if binding is not None:
            validate_binding(binding, metadata.manifest, bundled_catalog())
        _evaluate(root, metadata).require_valid()


def _library_glue(root: Path, binding: Binding | BindingV2, catalog: Catalog) -> list[dict]:
    glue = []
    for command in binding.commands:
        if not isinstance(command, ParsedCreate):
            continue
        library = command.parse.function.partition(".")[0]
        try:
            library_evidence(root, library, catalog)
        except BindingError as error:
            glue.append(
                host_contract.Violation(
                    code="library_required",
                    owner=f"package:{binding.package}",
                    symbol=library,
                    command=command.command,
                    location=host_contract.Location(f"{host_contract.TG_BOT}/pyproject.toml"),
                    conflict=f"/{command.command} parses with library {library!r}: {error}",
                    action=f"run `kit add {library}` before installing {binding.package}",
                ).as_dict()
            )
    return glue


def _sort_key(item: dict[str, Any]) -> tuple:
    return (
        item["path"] or "",
        item["line"] or 0,
        item["code"],
        item["command"] or "",
        item["key"] or "",
        item["symbol"] or "",
    )


def evaluate_install(
    root: Path, metadata: PackageMetadata, catalog: Catalog | None = None
) -> CheckInstallResult:
    """Classify one exact prospective install against the actual product."""

    name = metadata.manifest.name
    result = CheckInstallResult(package=name, status="mechanical", target=metadata.provenance)
    _product_shape(root, metadata)
    try:
        result.product_core = product_core_version(root)
    except BindingError as error:
        raise PreflightIncompatibleError("core_unsupported", str(error)) from error
    _core_compatibility(result.product_core, metadata)
    try:
        binding, retained = effective_binding(root, metadata)
        contract = _evaluate(root, metadata)
    except BindingError as error:  # A product binding file cannot be read.
        raise PreflightIncompatibleError("binding_invalid", str(error)) from error
    if binding is not None:
        try:
            validate_binding(binding, metadata.manifest, catalog or bundled_catalog())
        except BindingError as error:
            where = f" (retained services/tg_bot/bindings/{name}.yaml)" if retained else ""
            raise PreflightIncompatibleError("binding_invalid", f"{error}{where}") from error
    unresolvable = [
        item
        for item in contract.violations
        if item.code not in host_contract.GLUE_CODES and item.owner != host_contract.PRODUCT_OWNER
    ]
    if unresolvable:
        first = unresolvable[0]
        raise PreflightIncompatibleError(first.code, first.describe())
    glue = [item.as_dict() for item in contract.violations]
    if binding is not None:
        glue.extend(_library_glue(root, binding, catalog or bundled_catalog()))
    result.glue = sorted(glue, key=_sort_key)
    result.status = "glue" if result.glue else "mechanical"
    return result


def check_install(  # noqa: PLR0913
    root: Path,
    name: str,
    *,
    package_source: Path | None = None,
    catalog_source: str | None = None,
    catalog_ref: str | None = None,
    version: str | None = None,
    catalog: Catalog | None = None,
) -> CheckInstallResult:
    """Read-only typed preflight; every refusal is a result, never a traceback."""

    metadata: PackageMetadata | None = None
    try:
        if package_source is not None:
            if catalog_source or catalog_ref:
                raise PreflightIncompatibleError(
                    "provenance_required", "use either a package source or a catalog, not both"
                )
            metadata = metadata_from_source(
                name,
                package_source,
                {"route": "package_source", "path": str(package_source)},
            )
            if version is not None and metadata.manifest.version != version:
                raise PreflightIncompatibleError(
                    "package_mismatch",
                    f"the source carries {name} {metadata.manifest.version}, not {version}",
                )
        elif catalog_source and catalog_ref:
            try:
                core = product_core_version(root)
            except BindingError as error:
                raise PreflightIncompatibleError("core_unsupported", str(error)) from error
            metadata = metadata_from_catalog(name, core, catalog_source, catalog_ref, version)
        else:
            raise PreflightIncompatibleError(
                "provenance_required",
                "pass --package-source DIR, or both --catalog-source and --catalog-ref; "
                "check-install never falls back to a live catalog",
            )
        return evaluate_install(root, metadata, catalog)
    except PreflightIncompatibleError as error:
        return CheckInstallResult(
            package=name,
            status="incompatible",
            target=metadata.provenance if metadata is not None else None,
            incompatible={"code": error.code, "explanation": error.explanation},
        )

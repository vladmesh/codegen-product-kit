"""Load and validate the package catalog that lists every released kit package."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
import yaml

CATALOG_PATH = "packages/catalog.yaml"
FORMAT_VERSION = 1
_BUNDLED = Path(__file__).with_name("package_catalog.yaml")
_SOURCE_CHECKOUT = Path(__file__).parents[1] / CATALOG_PATH


class CatalogError(ValueError):
    """The package catalog or a request against it is not acceptable."""

    def __init__(self, message: str) -> None:
        super().__init__(f"{type(self).__name__}: {message}")


class UnsupportedCatalogFormatError(CatalogError):
    """The catalog declares a format version this tooling does not read."""


class InvalidCatalogEntryError(CatalogError):
    """A catalog field is missing or has the wrong shape."""


class DuplicateCatalogPackageError(CatalogError):
    """Two catalog packages share one name."""


class DuplicateCatalogVersionError(CatalogError):
    """One catalog package lists the same version twice."""


class InvalidCatalogVersionError(CatalogError):
    """A catalog version is not a PEP 440 version."""


class UnknownPackageError(CatalogError):
    """The requested package is not in the catalog."""


class IncompatibleCatalogVersionError(CatalogError):
    """No released version of the package admits this kit core."""


def package_tag(name: str, version: str) -> str:
    """Return the release tag of one package version; deliberately not PEP 440."""

    return f"packages/{name}/v{version}"


@dataclass(frozen=True)
class CatalogSetting:
    """One product setting a package asks for."""

    name: str
    summary: str


@dataclass(frozen=True)
class CatalogEnvironment:
    """One environment variable a package asks the product for."""

    name: str
    required: bool
    summary: str


@dataclass(frozen=True)
class CatalogVersion:
    """One released package version."""

    version: str
    tag: str
    requires_core: str

    def admits(self, core_version: str) -> bool:
        return Version(core_version) in SpecifierSet(self.requires_core)


@dataclass(frozen=True)
class CatalogPackage:
    """One catalog package and its released versions."""

    name: str
    distribution: str
    path: str
    summary: str
    capabilities: tuple[str, ...]
    settings: tuple[CatalogSetting, ...]
    environment: tuple[CatalogEnvironment, ...]
    versions: tuple[CatalogVersion, ...]

    def newest(self) -> CatalogVersion:
        return max(self.versions, key=lambda item: Version(item.version))

    def select(self, core_version: str) -> CatalogVersion:
        """Return the newest released version whose core requirement admits ``core_version``."""

        compatible = [item for item in self.versions if item.admits(core_version)]
        if not compatible:
            requirements = ", ".join(
                f"{item.version} requires core {item.requires_core}" for item in self.versions
            )
            raise IncompatibleCatalogVersionError(
                f"package {self.name!r} has no version compatible with core {core_version}: "
                f"{requirements}"
            )
        return max(compatible, key=lambda item: Version(item.version))


@dataclass(frozen=True)
class Catalog:
    """A validated package catalog."""

    format_version: int
    packages: tuple[CatalogPackage, ...]

    def get(self, name: str) -> CatalogPackage:
        for package in self.packages:
            if package.name == name:
                return package
        known = ", ".join(package.name for package in self.packages) or "none"
        raise UnknownPackageError(f"unknown package {name!r}; known packages: {known}")


def _mapping(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidCatalogEntryError(f"{where} must be a mapping")
    return value


def _text(entry: dict[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidCatalogEntryError(f"{where} needs a non-empty string {key!r}")
    return value


def _items(entry: dict[str, Any], key: str, where: str) -> list[Any]:
    value = entry.get(key)
    if not isinstance(value, list):
        raise InvalidCatalogEntryError(f"{where} needs a list {key!r}")
    return value


def _unique(names: list[str], where: str) -> None:
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise InvalidCatalogEntryError(f"{where} repeats {duplicates[0]!r}")


def _version(raw: object, name: str, index: int) -> CatalogVersion:
    where = f"package {name!r} versions[{index}]"
    entry = _mapping(raw, where)
    version = _text(entry, "version", where)
    try:
        canonical = Version(version)
    except InvalidVersion as error:
        raise InvalidCatalogVersionError(
            f"package {name!r} version {version!r} is not a PEP 440 version"
        ) from error
    if str(canonical) != version:
        raise InvalidCatalogVersionError(
            f"package {name!r} version {version!r} must be written as {str(canonical)!r}"
        )
    tag = _text(entry, "tag", where)
    if tag != package_tag(name, version):
        raise InvalidCatalogEntryError(
            f"{where} tag {tag!r} must be {package_tag(name, version)!r}"
        )
    requires_core = _text(entry, "requires_core", where)
    try:
        SpecifierSet(requires_core)
    except InvalidSpecifier as error:
        raise InvalidCatalogEntryError(
            f"{where} requires_core {requires_core!r} is not a version specifier"
        ) from error
    return CatalogVersion(version=version, tag=tag, requires_core=requires_core)


def _package(raw: object, index: int) -> CatalogPackage:
    entry = _mapping(raw, f"packages[{index}]")
    name = _text(entry, "name", f"packages[{index}]")
    where = f"package {name!r}"
    capabilities = _items(entry, "capabilities", where)
    if not capabilities or not all(isinstance(item, str) and item.strip() for item in capabilities):
        raise InvalidCatalogEntryError(f"{where} needs non-empty string capabilities")
    settings = []
    for item in _items(entry, "settings", where):
        setting = _mapping(item, f"{where} setting")
        settings.append(
            CatalogSetting(
                name=_text(setting, "name", f"{where} setting"),
                summary=_text(setting, "summary", f"{where} setting"),
            )
        )
    environment = []
    for item in _items(entry, "environment", where):
        variable = _mapping(item, f"{where} environment")
        required = variable.get("required")
        if not isinstance(required, bool):
            raise InvalidCatalogEntryError(f"{where} environment needs a boolean 'required'")
        environment.append(
            CatalogEnvironment(
                name=_text(variable, "name", f"{where} environment"),
                required=required,
                summary=_text(variable, "summary", f"{where} environment"),
            )
        )
    _unique([item.name for item in settings], f"{where} settings")
    _unique([item.name for item in environment], f"{where} environment")
    raw_versions = _items(entry, "versions", where)
    if not raw_versions:
        raise InvalidCatalogEntryError(f"{where} needs at least one released version")
    versions = tuple(_version(item, name, position) for position, item in enumerate(raw_versions))
    seen: set[Version] = set()
    for item in versions:
        if Version(item.version) in seen:
            raise DuplicateCatalogVersionError(f"{where} lists version {item.version} twice")
        seen.add(Version(item.version))
    return CatalogPackage(
        name=name,
        distribution=_text(entry, "distribution", where),
        path=_text(entry, "path", where),
        summary=_text(entry, "summary", where),
        capabilities=tuple(capabilities),
        settings=tuple(settings),
        environment=tuple(environment),
        versions=versions,
    )


def parse_catalog(source: str, origin: str = CATALOG_PATH) -> Catalog:
    """Validate catalog text; every refusal is a named ``CatalogError``."""

    try:
        document = yaml.safe_load(source)
    except yaml.YAMLError as error:
        raise InvalidCatalogEntryError(f"{origin} is not valid YAML: {error}") from error
    document = _mapping(document, origin)
    format_version = document.get("format_version")
    if format_version != FORMAT_VERSION:
        raise UnsupportedCatalogFormatError(
            f"{origin} has format_version {format_version!r}; this tooling reads {FORMAT_VERSION}"
        )
    packages = tuple(
        _package(raw, index) for index, raw in enumerate(_items(document, "packages", origin))
    )
    names: set[str] = set()
    for package in packages:
        if package.name in names:
            raise DuplicateCatalogPackageError(f"{origin} lists package {package.name!r} twice")
        names.add(package.name)
    return Catalog(format_version=format_version, packages=packages)


def load_catalog(path: Path) -> Catalog:
    """Read and validate one catalog file."""

    return parse_catalog(path.read_text(), str(path))


def bundled_catalog() -> Catalog:
    """Return the catalog shipped with this tooling, frozen at the tooling's kit ref."""

    path = _BUNDLED if _BUNDLED.is_file() else _SOURCE_CHECKOUT
    return load_catalog(path)

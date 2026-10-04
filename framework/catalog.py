"""Load and validate the package catalog that lists every released kit package."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, SchemaError
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
import yaml

from framework.component_matching import non_null_schema

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


class DuplicateCatalogComponentError(CatalogError):
    """Component identities collide across catalog lists."""


class ExtensionPreconditionError(CatalogError):
    """An extension's installed parent does not satisfy its declaration."""


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
class CatalogAction:
    name: str
    input: dict[str, Any]
    output: dict[str, Any]


@dataclass(frozen=True)
class CatalogFunction(CatalogAction):
    value: str


@dataclass(frozen=True)
class CatalogRecommendation:
    library: str
    why: str


@dataclass(frozen=True)
class CatalogParent:
    package: str
    versions: str


@dataclass(frozen=True)
class CatalogLibraryVersion:
    version: str
    tag: str
    requires_python: str


@dataclass(frozen=True)
class CatalogLibrary:
    name: str
    distribution: str
    path: str
    module: str
    summary: str
    functions: tuple[CatalogFunction, ...]
    versions: tuple[CatalogLibraryVersion, ...]


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
    actions: tuple[CatalogAction, ...] = ()
    recommended_with: tuple[CatalogRecommendation, ...] = ()
    default_binding: str | None = None
    extends: CatalogParent | None = None

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
    libraries: tuple[CatalogLibrary, ...] = ()
    extensions: tuple[CatalogPackage, ...] = ()

    def get(self, name: str) -> CatalogPackage:
        for package in self.packages:
            if package.name == name:
                return package
        known = ", ".join(package.name for package in self.packages) or "none"
        raise UnknownPackageError(f"unknown package {name!r}; known packages: {known}")

    def get_installable(self, name: str) -> CatalogPackage:
        """Resolve a runtime package or extension; libraries are not activated."""

        for extension in self.extensions:
            if extension.name == name:
                return extension
        return self.get(name)


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


def _range(value: str, where: str) -> str:
    try:
        SpecifierSet(value)
    except InvalidSpecifier as error:
        raise InvalidCatalogEntryError(f"{where} {value!r} is not a version specifier") from error
    return value


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


def _schema(value: object, where: str) -> dict[str, Any]:
    schema = _mapping(value, where)
    try:
        json.dumps(schema, allow_nan=False)
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise InvalidCatalogEntryError(f"{where}: invalid JSON Schema: {error.message}") from error
    except (TypeError, ValueError) as error:
        raise InvalidCatalogEntryError(
            f"{where}: schema must contain JSON values: {error}"
        ) from error
    _schema_shapes(schema, where)
    return schema


def _schema_shapes(schema: dict[str, Any], where: str) -> None:
    """Catalog signatures use object schemas, including at nested positions."""

    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        for index, child in enumerate(schema.get(key, [])):
            location = f"{where}.{key}[{index}]"
            _schema_shapes(_mapping(child, location), location)
    for key in ("properties", "$defs", "patternProperties", "dependentSchemas"):
        for name, child in schema.get(key, {}).items():
            location = f"{where}.{key}.{name}"
            _schema_shapes(_mapping(child, location), location)
    for key in ("items", "contains", "not", "if", "then", "else", "additionalProperties"):
        child = schema.get(key)
        if child is not None and (key != "additionalProperties" or isinstance(child, dict)):
            location = f"{where}.{key}"
            _schema_shapes(_mapping(child, location), location)
    if (
        "properties" in schema
        and not set(schema.get("required", [])) <= schema["properties"].keys()
    ):
        raise InvalidCatalogEntryError(f"{where} requires undeclared properties")


def _actions(entry: dict[str, Any], key: str, where: str) -> tuple[CatalogAction, ...]:
    result = []
    for raw in _items({key: entry.get(key, [])}, key, where):
        item = _mapping(raw, f"{where} {key}")
        name = _text(item, "name", where)
        location = f"{where} {key} {name!r}"
        input_schema = _schema(item.get("input"), f"{location} input")
        if input_schema.get("type") != "object" or not isinstance(
            input_schema.get("properties"), dict
        ):
            raise InvalidCatalogEntryError(f"{location} input needs object properties")
        if not set(input_schema.get("required", [])) <= input_schema["properties"].keys():
            raise InvalidCatalogEntryError(f"{location} input requires undeclared parameters")
        output = _schema(item.get("output"), f"{location} output")
        if key == "functions":
            value = _text(item, "value", location)
            primary = non_null_schema(output)
            if (
                primary.get("type") != "object"
                or value not in primary.get("properties", {})
                or value not in primary.get("required", [])
            ):
                raise InvalidCatalogEntryError(
                    f"{location} value {value!r} must name a required output property"
                )
            result.append(CatalogFunction(name, input_schema, output, value))
        else:
            result.append(CatalogAction(name, input_schema, output))
    _unique([item.name for item in result], f"{where} {key}")
    return tuple(result)


def _library(raw: object, index: int) -> CatalogLibrary:
    entry = _mapping(raw, f"libraries[{index}]")
    name = _text(entry, "name", f"libraries[{index}]")
    where = f"library {name!r}"
    versions = []
    for position, raw_version in enumerate(_items(entry, "versions", where)):
        item = _mapping(raw_version, f"{where} versions[{position}]")
        requirement = _range(_text(item, "requires_python", where), f"{where} requires_python")
        # Libraries use the same independent tag/version convention, without core activation.
        version = _version(dict(item, requires_core=">=0"), name, position)
        versions.append(CatalogLibraryVersion(version.version, version.tag, requirement))
    if not versions:
        raise InvalidCatalogEntryError(f"{where} needs at least one released version")
    if len({item.version for item in versions}) != len(versions):
        raise DuplicateCatalogVersionError(f"{where} repeats a version")
    functions = _actions(entry, "functions", where)
    if not functions:
        raise InvalidCatalogEntryError(f"{where} needs functions")
    module = _text(entry, "module", where)
    if not all(part.isidentifier() for part in module.split(".")):
        raise InvalidCatalogEntryError(f"{where} module {module!r} must be a dotted module name")
    return CatalogLibrary(
        name,
        _text(entry, "distribution", where),
        _text(entry, "path", where),
        module,
        _text(entry, "summary", where),
        tuple(item for item in functions if isinstance(item, CatalogFunction)),
        tuple(versions),
    )


def _recommendations(entry: dict[str, Any], where: str) -> tuple[CatalogRecommendation, ...]:
    recommendations = []
    for raw in _items(
        {"recommended_with": entry.get("recommended_with", [])}, "recommended_with", where
    ):
        item = _mapping(raw, f"{where} recommended_with")
        recommendations.append(
            CatalogRecommendation(_text(item, "library", where), _text(item, "why", where))
        )
    _unique([item.library for item in recommendations], f"{where} recommended_with")
    return tuple(recommendations)


def _binding(entry: dict[str, Any], where: str) -> str | None:
    if "default_binding" not in entry:
        return None
    binding = _text(entry, "default_binding", where)
    module, separator, resource = binding.partition(":")
    if (
        not separator
        or not all(part.isidentifier() for part in module.split("."))
        or not resource
        or resource.startswith("/")
        or ".." in resource.split("/")
    ):
        raise InvalidCatalogEntryError(f"{where} default_binding needs module:relative/path")
    return binding


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
        actions=_actions(entry, "actions", where),
        recommended_with=_recommendations(entry, where),
        default_binding=_binding(entry, where),
    )


def _catalog_package(raw: object, index: int) -> CatalogPackage:
    entry = _mapping(raw, f"packages[{index}]")
    if "extends" in entry:
        raise InvalidCatalogEntryError(
            f"packages[{index}] {entry.get('name')!r} has misplaced 'extends'; "
            "move the record to top-level 'extensions'"
        )
    return _package(entry, index)


def _extension(raw: object, index: int, parent_names: set[str]) -> CatalogPackage:
    entry = _mapping(raw, f"extensions[{index}]")
    extension = _package(entry, index)
    where = f"extension {extension.name!r} extends"
    parent = _mapping(entry.get("extends"), where)
    parent_name = _text(parent, "package", where)
    if parent_name not in parent_names:
        raise InvalidCatalogEntryError(f"{where} unknown parent package {parent_name!r}")
    requirement = _range(_text(parent, "versions", where), where)
    return replace(extension, extends=CatalogParent(parent_name, requirement))


def _component_identities(catalog: Catalog, origin: str) -> None:
    names = {package.name for package in catalog.packages}
    for component in (*catalog.libraries, *catalog.extensions):
        if component.name in names:
            raise DuplicateCatalogComponentError(f"{origin} repeats component {component.name!r}")
        names.add(component.name)
    distributions: set[str] = set()
    for component in (*catalog.packages, *catalog.libraries, *catalog.extensions):
        distribution = canonicalize_name(component.distribution)
        if distribution in distributions:
            raise DuplicateCatalogComponentError(f"{origin} repeats distribution {distribution!r}")
        distributions.add(distribution)
    library_names = {library.name for library in catalog.libraries}
    for package in (*catalog.packages, *catalog.extensions):
        for recommendation in package.recommended_with:
            if recommendation.library not in library_names:
                raise InvalidCatalogEntryError(
                    f"package {package.name!r} recommends unknown library "
                    f"{recommendation.library!r}"
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
        _catalog_package(raw, index)
        for index, raw in enumerate(_items(document, "packages", origin))
    )
    names: set[str] = set()
    for package in packages:
        if package.name in names:
            raise DuplicateCatalogPackageError(f"{origin} lists package {package.name!r} twice")
        names.add(package.name)
    libraries = tuple(
        _library(raw, index)
        for index, raw in enumerate(
            _items({"libraries": document.get("libraries", [])}, "libraries", origin)
        )
    )
    extensions = tuple(
        _extension(raw, index, names)
        for index, raw in enumerate(
            _items({"extensions": document.get("extensions", [])}, "extensions", origin)
        )
    )
    catalog = Catalog(format_version, packages, libraries, extensions)
    _component_identities(catalog, origin)
    return catalog


def load_catalog(path: Path) -> Catalog:
    """Read and validate one catalog file."""

    return parse_catalog(path.read_text(), str(path))


def bundled_catalog() -> Catalog:
    """Return the catalog shipped with this tooling, frozen at the tooling's kit ref."""

    path = _BUNDLED if _BUNDLED.is_file() else _SOURCE_CHECKOUT
    return load_catalog(path)

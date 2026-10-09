"""Product binding preflight. Reads metadata and data, never component runtime code."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from importlib import metadata
import json
from pathlib import Path
import subprocess

from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from framework.bindings import Binding, BindingError, ParsedCreate, load_binding, validate_binding
from framework.bindings_v2 import BindingV2
from framework.catalog import Catalog, bundled_catalog
from framework.spec.core_settings import CORE_OWNER, CORE_SETTINGS, LANGUAGE_KEY
from framework.spec.loader import AllSpecs
from framework.spec.package_resolution import ActivePackage

TIMEZONE_SCHEMA = {"type": "string", "format": "x-iana-tz"}
#: Bindings reference the core-owned language setting; they never declare it.
LANGUAGE_SCHEMA = CORE_SETTINGS[LANGUAGE_KEY]


@dataclass
class BindingPlan:
    bindings: list[Binding | BindingV2]
    actions: dict[str, dict]
    events: dict[str, dict]
    libraries: dict[str, str]


def require_binding_product(root: Path) -> None:
    for service in ("backend", "tg_bot"):
        if not (root / f"services/{service}/pyproject.toml").is_file():
            raise BindingError(f"BindingProductShapeError: {service} is required")
        if not (root / f"services/{service}/.venv/bin/python").is_file():
            raise BindingError(f"BindingEnvironmentError: {service} environment is not installed")
        environment = root / f"services/{service}/.venv"
        if (
            not environment.resolve().is_relative_to(root.resolve())
            or not (environment / "pyvenv.cfg").is_file()
        ):
            raise BindingError(f"BindingEnvironmentError: {service} must own a product virtualenv")
        _environment(root, service)


def _environment(root: Path, service: str) -> tuple[list[str], str]:
    environment = root / f"services/{service}/.venv"
    result = subprocess.run(  # noqa: S603
        [
            str(environment / "bin/python"),
            "-I",
            "-c",
            "import site, platform, sys, json; "
            "print(json.dumps([sys.prefix, site.getsitepackages(), platform.python_version()]))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    prefix, sites, python_version = json.loads(result.stdout)
    if (
        Path(prefix).resolve() != environment.resolve()
        or not sites
        or any(not Path(site).resolve().is_relative_to(environment.resolve()) for site in sites)
    ):
        raise BindingError(f"BindingEnvironmentError: {service} must use its product virtualenv")
    return sites, python_version


def library_evidence(root: Path, name: str, catalog: Catalog) -> str:
    library = next((item for item in catalog.libraries if item.name == name), None)
    if library is None:
        raise BindingError(f"BindingLibraryError: unknown library {name}")
    # Isolated interpreter reads only stdlib metadata. It never imports the library.
    sites, python_version = _environment(root, "tg_bot")
    installed = [
        item
        for item in metadata.distributions(path=sites)
        if canonicalize_name(item.metadata.get("Name") or "")
        == canonicalize_name(library.distribution)
    ]
    if len(installed) != 1:
        raise BindingError(
            f"BindingLibraryError: {library.distribution} must be installed in tg_bot"
        )
    distribution = installed[0]
    version = next(
        (item for item in library.versions if item.version == distribution.version), None
    )
    if version is None or Version(python_version) not in SpecifierSet(version.requires_python):
        raise BindingError(f"BindingLibraryError: unsupported {name} version/Python")
    module = Path(*library.module.split(".")) / "__init__.py"
    recorded = {str(item) for item in distribution.files or ()}
    path = Path(distribution.locate_file(module)).resolve()
    if (
        str(module) not in recorded
        or not path.is_file()
        or not any(path.is_relative_to(Path(site).resolve()) for site in sites)
    ):
        raise BindingError(f"BindingLibraryError: {name} installed module evidence is missing")
    _library_exports(path, library)
    return library.module


def _library_exports(path, library) -> None:
    try:
        exports = {
            node.name: node
            for node in ast.parse(path.read_text()).body
            if isinstance(node, ast.FunctionDef)
        }
        for function in library.functions:
            node = exports.get(function.name)
            parameters = (
                {
                    arg.arg
                    for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                }
                if node
                else set()
            )
            if parameters != set(function.input["properties"]):
                raise ValueError(f"missing or incompatible export {function.name}")
    except (OSError, SyntaxError, ValueError) as error:
        raise BindingError(
            f"BindingLibraryError: {library.name} module signature: {error}"
        ) from error


def binding_files(root: Path) -> dict[str, Binding | BindingV2]:
    return {
        path.name: load_binding(path)
        for path in sorted((root / "services/tg_bot/bindings").glob("*.yaml"))
    }


def binding_sources(root: Path, bindings: dict[str, Binding | BindingV2]) -> dict[str, Path]:
    """Product files the bindings were read from, for source locations."""
    return {name: root / "services/tg_bot/bindings" / name for name in bindings}


def _libraries(root, binding, catalog, plan) -> None:
    # Command names and ownership are resolved by the core host contract.
    for command in binding.commands:
        if isinstance(command, ParsedCreate):
            name = command.parse.function.partition(".")[0]
            if name not in plan.libraries:
                plan.libraries[name] = library_evidence(root, name, catalog)


def product_core_version(root: Path) -> str:
    """Read the product facade's declared version without importing its runtime."""
    try:
        for node in ast.parse((root / "codegen_kit/packages.py").read_text()).body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "CORE_VERSION"
                for target in node.targets
            ):
                value = ast.literal_eval(node.value)
                if isinstance(value, str) and Version(value) >= Version("2.2"):
                    return value
                break
    except (OSError, SyntaxError, ValueError) as error:
        raise BindingError(
            f"BindingCoreError: invalid product core declaration: {error}"
        ) from error
    raise BindingError("BindingCoreError: product facade must declare core >=2.2; upgrade first")


def _binding_package(
    binding: Binding | BindingV2, packages: dict[str, ActivePackage], core_version: str
) -> ActivePackage:
    package = packages.get(binding.package)
    if package is None:
        raise BindingError(f"BindingPackageError: {binding.package} is not installed and active")
    if not package.manifest.actions:
        raise BindingError(f"BindingActionsError: {binding.package} has no actions")
    if Version(core_version) not in SpecifierSet(package.manifest.requires_core):
        raise BindingError(
            f"BindingCoreError: {binding.package} is incompatible with core {core_version}"
        )
    if isinstance(binding, BindingV2) and Version(core_version) < Version("2.4"):
        raise BindingError("BindingCoreError: binding v2 requires core >=2.4; upgrade first")
    return package


def binding_settings(binding: Binding | BindingV2) -> dict[str, dict]:
    """Settings a binding reads: the core language and an optional product timezone."""
    settings = {}
    if binding.timezone is not None:
        settings[binding.timezone.key] = TIMEZONE_SCHEMA.copy()
    if isinstance(binding, BindingV2):
        if binding.language.key in settings:
            raise BindingError("BindingLanguageError: language and timezone need distinct keys")
        settings[binding.language.key] = LANGUAGE_SCHEMA.copy()
    return settings


def _setting_keys(bindings) -> None:
    if len({binding.timezone.key for binding in bindings if binding.timezone}) > 1:
        raise BindingError("BindingTimezoneError: all bindings must use one product timezone key")
    if len({binding.language.key for binding in bindings if isinstance(binding, BindingV2)}) > 1:
        raise BindingError(
            "BindingLanguageError: all v2 bindings must use one product language key"
        )


def _setting_owners(binding: Binding | BindingV2, specs: AllSpecs) -> None:
    """Language resolves to the core declaration; a timezone to a product manifest."""
    for key, schema in binding_settings(binding).items():
        owner = specs.settings_schema_sources.get(key)
        if key in CORE_SETTINGS or schema == LANGUAGE_SCHEMA:
            if owner != CORE_OWNER or specs.settings_schemas.get(key) != schema:
                raise BindingError(
                    f"BindingSettingError: {key} must reference the core-owned "
                    f"{LANGUAGE_KEY!r} setting"
                )
        elif owner not in specs.manifests or specs.settings_schemas.get(key) != schema:
            raise BindingError(f"BindingTimezoneError: {key} needs a product-owned {schema}")


def validate_product_bindings(
    root: Path,
    specs: AllSpecs,
    bindings: dict[str, Binding | BindingV2] | None = None,
    catalog: Catalog | None = None,
) -> BindingPlan:
    selected = bindings if bindings is not None else binding_files(root)
    plan = BindingPlan([], {}, {}, {})
    if not selected:
        return plan
    _setting_keys(selected.values())
    require_binding_product(root)
    core_version = product_core_version(root)
    catalog = catalog or bundled_catalog()
    packages = {item.name: item for item in specs.packages}
    events: set[str] = set()
    package_names: set[str] = set()
    for _filename, binding in sorted(selected.items()):
        package = _binding_package(binding, packages, core_version)
        if binding.package in package_names:
            raise BindingError(
                f"BindingDuplicateError: duplicate package binding {binding.package}"
            )
        package_names.add(binding.package)
        validate_binding(binding, package.manifest, catalog)
        _setting_owners(binding, specs)
        _libraries(root, binding, catalog, plan)
        for event in binding.events:
            if event.event in events:
                raise BindingError(f"BindingDuplicateError: duplicate event {event.event}")
            events.add(event.event)
            plan.events[event.event] = package.manifest.events.messages[event.event].schema_data
        for action in package.manifest.actions:
            data = action.model_dump()
            if not action.errors:
                data.pop("errors")  # Preserve the serialized v1 action contract byte for byte.
            data["operation"]["path"] = package.manifest.http.prefix + action.operation.path
            plan.actions[f"{binding.package}.{action.name}"] = data
        plan.bindings.append(binding)
    return plan


def default_binding_resource(package: ActivePackage) -> Path:
    reference = package.manifest.default_binding
    if not reference:
        raise BindingError(f"BindingDefaultError: {package.name} has no installed default binding")
    module, _, relative = reference.partition(":")
    root = package.package_root.resolve()
    if tuple(root.parts[-len(module.split(".")) :]) != tuple(module.split(".")):
        raise BindingError(
            "BindingResourceError: default resource must belong to installed package"
        )
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise BindingError("BindingResourceError: default resource escapes or is missing")
    return path

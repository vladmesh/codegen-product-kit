"""The core host contract: one owner for shared core settings and Telegram commands.

Generation, ``kit add``/``kit bind`` admission, the product's hard lint and the read-only
``kit check-install`` preflight all evaluate this one contract. It reads product files and
binding data only; it never imports product, package or binding runtime code.

Order: collect typed declarations with their source locations, resolve core ownership and
exclusive command claims, then report every violation before anything is written. The
generated tg_bot registry (``services/tg_bot/src/generated/commands.py``) renders the same
validated claims, and the bot registers commands, module bindings and unknown input from it.
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sys
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader
import yaml

from framework.bindings import BindingError
from framework.generators.base import GENERATED_HEADER
from framework.lib.fs import atomic_write_text
from framework.spec.core_settings import (
    CORE_OWNER,
    CORE_SETTING_SCOPES,
    CORE_SETTINGS,
    LANGUAGE_KEY,
)

if TYPE_CHECKING:
    from framework.bindings import Binding
    from framework.bindings_v2 import BindingV2

PRODUCT_OWNER = "product"
#: Commands only the core may register. ``command`` exists in backend shapes only, but it
#: stays reserved everywhere so a later backend does not change a product command's meaning.
RESERVED_COMMANDS = ("start", "command")
MAX_COMMAND_LENGTH = 32
COMMAND_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
TG_BOT = "services/tg_bot"
CORE_COMMAND_SOURCE = f"{TG_BOT}/src/main.py"
PRODUCT_COMMANDS = f"{TG_BOT}/src/commands.py"
REGISTRY = f"{TG_BOT}/src/generated/commands.py"
PRODUCT_COMMAND_TYPE = "ProductCommand"
#: Names that register Telegram handlers or match arbitrary input. Product tg_bot code may
#: not mention them: commands go through ``COMMANDS`` and unknown input belongs to core.
BYPASS_NAMES = frozenset(
    {
        "BaseHandler",
        "CommandHandler",
        "ConversationHandler",
        "MessageHandler",
        "PrefixHandler",
        "StringCommandHandler",
        "StringRegexHandler",
        "TypeHandler",
        "add_handler",
        "add_handlers",
        "remove_handler",
    }
)
#: Names that reach a PTB Application; ``<application>.handlers`` is the core registry itself.
APPLICATION_NAMES = frozenset({"application", "app"})
#: Annotations that type a name as the bot application.
APPLICATION_TYPES = frozenset({"Application", "CoreApplication"})
#: Builtins that read, replace or delete an attribute named by a string.
_ATTRIBUTE_FUNCTIONS = frozenset({"getattr", "setattr", "delattr"})
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
#: tg_bot paths that are not product handler code: environments, tests, wheels and the
#: generated tree, which product CI drift-checks against the generator instead.
_SKIPPED_ROOTS = frozenset({".venv", "tests", "packages"})

#: Violations the product author resolves by editing product-owned files. A binding
#: violation is glue too when the binding is a retained product file (owner ``product``).
GLUE_CODES = frozenset(
    {
        "core_setting_redeclared",
        "command_collision",
        "reserved_command",
        "invalid_command",
        "unsupported_product_command",
        "registration_bypass",
    }
)


@dataclass(frozen=True)
class Location:
    """A product-relative file and, when known, its 1-based line."""

    path: str
    line: int | None = None

    def __str__(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


@dataclass(frozen=True)
class CommandClaim:
    """One Telegram command and the owner that declares it."""

    command: str
    owner: str
    location: Location
    symbol: str


@dataclass(frozen=True)
class Violation:
    """One named breach of the two hard host invariants, with a concrete repair."""

    code: str
    owner: str
    conflict: str
    action: str
    location: Location | None = None
    key: str | None = None
    command: str | None = None
    symbol: str | None = None
    other: CommandClaim | None = None

    def describe(self) -> str:
        where = f"{self.location}: " if self.location else ""
        other = f" (other owner {self.other.owner} at {self.other.location})" if self.other else ""
        return f"{self.code}: {where}{self.conflict}{other}; {self.action}"

    def as_dict(self) -> dict[str, Any]:
        other = None
        if self.other is not None:
            other = {
                "owner": self.other.owner,
                "path": self.other.location.path,
                "line": self.other.location.line,
                "symbol": self.other.symbol,
            }
        return {
            "code": self.code,
            "path": self.location.path if self.location else None,
            "line": self.location.line if self.location else None,
            "owner": self.owner,
            "symbol": self.symbol,
            "key": self.key,
            "command": self.command,
            "conflict": self.conflict,
            "action": self.action,
            "other": other,
        }


class HostContractError(BindingError):
    """The product breaks core setting ownership or command registration."""

    def __init__(self, violations: Iterable[Violation]) -> None:
        self.violations = tuple(violations)
        lines = "\n".join(f"  - {item.describe()}" for item in self.violations)
        super().__init__(f"HostContractError: core host contract violated:\n{lines}")


@dataclass
class HostContract:
    """The validated core settings and command registry of one (prospective) product."""

    backend: bool
    tg_bot: bool
    settings: dict[str, str] = field(default_factory=dict)
    commands: list[CommandClaim] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)

    def require_valid(self) -> HostContract:
        if self.violations:
            raise HostContractError(self.violations)
        return self


def _key_lines(node: yaml.Node | None, *path: str) -> dict[str, int]:
    """Return 1-based lines of the mapping keys found under ``path`` in a composed node."""

    for name in path:
        if not isinstance(node, yaml.MappingNode):
            return {}
        node = next((value for key, value in node.value if key.value == name), None)
    if not isinstance(node, yaml.MappingNode):
        return {}
    return {key.value: key.start_mark.line + 1 for key, _ in node.value}


def _compose(path: Path) -> yaml.Node | None:
    try:
        return yaml.compose(path.read_text())
    except (OSError, yaml.YAMLError):
        return None


def manifest_setting_violations(root: Path) -> list[Violation]:
    """Product service manifests must be valid and must not declare a core-owned key."""

    from framework.spec.loader import SpecValidationError, load_manifest

    violations = []
    for manifest in sorted((root / "services").glob("*/manifest.yaml")):
        relative = str(manifest.relative_to(root))
        service = manifest.parent.name
        try:
            properties = load_manifest(manifest).settings_schema["properties"]
        except (SpecValidationError, OSError, UnicodeError) as error:
            # Fail closed: the ownership of an unreadable manifest cannot be verified.
            violations.append(
                Violation(
                    code="manifest_invalid",
                    owner=f"service:{service}",
                    location=Location(relative),
                    conflict=f"{relative} is not a valid service manifest: {error}",
                    action=f"make {relative} a valid version 1 service manifest",
                )
            )
            continue
        lines = _key_lines(_compose(manifest), "settings_schema", "properties")
        for key in sorted(CORE_SETTINGS.keys() & set(properties)):
            violations.append(
                Violation(
                    code="core_setting_redeclared",
                    owner=f"service:{service}",
                    key=key,
                    location=Location(relative, lines.get(key)),
                    conflict=(
                        f"setting {key!r} is owned by core ({CORE_SETTING_SCOPES[key]} scope, "
                        f"{json.dumps(CORE_SETTINGS[key], sort_keys=True)}); "
                        f"{relative} declares it again"
                    ),
                    action=(
                        f"delete settings_schema.properties.{key} from {relative} and read or "
                        f"set the core {key!r} setting through the ordinary settings API"
                    ),
                )
            )
    return violations


def _command_arguments(node: ast.Call) -> dict[str, ast.expr] | str:
    """Bind a call like the runtime constructor (name, handler), or name the unsupported form."""

    parameters = ("name", "handler")
    if any(isinstance(item, ast.Starred) for item in node.args) or any(
        keyword.arg is None for keyword in node.keywords
    ):
        return f"{PRODUCT_COMMAND_TYPE} arguments may not be unpacked"
    if len(node.args) > len(parameters):
        return f"{PRODUCT_COMMAND_TYPE} takes at most a name and a handler positionally"
    arguments: dict[str, ast.expr] = dict(zip(parameters, node.args, strict=False))
    for keyword in node.keywords:
        if keyword.arg not in parameters:
            return f"{PRODUCT_COMMAND_TYPE} has no argument {keyword.arg!r}"
        if keyword.arg in arguments:
            return f"{PRODUCT_COMMAND_TYPE} receives {keyword.arg!r} twice"
        arguments[str(keyword.arg)] = keyword.value
    if set(arguments) != set(parameters):
        return f"{PRODUCT_COMMAND_TYPE} takes exactly a name and a handler"
    return arguments


def _product_command(node: ast.expr, location: Location) -> tuple[CommandClaim | None, str]:
    """Admit exactly the runtime constructor's shapes: name and handler, positional or named."""

    if not (
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == PRODUCT_COMMAND_TYPE)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == PRODUCT_COMMAND_TYPE)
        )
    ):
        return None, f"each COMMANDS entry must be a {PRODUCT_COMMAND_TYPE}(...) call"
    arguments = _command_arguments(node)
    if isinstance(arguments, str):
        return None, arguments
    name, handler = arguments["name"], arguments["handler"]
    if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
        return None, "the command name must be a string literal"
    if not isinstance(handler, ast.Name | ast.Attribute):
        return None, "the handler must name a function"
    return CommandClaim(name.value, PRODUCT_OWNER, location, ast.unparse(handler)), ""


def product_command_claims(root: Path) -> tuple[list[CommandClaim], list[Violation]]:
    """Read ``COMMANDS`` from the product command module as data, never importing it."""

    path = root / PRODUCT_COMMANDS
    if not path.is_file():
        return [], []
    try:
        module = ast.parse(path.read_text(), filename=str(path))
    except (OSError, SyntaxError) as error:
        return [], [
            Violation(
                code="unsupported_product_command",
                owner=PRODUCT_OWNER,
                location=Location(PRODUCT_COMMANDS, getattr(error, "lineno", None)),
                conflict=f"the product command module cannot be read: {error}",
                action=f"make {PRODUCT_COMMANDS} valid Python",
            )
        ]
    claims: list[CommandClaim] = []
    violations: list[Violation] = []
    assignments = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign)
        and any(
            isinstance(target, ast.Name) and target.id == "COMMANDS"
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
    ]
    form = (
        f"declare product commands only as one top-level `COMMANDS = ({PRODUCT_COMMAND_TYPE}"
        '("name", handler), ...)` tuple in ' + PRODUCT_COMMANDS
    )
    top_level = [node for node in assignments if node in module.body]
    if len(assignments) != 1 or len(top_level) != 1 or isinstance(top_level[0], ast.AugAssign):
        line = assignments[0].lineno if assignments else None
        violations.append(
            Violation(
                code="unsupported_product_command",
                owner=PRODUCT_OWNER,
                location=Location(PRODUCT_COMMANDS, line),
                conflict="COMMANDS must be assigned exactly once at module level",
                action=form,
            )
        )
        return claims, violations
    value = top_level[0].value
    if not isinstance(value, ast.Tuple):  # Immutable: nothing can append after the lint.
        violations.append(
            Violation(
                code="unsupported_product_command",
                owner=PRODUCT_OWNER,
                location=Location(PRODUCT_COMMANDS, top_level[0].lineno),
                conflict="COMMANDS must be a literal tuple of ProductCommand entries",
                action=form,
            )
        )
        return claims, violations
    for element in value.elts:
        location = Location(PRODUCT_COMMANDS, element.lineno)
        claim, problem = _product_command(element, location)
        if claim is None:
            violations.append(
                Violation(
                    code="unsupported_product_command",
                    owner=PRODUCT_OWNER,
                    location=location,
                    conflict=problem,
                    action=form,
                )
            )
        else:
            claims.append(claim)
    return claims, violations


def _bypass_names(node: ast.AST) -> Iterable[str]:
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, ast.Attribute):
        yield node.attr
    elif isinstance(node, ast.alias):
        yield node.name.rpartition(".")[2]
        if node.asname:
            yield node.asname
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value


def _annotates_application(node: ast.expr | None) -> bool:
    while isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.rpartition(".")[2].partition("[")[0] in APPLICATION_TYPES
    return (isinstance(node, ast.Name) and node.id in APPLICATION_TYPES) or (
        isinstance(node, ast.Attribute) and node.attr in APPLICATION_TYPES
    )


def _scope_nodes(scope: ast.AST) -> Iterable[ast.AST]:
    """The nodes of one scope, without the bodies of the functions and classes it defines."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


@dataclass
class _ApplicationAliases:
    """Names and attributes that hold the bot application, following ordinary aliases.

    ``context.application``, ``app`` and ``application`` reach it directly; ``bot =
    context.application``, ``bot: Application = ...``, a parameter typed ``Application``,
    ``with ... as bot``, ``(bot := ...)`` and ``self.bot = context.application`` make an alias.
    Names are scoped like Python's: a function sees its own and its enclosing functions' names,
    and a parameter of the same name shadows them. Attribute aliases hold module-wide.
    """

    attributes: set[str] = field(default_factory=set)

    def reaches(self, node: ast.AST, names: Collection[str]) -> bool:
        while isinstance(node, ast.Attribute | ast.Subscript | ast.Call):
            if isinstance(node, ast.Attribute) and (
                node.attr in APPLICATION_NAMES or node.attr in self.attributes
            ):
                return True
            node = node.func if isinstance(node, ast.Call) else node.value
        return isinstance(node, ast.Name) and (node.id in APPLICATION_NAMES or node.id in names)

    def _bind(self, target: ast.AST, names: set[str]) -> None:
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            self.attributes.add(target.attr)

    def _targets(self, node: ast.AST, names: Collection[str]) -> Iterable[ast.AST]:
        """The targets one statement binds to the application."""
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if self.reaches(node.value, names):
                    yield target
                elif isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    for item, value in zip(target.elts, node.value.elts, strict=False):
                        if self.reaches(value, names):
                            yield item
        elif isinstance(node, ast.AnnAssign) and (
            _annotates_application(node.annotation)
            or (node.value is not None and self.reaches(node.value, names))
        ):
            yield node.target
        elif isinstance(node, ast.NamedExpr) and self.reaches(node.value, names):
            yield node.target
        elif (
            isinstance(node, ast.withitem)
            and node.optional_vars is not None
            and self.reaches(node.context_expr, names)
        ):
            yield node.optional_vars

    def scope_names(self, scope: ast.AST, inherited: Collection[str]) -> set[str]:
        names = set(inherited)
        if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            arguments = scope.args
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
                *filter(None, (arguments.vararg, arguments.kwarg)),
            ):
                names.discard(argument.arg)
                if _annotates_application(argument.annotation):
                    names.add(argument.arg)
        while True:
            before = (len(names), len(self.attributes))
            for node in _scope_nodes(scope):
                for target in list(self._targets(node, names)):
                    self._bind(target, names)
            if before == (len(names), len(self.attributes)):
                return names

    def registry_access(self, node: ast.AST, names: Collection[str]) -> bool:
        """``<application>.handlers`` read, assigned or deleted, or reached by attribute name."""
        if isinstance(node, ast.Attribute):
            return node.attr == "handlers" and self.reaches(node.value, names)
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _ATTRIBUTE_FUNCTIONS
            and len(node.args) >= 2  # noqa: PLR2004
            and self.reaches(node.args[0], names)
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "handlers"
        )


def registry_accesses(module: ast.Module) -> list[int]:
    """Lines of a module that reach the application's handler registry, through any alias."""
    aliases = _ApplicationAliases()
    while True:
        known = set(aliases.attributes)
        lines: set[int] = set()
        pending: list[tuple[ast.AST, frozenset[str]]] = [(module, frozenset())]
        while pending:
            scope, inherited = pending.pop()
            names = aliases.scope_names(scope, inherited)
            # A class body's names are not visible to its methods, as in Python.
            visible = inherited if isinstance(scope, ast.ClassDef) else frozenset(names)
            for node in _scope_nodes(scope):
                if isinstance(node, _SCOPES):
                    pending.append((node, visible))
                elif aliases.registry_access(node, names):
                    lines.add(node.lineno)
        if aliases.attributes == known:
            return sorted(lines)


def _product_sources(root: Path) -> list[Path]:
    service = root / TG_BOT
    sources = []
    for directory, children, files in os.walk(service):
        parts = Path(directory).relative_to(service).parts
        # Prune environments, tests, wheels, caches and the drift-checked generated tree.
        children[:] = sorted(
            child
            for child in children
            if not child.startswith(".")
            and child != "__pycache__"
            and not (not parts and child in _SKIPPED_ROOTS)
            and (*parts, child) != ("src", "generated")
        )
        sources.extend(Path(directory) / name for name in sorted(files) if name.endswith(".py"))
    return sorted(sources)


def registration_bypasses(root: Path) -> list[Violation]:
    """Fail closed on any tg_bot code that registers handlers outside the core registry."""

    violations = []
    for path in _product_sources(root):
        relative = str(path.relative_to(root))
        try:
            module = ast.parse(path.read_text(), filename=str(path))
        except (OSError, SyntaxError) as error:
            violations.append(
                Violation(
                    code="registration_bypass",
                    owner=PRODUCT_OWNER,
                    location=Location(relative, getattr(error, "lineno", None)),
                    conflict=f"tg_bot source cannot be checked: {error}",
                    action="make the file valid Python so registration can be verified",
                )
            )
            continue
        seen: set[tuple[int, str]] = {(line, "handlers") for line in registry_accesses(module)}
        violations.extend(
            Violation(
                code="registration_bypass",
                owner=PRODUCT_OWNER,
                symbol="application.handlers",
                location=Location(relative, line),
                conflict=(
                    "the application's handlers are the core command registry; reading, "
                    "replacing or deleting them, also through an alias of the application, "
                    "replaces commands, access or the unknown-input reply"
                ),
                action=(
                    "remove the access to application.handlers; declare commands as "
                    f"{PRODUCT_COMMAND_TYPE}(name, handler) in COMMANDS of {PRODUCT_COMMANDS}"
                ),
            )
            for line, _ in sorted(seen)
        )
        for node in ast.walk(module):
            for name in _bypass_names(node):
                line = getattr(node, "lineno", None) or 0
                if name not in BYPASS_NAMES or (line, name) in seen:
                    continue
                seen.add((line, name))
                violations.append(
                    Violation(
                        code="registration_bypass",
                        owner=PRODUCT_OWNER,
                        symbol=name,
                        location=Location(relative, line or None),
                        conflict=(
                            f"{name} registers Telegram handlers or input outside the core "
                            "command registry"
                        ),
                        action=(
                            f"remove {name}; declare a command as {PRODUCT_COMMAND_TYPE}(name, "
                            f"handler) in COMMANDS of {PRODUCT_COMMANDS}; unknown commands and "
                            "text are answered by core"
                        ),
                    )
                )
    return violations


def _binding_command_lines(path: Path | None) -> dict[str, int]:
    node = _compose(path) if path is not None else None
    commands = None
    if isinstance(node, yaml.MappingNode):
        commands = next((value for key, value in node.value if key.value == "commands"), None)
    lines: dict[str, int] = {}
    if isinstance(commands, yaml.SequenceNode):
        for item in commands.value:
            if isinstance(item, yaml.MappingNode):
                for key, value in item.value:
                    if key.value == "command" and isinstance(value, yaml.ScalarNode):
                        lines.setdefault(value.value, value.start_mark.line + 1)
    return lines


def binding_claims(
    bindings: Mapping[str, Binding | BindingV2],
    sources: Mapping[str, Path] | None = None,
) -> tuple[list[CommandClaim], list[Violation]]:
    """Commands bound modules contribute, at their product binding path, in file order."""

    claims: list[CommandClaim] = []
    violations: list[Violation] = []
    for filename, binding in sorted(bindings.items()):
        relative = f"{TG_BOT}/bindings/{filename}"
        owner = f"package:{binding.package}"
        lines = _binding_command_lines((sources or {}).get(filename))
        for command in binding.commands:
            claims.append(
                CommandClaim(
                    command.command,
                    owner,
                    Location(relative, lines.get(command.command)),
                    command.kind,
                )
            )
    return claims, violations


def _timezone_conflict(binding: Binding | BindingV2, timezones: Mapping[str, str]) -> str:
    """Why a binding's timezone reference breaks the shared settings, or an empty string."""

    from framework.binding_product import binding_settings

    if binding.timezone is None:
        return ""
    key = binding.timezone.key
    try:
        binding_settings(binding)
    except BindingError as error:
        return str(error)
    if key in CORE_SETTINGS:
        return f"timezone reads the core setting {key!r}, which is not a timezone"
    if timezones and key not in timezones:
        other, where = next(iter(timezones.items()))
        return (
            f"timezone key {key!r} differs from {other!r} of {where}; all bindings share one "
            "product timezone key"
        )
    return ""


def binding_setting_violations(
    bindings: Mapping[str, Binding | BindingV2],
    sources: Mapping[str, Path],
    retained: Collection[str],
) -> list[Violation]:
    """Settings a binding reads must reference core language and one product timezone.

    Reuses ``binding_settings``. A violation in a retained product binding file is the
    product's to fix (owner ``product``); one in a package default is the package's.
    """

    from framework.bindings_v2 import BindingV2

    violations: list[Violation] = []
    timezones: dict[str, str] = {}
    for filename, binding in sorted(bindings.items()):
        relative = f"{TG_BOT}/bindings/{filename}"
        mine = filename in retained
        owner = PRODUCT_OWNER if mine else f"package:{binding.package}"
        source = sources.get(filename)
        node = _compose(source) if source is not None else None
        fix = f"edit {relative}" if mine else f"bind {binding.package} with a product override"
        if isinstance(binding, BindingV2) and binding.language.key != LANGUAGE_KEY:
            violations.append(
                Violation(
                    code="binding_language_owner",
                    owner=owner,
                    key=binding.language.key,
                    symbol=binding.package,
                    location=Location(relative, _key_lines(node, "language").get("key")),
                    conflict=(
                        f"binding reads language from {binding.language.key!r}; language is "
                        f"the core setting {LANGUAGE_KEY!r}"
                    ),
                    action=f"{fix}: reference core language as language: {{key: {LANGUAGE_KEY}}}",
                )
            )
        conflict = _timezone_conflict(binding, timezones)
        if conflict and binding.timezone is not None:
            violations.append(
                Violation(
                    code="binding_setting_conflict",
                    owner=owner,
                    key=binding.timezone.key,
                    symbol=binding.package,
                    location=Location(relative, _key_lines(node, "timezone").get("key")),
                    conflict=conflict,
                    action=(
                        f"{fix}: give timezone one product key, distinct from the core "
                        f"{LANGUAGE_KEY!r} setting"
                    ),
                )
            )
        elif binding.timezone is not None:
            timezones.setdefault(binding.timezone.key, relative)
    return violations


def _command_violation(claim: CommandClaim, claimed: dict[str, CommandClaim]) -> Violation | None:
    name = claim.command
    if len(name) > MAX_COMMAND_LENGTH or not COMMAND_PATTERN.fullmatch(name):
        return Violation(
            code="invalid_command",
            owner=claim.owner,
            command=name,
            symbol=claim.symbol,
            location=claim.location,
            conflict=f"/{name} is not a Telegram command name ([a-z][a-z0-9_]*, at most 32)",
            action="rename the command to lowercase letters, digits and underscores",
        )
    previous = claimed.get(name)
    if previous is not None:
        # Anchor the glue on the side the product author edits.
        edit, other = (previous, claim) if previous.owner == PRODUCT_OWNER else (claim, previous)
        if edit.owner == PRODUCT_OWNER:
            action = (
                f"rename the product command /{name} at {edit.location} (its "
                f"{PRODUCT_COMMAND_TYPE} entry) to an unused name and run make generate-from-spec"
            )
        else:
            module = edit.owner.removeprefix("package:")
            action = (
                f"bind {module} with `kit bind {module} --file <override>`, a product-owned "
                f"copy of its binding that renames /{name}"
            )
        return Violation(
            code="command_collision",
            owner=edit.owner,
            command=name,
            symbol=edit.symbol,
            location=edit.location,
            conflict=f"/{name} is claimed by both {other.owner} and {edit.owner}",
            action=action,
            other=other,
        )
    if name in RESERVED_COMMANDS and claim.owner != CORE_OWNER:
        return Violation(
            code="reserved_command",
            owner=claim.owner,
            command=name,
            symbol=claim.symbol,
            location=claim.location,
            conflict=f"/{name} is reserved by core",
            action=f"rename /{name} at {claim.location} to an unused name",
        )
    return None


def evaluate(
    root: Path,
    bindings: Mapping[str, Binding | BindingV2] | None = None,
    binding_sources: Mapping[str, Path] | None = None,
) -> HostContract:
    """Collect and resolve the product's core settings and command claims."""

    from framework.binding_product import binding_files

    contract = HostContract(
        backend=(root / "services/backend").is_dir(), tg_bot=(root / TG_BOT).is_dir()
    )
    contract.settings = dict.fromkeys(sorted(CORE_SETTINGS), CORE_OWNER)
    contract.violations.extend(manifest_setting_violations(root))
    if not contract.tg_bot:
        return contract
    if bindings is None:
        bindings = binding_files(root)
        binding_sources = {name: root / TG_BOT / "bindings" / name for name in bindings}
    sources = dict(binding_sources or {})
    product_bindings = root / TG_BOT / "bindings"
    # Retained: the binding is the product's own file, not a prospective package default.
    retained = {
        name
        for name, path in sources.items()
        if path.parent.resolve() == product_bindings.resolve() and path.name == name
    }
    builtins = [CommandClaim("start", CORE_OWNER, Location(CORE_COMMAND_SOURCE), "handle_start")]
    if contract.backend:
        builtins.append(
            CommandClaim("command", CORE_OWNER, Location(CORE_COMMAND_SOURCE), "handle_command")
        )
    product, product_violations = product_command_claims(root)
    modules, module_violations = binding_claims(bindings, sources)
    contract.violations.extend(product_violations)
    contract.violations.extend(registration_bypasses(root))
    contract.violations.extend(module_violations)
    contract.violations.extend(binding_setting_violations(bindings, sources, retained))
    claimed: dict[str, CommandClaim] = {}
    for claim in [*builtins, *product, *modules]:
        violation = _command_violation(claim, claimed)
        if violation is None:
            claimed[claim.command] = claim
            contract.commands.append(claim)
        else:
            contract.violations.append(violation)
    return contract


def render_registry(contract: HostContract) -> str:
    """Render the bot's command registry from a validated contract, deterministically."""

    contract.require_valid()
    environment = Environment(
        loader=FileSystemLoader(str(Path(__file__).parent / "templates" / "codegen")),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,  # noqa: S701
    )
    content = environment.get_template("commands.py.j2").render(
        commands=[
            # Paths only: moving a declaration within its file is not registry drift.
            {"command": claim.command, "owner": claim.owner, "source": claim.location.path}
            for claim in contract.commands
        ],
        language_key=LANGUAGE_KEY,
    )
    return (GENERATED_HEADER + content).rstrip() + "\n"


def registry_violation(root: Path, contract: HostContract) -> Violation | None:
    """Return a violation when the committed registry differs from the contract."""

    path = root / REGISTRY
    expected = render_registry(contract)
    try:
        actual = path.read_text()
    except OSError:
        actual = None
    if actual == expected:
        return None
    return Violation(
        code="registry_stale",
        owner=CORE_OWNER,
        location=Location(REGISTRY),
        conflict="the generated command registry does not match the declared commands",
        action="run `make generate-from-spec`; never edit the generated registry by hand",
    )


def write_registry(root: Path, contract: HostContract) -> Path | None:
    if not contract.tg_bot:
        return None
    path = root / REGISTRY
    content = render_registry(contract)
    if not path.is_file() or path.read_text() != content:
        atomic_write_text(path, content)
    return path


def check_product(root: Path, *, registry: bool = True) -> HostContract:
    """The hard product lint: violations and, optionally, registry drift fail."""

    contract = evaluate(root).require_valid()
    if registry and contract.tg_bot and (stale := registry_violation(root, contract)):
        raise HostContractError([stale])
    return contract


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m framework.host_contract",
        description="Check (or, with --write, regenerate) the core host contract.",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--write", action="store_true", help="regenerate the command registry")
    arguments = parser.parse_args(argv)
    root = arguments.root.resolve()
    try:
        if arguments.write:
            write_registry(root, evaluate(root).require_valid())
        contract = check_product(root)
    except HostContractError as error:
        print(error, file=sys.stderr)
        return 1
    names = ", ".join(f"/{claim.command} ({claim.owner})" for claim in contract.commands)
    print(f"Host contract PASSED: core settings {sorted(contract.settings)}; commands {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

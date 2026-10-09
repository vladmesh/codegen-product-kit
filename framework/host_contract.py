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
from collections.abc import Iterable, Mapping
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
    }
)
#: tg_bot paths that are not product handler code: environments, tests, wheels and the
#: generated tree, which product CI drift-checks against the generator instead.
_SKIPPED_ROOTS = frozenset({".venv", "tests", "packages"})

#: Violations the product author resolves by editing product-owned files.
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
    """Product service manifests must not declare a core-owned setting key."""

    violations = []
    for manifest in sorted((root / "services").glob("*/manifest.yaml")):
        relative = str(manifest.relative_to(root))
        try:
            data = yaml.safe_load(manifest.read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue  # The spec loader names an unreadable manifest itself.
        properties = (data.get("settings_schema") or {}).get("properties") or {}
        lines = _key_lines(_compose(manifest), "settings_schema", "properties")
        for key in sorted(CORE_SETTINGS.keys() & set(properties)):
            service = manifest.parent.name
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


def _product_command(node: ast.expr, location: Location) -> tuple[CommandClaim | None, str]:
    if not (
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == PRODUCT_COMMAND_TYPE)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == PRODUCT_COMMAND_TYPE)
        )
    ):
        return None, f"each COMMANDS entry must be a {PRODUCT_COMMAND_TYPE}(...) call"
    arguments = {
        **dict(zip(("name", "handler"), node.args, strict=False)),
        **{keyword.arg: keyword.value for keyword in node.keywords if keyword.arg},
    }
    name, handler = arguments.get("name"), arguments.get("handler")
    if len(node.args) > 2 or set(arguments) != {"name", "handler"}:  # noqa: PLR2004
        return None, f"{PRODUCT_COMMAND_TYPE} takes exactly a name and a handler"
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
        seen: set[tuple[int, str]] = set()
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

    from framework.bindings_v2 import BindingV2

    claims: list[CommandClaim] = []
    violations: list[Violation] = []
    for filename, binding in sorted(bindings.items()):
        relative = f"{TG_BOT}/bindings/{filename}"
        owner = f"package:{binding.package}"
        lines = _binding_command_lines((sources or {}).get(filename))
        if isinstance(binding, BindingV2) and binding.language.key != LANGUAGE_KEY:
            violations.append(
                Violation(
                    code="binding_language_owner",
                    owner=owner,
                    key=binding.language.key,
                    location=Location(relative),
                    conflict=(
                        f"binding reads language from {binding.language.key!r}; language is "
                        f"the core setting {LANGUAGE_KEY!r}"
                    ),
                    action=f"reference the core setting as language: {{key: {LANGUAGE_KEY}}}",
                )
            )
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
    builtins = [CommandClaim("start", CORE_OWNER, Location(CORE_COMMAND_SOURCE), "handle_start")]
    if contract.backend:
        builtins.append(
            CommandClaim("command", CORE_OWNER, Location(CORE_COMMAND_SOURCE), "handle_command")
        )
    product, product_violations = product_command_claims(root)
    modules, module_violations = binding_claims(bindings, binding_sources)
    contract.violations.extend(product_violations)
    contract.violations.extend(registration_bypasses(root))
    contract.violations.extend(module_violations)
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

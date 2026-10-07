"""Finite binding v1 data and admission against actual installed package contracts."""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, model_validator
import yaml

from framework.catalog import Catalog, CatalogFunction
from framework.component_matching import (
    argument_schema_matches,
    non_null_schema,
    primary_output_matches,
)
from framework.spec.actions import PackageAction
from framework.spec.packages import PackageManifest

if TYPE_CHECKING:
    from framework.bindings_v2 import BindingV2, ReplyV2

BINDING_V2 = 2

Reference = Annotated[
    str,
    Field(
        pattern=r"^\$(?:text|clock|timezone|preset\.at|(?:parsed|item|event|result)\.[a-z][a-z0-9_]*)$"
    ),
]


class BindingError(ValueError):
    """A binding cannot be fulfilled by the supplied contracts."""


class StrictModel(BaseModel):
    model_config = {"extra": "forbid"}


class DisplayField(StrictModel):
    source: Reference
    format: Literal["month_word"] | None = None


class Reply(StrictModel):
    parts: list[str | DisplayField] = Field(min_length=1)


class ActionCall(StrictModel):
    action: str
    args: dict[str, Reference]
    reply: Reply


class Offset(StrictModel):
    kind: Literal["offset"]
    seconds: Literal[300, 3600]


class WallTime(StrictModel):
    kind: Literal["wall_time"]
    days: Literal[1]
    time: Literal["09:00"]


class Preset(ActionCall):
    label: str = Field(min_length=1)
    time: Annotated[Offset | WallTime, Field(discriminator="kind")]


class OnEmpty(StrictModel):
    text: str = Field(min_length=1)
    context: Literal["original_text"]
    presets: list[Preset]

    @model_validator(mode="after")
    def exact_presets(self) -> "OnEmpty":
        if len({item.label for item in self.presets}) != len(self.presets):
            raise ValueError("duplicate callback preset labels")
        times = [item.time.model_dump() for item in self.presets]
        if times != [
            {"kind": "offset", "seconds": 300},
            {"kind": "offset", "seconds": 3600},
            {"kind": "wall_time", "days": 1, "time": "09:00"},
        ]:
            raise ValueError("presets must be 5 minutes, 1 hour, tomorrow 09:00, in that order")
        return self


class ParseCall(StrictModel):
    function: str
    text: Literal["$text"]
    lang: Literal["en"]
    now: Literal["$clock"]
    tz: Literal["$timezone"]


class ParsedCreate(ActionCall):
    kind: Literal["parsed_create"]
    command: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    help: str = Field(min_length=1)
    parse: ParseCall
    nonempty_text: Literal[True]
    on_invalid_text: str = Field(min_length=1)
    on_empty: OnEmpty


class Button(ActionCall):
    label: str = Field(min_length=1)


class ItemFilter(StrictModel):
    field: str
    equals: str


class ListedItems(StrictModel):
    kind: Literal["list"]
    command: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    help: str = Field(min_length=1)
    action: str
    args: dict[str, Reference]
    filter: ItemFilter
    reply_each: Reply
    on_none: str = Field(min_length=1)
    buttons: list[Button] = Field(min_length=1)


class EventBinding(StrictModel):
    event: str
    to: Reference
    reply: Reply


class TimezoneRequirement(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    scope: Literal["product"]
    required: Literal[True]
    format: Literal["x-iana-tz"]


class Binding(StrictModel):
    binding_version: Literal[1]
    package: str
    timezone: TimezoneRequirement
    commands: list[Annotated[ParsedCreate | ListedItems, Field(discriminator="kind")]]
    events: list[EventBinding]

    @model_validator(mode="after")
    def unique_names(self) -> "Binding":
        for label, names in (
            ("commands", [item.command for item in self.commands]),
            ("events", [item.event for item in self.events]),
        ):
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate {label}")
        return self


def load_binding(path: Path) -> "Binding | BindingV2":
    """Read data only; no library, runtime, channel or package import is executed."""
    try:
        from framework.bindings_v2 import BindingV2

        data = yaml.safe_load(path.read_text())
        model = (
            BindingV2
            if isinstance(data, dict) and data.get("binding_version") == BINDING_V2
            else Binding
        )
        return model.model_validate(data)
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise BindingError(f"BindingFormatError: invalid binding {path}: {error}") from error


def _field(schema: dict[str, Any], name: str) -> dict[str, Any]:
    if name not in schema.get("required", []) or name not in schema.get("properties", {}):
        raise BindingError(f"unknown or optional field {name!r}")
    return schema["properties"][name]


def _source(reference: str, contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    name, _, field = reference[1:].partition(".")
    if name not in contexts:
        raise BindingError(f"source {reference!r} unavailable in this context")
    return _field(contexts[name], field) if field else contexts[name]


def _action(name: str, manifest: PackageManifest) -> PackageAction:
    for action in manifest.actions:
        if name == f"{manifest.name}.{action.name}":
            return action
    raise BindingError(f"unknown action {name!r} in installed {manifest.name} {manifest.version}")


def _arguments(
    args: dict[str, str], schema: dict[str, Any], contexts: dict[str, dict[str, Any]]
) -> None:
    properties = schema["properties"]
    if args.keys() - properties.keys() or set(schema.get("required", [])) - args.keys():
        raise BindingError("unknown or missing required action/function arguments")
    for name, reference in args.items():
        if not argument_schema_matches(_source(reference, contexts), properties[name]):
            raise BindingError(f"incompatible argument {name!r} from {reference}")


def _reply(reply: "Reply | ReplyV2", contexts: dict[str, dict[str, Any]]) -> None:
    for part in reply.parts:
        if isinstance(part, DisplayField):
            schema = _source(part.source, contexts)
            target = (
                {"type": "string", "format": "date-time"}
                if part.format == "month_word"
                else {"type": "string"}
            )
            if not argument_schema_matches(schema, target):
                raise BindingError(f"incompatible display field {part.source!r}")


def _call(call: ActionCall, manifest: PackageManifest, contexts: dict[str, dict[str, Any]]) -> None:
    action = _action(call.action, manifest)
    _arguments(call.args, action.input, contexts)
    _reply(call.reply, {"result": action.output})


def _function(name: str, catalog: Catalog) -> CatalogFunction:
    for library in catalog.libraries:
        for function in library.functions:
            if name == f"{library.name}.{function.name}":
                return function
    raise BindingError(f"unknown library function {name!r}")


def _parsed_command(command: ParsedCreate, manifest: PackageManifest, catalog: Catalog) -> None:
    if command.parse.function != "textparse.when":
        raise BindingError("binding v1 supports only textparse.when")
    function = _function(command.parse.function, catalog)
    contexts = {
        "text": {"type": "string", "minLength": 1},
        "clock": {"type": "string", "format": "date-time"},
        "timezone": {"type": "string", "format": "x-iana-tz"},
    }
    parse_args = {key: getattr(command.parse, key) for key in ("text", "now", "tz")}
    _arguments(
        {**parse_args, "lang": "$language"},
        function.input,
        {**contexts, "language": {"type": "string", "const": command.parse.lang}},
    )
    parsed = non_null_schema(function.output)
    # The declared guard must reject empty parsed text before create. It proves minLength.
    parsed = {**parsed, "properties": dict(parsed.get("properties", {}))}
    rest = _field(parsed, "rest")
    if rest.get("type") != "string":
        raise BindingError("parsed rest must be a string")
    parsed["properties"]["rest"] = {**rest, "minLength": 1}
    action = _action(command.action, manifest)
    if command.args != {"text": "$parsed.rest", "remind_at": "$parsed.at"}:
        raise BindingError("parsed create must map rest/at to text/remind_at")
    if not primary_output_matches(function, action.input["properties"].get("remind_at", {})):
        raise BindingError("parser primary output is incompatible with remind_at")
    _call(command, manifest, {"parsed": parsed})
    for preset in command.on_empty.presets:
        if preset.action != command.action or preset.args != {
            "text": "$text",
            "remind_at": "$preset.at",
        }:
            raise BindingError("presets must create with original text and explicit preset instant")
        _call(
            preset,
            manifest,
            {
                "text": contexts["text"],
                "preset": {"properties": {"at": contexts["clock"]}, "required": ["at"]},
            },
        )


def _listed_command(command: ListedItems, manifest: PackageManifest) -> None:
    action = _action(command.action, manifest)
    _arguments(command.args, action.input, {})
    if action.output.get("type") != "array":
        raise BindingError("list action must return an array")
    item = action.output["items"]
    field = _field(item, command.filter.field)
    if not Draft202012Validator(field).is_valid(command.filter.equals):
        raise BindingError("invalid listed-item filter value")
    _reply(command.reply_each, {"item": item})
    if len({button.label for button in command.buttons}) != len(command.buttons):
        raise BindingError("duplicate callback button labels")
    for button in command.buttons:
        _call(button, manifest, {"item": item})


def validate_binding(
    binding: "Binding | BindingV2", manifest: PackageManifest, catalog: Catalog
) -> None:
    """Admit finite data against the supplied installed manifest, never newest catalog actions.

    Nullable parse results take on_empty exclusively. No action is invoked by validation;
    execution of these branches is a later generated-product responsibility.
    """
    from framework.bindings_v2 import BindingV2, validate_binding_v2

    if isinstance(binding, BindingV2):
        validate_binding_v2(binding, manifest)
        return
    if binding.package != manifest.name:
        raise BindingError("binding package does not match installed manifest")
    for command in binding.commands:
        if isinstance(command, ParsedCreate):
            _parsed_command(command, manifest, catalog)
        else:
            _listed_command(command, manifest)
    for event in binding.events:
        if event.event not in manifest.events.publishes:
            raise BindingError(f"unknown published event {event.event!r}")
        message = manifest.events.messages[event.event]
        if message.recipient is None or event.to != f"$event.{message.recipient}":
            raise BindingError("event target must reference its declared recipient")
        contexts = {"event": message.schema_data}
        if not argument_schema_matches(_source(event.to, contexts), {"type": "string"}):
            raise BindingError("event recipient must be a required string")
        _reply(event.reply, contexts)


def validate_product_timezone(value: str) -> None:
    """Later generators must supply one explicit product zone before admitting execution."""
    if (
        not value
        or value in {"localtime", "posixrules"}
        or value.startswith(("/", ".", "posix/", "right/"))
    ):
        raise BindingError("an explicit product IANA timezone is required")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise BindingError(f"invalid product IANA timezone {value!r}") from error

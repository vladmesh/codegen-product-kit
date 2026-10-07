"""Finite bilingual binding admission, separate from the pinned v1 grammar."""

from typing import Annotated, Literal

from jsonschema import Draft202012Validator
from pydantic import Field, model_validator

from framework.bindings import (
    BindingError,
    DisplayField,
    ItemFilter,
    Reference,
    StrictModel,
    TimezoneRequirement,
    _action,
    _arguments,
    _field,
    _reply,
    _source,
)
from framework.component_matching import argument_schema_matches
from framework.spec.packages import PackageManifest


class Text(StrictModel):
    ru: str = Field(min_length=1)
    en: str = Field(min_length=1)


class ReplyV2(StrictModel):
    parts: list[Text | DisplayField] = Field(min_length=1)


class CallV2(StrictModel):
    action: str
    args: dict[str, Reference]
    reply: ReplyV2


class ButtonV2(CallV2):
    label: Text


class TextCreate(CallV2):
    kind: Literal["text_create"]
    command: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    help: Text
    on_empty: Text
    on_invalid_text: Text
    on_error: dict[str, Text] = Field(default_factory=dict)


class ListV2(StrictModel):
    kind: Literal["list", "show"]
    command: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    help: Text
    action: str
    args: dict[str, Reference]
    filter: ItemFilter | None = None
    reply_each: ReplyV2
    on_none: Text
    buttons: list[ButtonV2] = Field(default_factory=list)

    @model_validator(mode="after")
    def finite_show(self) -> "ListV2":
        if self.kind == "show" and (self.filter is not None or self.buttons):
            raise ValueError("show has no filter or buttons")
        for locale in ("ru", "en"):
            labels = [getattr(button.label, locale) for button in self.buttons]
            if len(set(labels)) != len(labels):
                raise ValueError("duplicate callback button labels")
        return self


class EventV2(StrictModel):
    event: str
    to: Reference
    reply: ReplyV2


class LanguageRequirement(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    scope: Literal["product"]
    values: tuple[Literal["ru"], Literal["en"]]


class BindingV2(StrictModel):
    binding_version: Literal[2]
    package: str
    language: LanguageRequirement
    timezone: TimezoneRequirement | None = None
    commands: list[Annotated[TextCreate | ListV2, Field(discriminator="kind")]]
    events: list[EventV2]

    @model_validator(mode="after")
    def unique_names(self) -> "BindingV2":
        for label, names in (
            ("commands", [item.command for item in self.commands]),
            ("events", [item.event for item in self.events]),
        ):
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate {label}")
        return self


def _localized_reply(reply: ReplyV2, contexts: dict, binding: BindingV2) -> None:
    # DisplayField is shared with v1, so the same conservative schema matcher applies.
    _reply(reply, contexts)
    if binding.timezone is None and any(
        isinstance(part, DisplayField) and part.format == "month_word" for part in reply.parts
    ):
        raise BindingError("date display requires a product timezone declaration")


def _call_v2(call: CallV2, manifest: PackageManifest, contexts: dict, binding: BindingV2):
    action = _action(call.action, manifest)
    _arguments(call.args, action.input, contexts)
    _localized_reply(call.reply, {"result": action.output}, binding)
    return action


def _list_v2(command: ListV2, manifest: PackageManifest, binding: BindingV2) -> None:
    action = _action(command.action, manifest)
    _arguments(command.args, action.input, {})
    if action.output.get("type") != "array":
        raise BindingError("list/show action must return an array")
    item = action.output["items"]
    if command.filter is not None:
        field = _field(item, command.filter.field)
        if not Draft202012Validator(field).is_valid(command.filter.equals):
            raise BindingError("invalid listed-item filter value")
    _localized_reply(command.reply_each, {"item": item}, binding)
    for button in command.buttons:
        _call_v2(button, manifest, {"item": item}, binding)


def validate_binding_v2(binding: BindingV2, manifest: PackageManifest) -> None:
    if binding.package != manifest.name:
        raise BindingError("binding package does not match installed manifest")
    for command in binding.commands:
        if isinstance(command, TextCreate):
            if len(command.args) != 1 or set(command.args.values()) != {"$text"}:
                raise BindingError("text_create maps $text to exactly one action argument")
            action = _call_v2(
                command, manifest, {"text": {"type": "string", "minLength": 1}}, binding
            )
            if command.on_error.keys() - set(action.errors):
                raise BindingError("unknown declared action error code")
        else:
            _list_v2(command, manifest, binding)
    for event in binding.events:
        if event.event not in manifest.events.publishes:
            raise BindingError(f"unknown published event {event.event!r}")
        message = manifest.events.messages[event.event]
        if message.recipient is None or event.to != f"$event.{message.recipient}":
            raise BindingError("event target must reference its declared recipient")
        contexts = {"event": message.schema_data}
        if not argument_schema_matches(_source(event.to, contexts), {"type": "string"}):
            raise BindingError("event recipient must be a required string")
        _localized_reply(event.reply, contexts, binding)

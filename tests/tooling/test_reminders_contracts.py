"""Real action/OpenAPI agreement, binding admission and independent release preservation."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha1
from pathlib import Path
from typing import Any

from pydantic import ValidationError
import pytest
import yaml

from framework.action_contracts import verify_action_openapi
from framework.bindings import (
    Binding,
    BindingError,
    load_binding,
    validate_binding,
    validate_product_timezone,
)
from framework.catalog import load_catalog
from framework.component_matching import argument_schema_matches, primary_output_matches
from framework.spec.packages import (
    PackageManifestError,
    load_package_manifest,
    parse_package_manifest,
)
from tests.tooling.reminders_openapi import real_openapi

ROOT = Path(__file__).parents[2]
PACKAGE = ROOT / "packages/codegen-kit-reminders/codegen_kit_reminders"


def _manifest() -> dict[str, Any]:
    return yaml.safe_load((PACKAGE / "package.yaml").read_text())


def _binding() -> dict[str, Any]:
    return yaml.safe_load((PACKAGE / "bindings/default.yaml").read_text())


def test_real_fastapi_openapi_agrees_with_every_public_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = parse_package_manifest(_manifest())
    document = real_openapi(monkeypatch)
    verify_action_openapi(manifest, document)
    assert [action.name for action in manifest.actions] == ["create", "list", "cancel"]
    for action in manifest.actions:
        assert "user_ref" not in action.input["properties"]
    create = manifest.actions[0]
    assert create.input["properties"]["text"] == {"type": "string", "minLength": 1}
    assert set(create.input["required"]) == {"text", "remind_at"}
    assert manifest.actions[2].input["properties"]["reminder_id"] == {
        "type": "string",
        "format": "uuid",
    }
    assert set(create.output["properties"]) == {
        "id",
        "user_ref",
        "text",
        "remind_at",
        "state",
        "created_at",
        "cancelled_at",
        "due_at",
        "emitted_at",
    }


@pytest.mark.parametrize("drift", ["constraint", "format", "required", "path", "method", "output"])
def test_real_openapi_comparison_detects_deliberate_drift(
    monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    data = _manifest()
    create = data["actions"][0]
    if drift == "constraint":
        create["input"]["properties"]["text"]["minLength"] = 2
    elif drift == "format":
        create["input"]["properties"]["remind_at"]["format"] = "duration"
    elif drift == "required":
        create["input"]["required"] = ["text"]
    elif drift == "path":
        create["operation"]["path"] = "/create"
    elif drift == "method":
        create["operation"]["method"] = "PATCH"
    else:
        create["output"]["properties"]["id"]["format"] = "date-time"
    with pytest.raises(ValueError, match="OpenAPI"):
        verify_action_openapi(parse_package_manifest(data), real_openapi(monkeypatch))


@pytest.mark.parametrize(
    "bad",
    [
        "name",
        "method",
        "path",
        "path-argument",
        "input",
        "output",
        "unknown",
        "duplicate",
        "ref",
        "resource",
    ],
)
def test_manifest_refuses_malformed_action_metadata(bad: str) -> None:
    data = _manifest()
    action = data["actions"][0]
    if bad == "name":
        action["name"] = "Create.Bad"
    elif bad == "method":
        action["operation"]["method"] = "post"
    elif bad == "path":
        action["operation"]["path"] = "/../create?bad"
    elif bad == "path-argument":
        action["operation"]["path"] = "/{missing}"
    elif bad == "input":
        action["input"] = {"type": "array", "items": {"type": "string"}}
    elif bad == "output":
        action["output"] = {"type": "datetime"}
    elif bad == "unknown":
        action["executor"] = "eval"
    elif bad == "duplicate":
        data["actions"].append(deepcopy(action))
    elif bad == "ref":
        action["output"] = {"$ref": "https://example.com/schema"}
    else:
        data["default_binding"] = "codegen_kit_reminders:/etc/passwd"
    with pytest.raises(PackageManifestError):
        parse_package_manifest(data)


@pytest.mark.parametrize("bad", ["unknown", "non-string", "optional", "nullable", "unknown-key"])
def test_manifest_refuses_invalid_event_recipient(bad: str) -> None:
    data = _manifest()
    message = data["events"]["messages"]["reminders.due"]
    if bad == "unknown":
        message["recipient"] = "missing"
    elif bad == "optional":
        message["schema"]["required"].remove("user_ref")
    elif bad == "non-string":
        message["schema"]["properties"]["user_ref"]["type"] = "integer"
    elif bad == "nullable":
        message["schema"]["properties"]["user_ref"]["type"] = ["string", "null"]
    else:
        message["recipient_policy"] = "guess"
    with pytest.raises(PackageManifestError):
        parse_package_manifest(data)


def test_real_source_catalog_binding_and_primary_output_agree() -> None:
    manifest = parse_package_manifest(_manifest())
    catalog = load_catalog(ROOT / "packages/catalog.yaml")
    package = catalog.get("reminders")
    assert (
        package.default_binding
        == manifest.default_binding
        == "codegen_kit_reminders:bindings/default.yaml"
    )
    for actual, declared in zip(manifest.actions, package.actions, strict=True):
        assert (actual.name, actual.input, actual.output, actual.summary, actual.operation) == (
            declared.name,
            declared.input,
            declared.output,
            declared.summary,
            declared.operation,
        )
    assert primary_output_matches(
        catalog.libraries[0].functions[0], manifest.actions[0].input["properties"]["remind_at"]
    )
    binding = load_binding(PACKAGE / "bindings/default.yaml")
    validate_binding(binding, manifest, catalog)
    assert [command.command for command in binding.commands] == ["remind", "reminders"]
    assert binding.commands[1].filter.model_dump() == {"field": "state", "equals": "scheduled"}
    assert binding.commands[1].buttons[0].args == {"reminder_id": "$item.id"}
    assert binding.events[0].to == "$event.user_ref"


@pytest.mark.parametrize(
    "bad",
    [
        "unknown-key",
        "duplicate-command",
        "action",
        "function",
        "argument",
        "missing",
        "incompatible",
        "parsed-field",
        "result-field",
        "optional-result",
        "event",
        "event-field",
        "recipient",
        "filter",
        "null-branch",
        "guard",
        "preset-value",
        "preset-missing",
        "preset-args",
        "preset-context",
        "source-eval",
        "language",
        "clock",
        "timezone",
        "format",
        "preset-action",
    ],
)
def test_binding_refuses_unfulfillable_or_unrecognized_data(bad: str) -> None:  # noqa: C901, PLR0912
    # Each mutation isolates a distinct admission refusal in the real shipped resource.
    data = _binding()
    create, listed = data["commands"]
    event = data["events"][0]
    # YAML anchors are data reuse only; remove shared reply aliases for independent mutations.
    create["reply"] = deepcopy(create["reply"])
    if bad == "unknown-key":
        data["executor"] = "python"
    elif bad == "duplicate-command":
        listed["command"] = create["command"]
    elif bad == "action":
        create["action"] = "reminders.missing"
    elif bad == "function":
        create["parse"]["function"] = "textparse.duration"
    elif bad == "argument":
        create["args"]["user_ref"] = "$text"
    elif bad == "missing":
        del create["args"]["remind_at"]
    elif bad == "incompatible":
        listed["buttons"][0]["args"]["reminder_id"] = "$item.text"
    elif bad == "parsed-field":
        create["args"]["text"] = "$parsed.missing"
    elif bad in {"result-field", "optional-result"}:
        create["reply"]["parts"][1]["source"] = "$result." + (
            "missing" if bad == "result-field" else "cancelled_at"
        )
    elif bad == "event":
        event["event"] = "reminders.missing"
    elif bad == "event-field":
        event["reply"]["parts"][1]["source"] = "$event.missing"
    elif bad == "recipient":
        event["to"] = "$event.text"
    elif bad == "filter":
        listed["filter"]["equals"] = "pending"
    elif bad == "null-branch":
        del create["on_empty"]
    elif bad == "guard":
        create["nonempty_text"] = False
    elif bad == "preset-value":
        create["on_empty"]["presets"][0]["time"]["seconds"] = 60
    elif bad == "preset-missing":
        create["on_empty"]["presets"].pop()
    elif bad == "preset-args":
        create["on_empty"]["presets"][0]["args"]["text"] = "$parsed.rest"
    elif bad == "preset-context":
        create["on_empty"]["context"] = "parsed_text"
    elif bad == "source-eval":
        create["args"]["text"] = "eval('text')"
    elif bad in {"language", "clock", "timezone"}:
        key = {"language": "lang", "clock": "now", "timezone": "tz"}[bad]
        create["parse"][key] = "guessed"
    elif bad == "preset-action":
        create["on_empty"]["presets"][0]["action"] = "reminders.cancel"
    else:
        create["reply"]["parts"][1]["format"] = "%d.%m"
    with pytest.raises((ValidationError, BindingError)):
        validate_binding(
            Binding.model_validate(data),
            parse_package_manifest(_manifest()),
            load_catalog(ROOT / "packages/catalog.yaml"),
        )


@pytest.mark.parametrize("version", ["0.3.0", "0.4.0"])
def test_released_manifests_confer_no_new_actions_or_binding(version: str) -> None:
    source = ROOT / "tests/fixtures/reminders_releases" / version
    manifest = load_package_manifest(source / "codegen_kit_reminders/package.yaml")
    assert manifest.version == version
    assert manifest.actions == [] and manifest.default_binding is None
    assert manifest.events.messages["reminders.due"].recipient is None
    with pytest.raises(BindingError, match="unknown action"):
        validate_binding(
            load_binding(PACKAGE / "bindings/default.yaml"),
            manifest,
            load_catalog(ROOT / "packages/catalog.yaml"),
        )
    assert (
        _git_tree(source)
        == {
            "0.3.0": "5d3b157eb25a9faacab9f7096a722f02b5053cd4",
            "0.4.0": "2414be2c751a5a1407665179801ffcd084572fa5",
        }[version]
    )


def _git_object(kind: str, data: bytes) -> bytes:
    return sha1(
        kind.encode() + b" " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False
    ).digest()


def _git_tree(path: Path) -> str:
    children = [item for item in path.iterdir() if item.name != "__pycache__"]
    children.sort(key=lambda item: item.name + ("/" if item.is_dir() else ""))
    contents = b""
    for item in children:
        mode = b"40000" if item.is_dir() else b"100644"
        digest = (
            bytes.fromhex(_git_tree(item))
            if item.is_dir()
            else _git_object("blob", item.read_bytes())
        )
        contents += mode + b" " + item.name.encode() + b"\0" + digest
    return _git_object("tree", contents).hex()


@pytest.mark.parametrize("zone", ["", "guessed", "/etc/localtime", "../UTC"])
def test_explicit_product_timezone_refuses_missing_or_invalid_configuration(zone: str) -> None:
    with pytest.raises(BindingError):
        validate_product_timezone(zone)


def test_explicit_product_timezone_is_admitted() -> None:
    validate_product_timezone("America/New_York")


@pytest.mark.parametrize("field", ["at", "rest", "now"])
def test_binding_admission_uses_actual_function_argument_and_result_schemas(field: str) -> None:
    catalog = load_catalog(ROOT / "packages/catalog.yaml")
    library = catalog.libraries[0]
    function = library.functions[0]
    signature = deepcopy(function.output if field != "now" else function.input)
    signature["properties"][field] = {"type": "integer"}
    function = replace(function, **{"input" if field == "now" else "output": signature})
    library = replace(library, functions=(function,))
    with pytest.raises(BindingError):
        validate_binding(
            load_binding(PACKAGE / "bindings/default.yaml"),
            parse_package_manifest(_manifest()),
            replace(catalog, libraries=(library,)),
        )


def test_nonempty_text_guard_is_required_for_the_real_action_schema() -> None:
    target = parse_package_manifest(_manifest()).actions[0].input["properties"]["text"]
    assert not argument_schema_matches({"type": "string"}, target)
    assert argument_schema_matches({"type": "string", "minLength": 1}, target)

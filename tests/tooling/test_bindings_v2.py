"""Finite v2 admission and pinned v1 emission."""

from copy import deepcopy
from pathlib import Path
import shutil
import sys

from pydantic import ValidationError
import pytest
import yaml

from framework.binding_product import BindingPlan
from framework.bindings import BindingError, load_binding, validate_binding
from framework.bindings_v2 import BindingV2
from framework.catalog import bundled_catalog
from framework.generators.bindings import BindingsGenerator
from framework.spec.packages import load_package_manifest

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/fixtures/binding_v2_package/binding_v2_package"


def test_v2_admits_synthetic_contract():
    binding = load_binding(FIXTURE / "default.yaml")
    assert isinstance(binding, BindingV2)
    validate_binding(binding, load_package_manifest(FIXTURE / "package.yaml"), bundled_catalog())


@pytest.mark.parametrize(
    "bad",
    [
        "key",
        "missing-locale",
        "extra-locale",
        "literal",
        "action",
        "event",
        "field",
        "schema",
        "duplicate-command",
        "duplicate-event",
        "duplicate-label-ru",
        "duplicate-label-en",
        "branch",
        "error-code",
        "timezone",
        "show-button",
        "filter",
        "extra-language",
        "user-language",
    ],
)
def test_v2_refuses_invalid_data_and_contracts(bad):  # noqa: C901, PLR0912
    data = yaml.safe_load((FIXTURE / "default.yaml").read_text())
    manifest = load_package_manifest(FIXTURE / "package.yaml")
    create, listed, show = data["commands"]
    if bad == "key":
        create["unknown"] = True
    elif bad == "missing-locale":
        create["help"].pop("ru")
    elif bad == "extra-locale":
        create["reply"]["parts"][0]["fr"] = "Bonjour"
    elif bad == "literal":
        create["reply"]["parts"][0] = "English only"
    elif bad == "action":
        create["action"] = "binding-notes.unknown"
    elif bad == "event":
        data["events"][0]["event"] = "binding-notes.unknown"
    elif bad == "field":
        listed["reply_each"]["parts"][1]["source"] = "$item.unknown"
    elif bad == "schema":
        manifest.actions[0].input["properties"]["text"] = {"type": "integer"}
    elif bad == "duplicate-command":
        listed["command"] = create["command"]
    elif bad == "duplicate-event":
        data["events"].append(deepcopy(data["events"][0]))
    elif bad.startswith("duplicate-label"):
        second = deepcopy(listed["buttons"][0])
        locale = bad.rsplit("-", 1)[1]
        second["label"]["en" if locale == "ru" else "ru"] = "different"
        listed["buttons"].append(second)
    elif bad == "branch":
        create["args"]["text"] = "$item.text"
    elif bad == "error-code":
        create["on_error"]["unknown"] = {"ru": "ошибка", "en": "error"}
    elif bad == "timezone":
        manifest.actions[0].output["properties"]["text"] = {"type": "string", "format": "date-time"}
        create["reply"]["parts"][1]["format"] = "month_word"
    elif bad == "show-button":
        show["buttons"] = listed["buttons"]
    elif bad == "filter":
        listed["filter"] = {"field": "state", "equals": "unknown"}
    elif bad == "extra-language":
        data["language"]["values"].append("fr")
    else:
        data["language"]["scope"] = "user"
    with pytest.raises((ValidationError, BindingError)):
        validate_binding(BindingV2.model_validate(data), manifest, bundled_catalog())


def v1_plan():
    package = ROOT / "packages/codegen-kit-reminders/codegen_kit_reminders"
    manifest = load_package_manifest(package / "package.yaml")
    actions = {}
    for action in manifest.actions:
        data = action.model_dump(exclude={"errors"})
        data["operation"]["path"] = manifest.http.prefix + action.operation.path
        actions[f"reminders.{action.name}"] = data
    return BindingPlan(
        [load_binding(package / "bindings/default.yaml")],
        actions,
        {"reminders.due": manifest.events.messages["reminders.due"].schema_data},
        {"textparse": "codegen_kit_textparse"},
    )


def test_v1_generated_bytes_match_pre_v2_baseline(tmp_path):
    (tmp_path / "services/tg_bot").mkdir(parents=True)
    # Pin the same formatter/config used by product generation, not PATH-dependent output.
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/ruff").symlink_to(Path(sys.executable).with_name("ruff"))
    shutil.copy(ROOT / "template/ruff.toml", tmp_path / "ruff.toml")
    BindingsGenerator(None, tmp_path, v1_plan()).generate()
    for name in ("bindings.py", "binding_relay.py"):
        actual = tmp_path / "services/tg_bot/src/generated" / name
        assert (
            actual.read_bytes()
            == (ROOT / "tests/fixtures/binding_v1_baseline" / (name + ".txt")).read_bytes()
        )


@pytest.mark.parametrize("locale", ["ru", "en"])
@pytest.mark.parametrize("extra", [False, True])
@pytest.mark.parametrize(
    "path",
    [
        ("commands", 0, "help"),
        ("commands", 0, "on_empty"),
        ("commands", 0, "on_invalid_text"),
        ("commands", 0, "reply", "parts", 0),
        ("commands", 0, "on_error", "not_found"),
        ("commands", 1, "on_none"),
        ("commands", 1, "buttons", 0, "label"),
        ("commands", 1, "buttons", 0, "reply", "parts", 0),
        ("events", 0, "reply", "parts", 0),
    ],
)
def test_every_text_position_requires_exactly_both_locales(tmp_path, path, locale, extra):
    data = yaml.safe_load((FIXTURE / "default.yaml").read_text())
    text = data
    for key in path:
        text = text[key]
    if extra:
        text["fr"] = "Texte"
    else:
        text.pop(locale)
    file = tmp_path / "binding.yaml"
    file.write_text(yaml.safe_dump(data, allow_unicode=True))
    with pytest.raises(BindingError, match="BindingFormatError"):
        load_binding(file)


def test_action_error_codes_are_finite_unique_identifiers():
    from framework.spec.actions import PackageAction

    action = load_package_manifest(FIXTURE / "package.yaml").actions[0].model_dump()
    for errors in (["not_found", "not_found"], ["Any Expression!"], [""]):
        with pytest.raises(ValidationError):
            PackageAction.model_validate({**action, "errors": errors})

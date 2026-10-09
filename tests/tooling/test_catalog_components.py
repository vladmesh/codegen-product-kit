"""Additive v1 metadata and actual released-reader compatibility, entirely offline."""

from dataclasses import asdict, replace
from hashlib import sha1
import importlib.util
from pathlib import Path
import sys
from typing import Any

import pytest
import yaml

from framework.catalog import (
    CatalogFunction,
    DuplicateCatalogComponentError,
    DuplicateCatalogVersionError,
    InvalidCatalogEntryError,
    load_catalog,
    parse_catalog,
)
from framework.component_matching import primary_output_matches

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/catalog"


def _document() -> dict[str, Any]:
    return yaml.safe_load((FIXTURES / "components.yaml").read_text())


def test_additive_models_and_missing_metadata() -> None:
    catalog = load_catalog(FIXTURES / "components.yaml")
    assert catalog.get("reminders").actions[0].name == "create"
    assert catalog.get("reminders").recommended_with[0].library == "textparse"
    assert catalog.libraries[0].module == "codegen_kit_textparse"
    assert catalog.libraries[0].functions[0].value == "at"
    assert catalog.get_installable("synthetic-extension").extends.package == "reminders"
    document = yaml.safe_load((ROOT / "packages/catalog.yaml").read_text())
    del document["libraries"], document["extensions"]
    # Construct a pre-library catalog: recommendations cannot reference absent libraries.
    del document["packages"][0]["recommended_with"]
    del document["packages"][0]["actions"], document["packages"][0]["default_binding"]
    legacy = parse_catalog(yaml.safe_dump(document))
    assert legacy.libraries == legacy.extensions == ()
    assert legacy.get("reminders").actions == ()
    assert legacy.get("reminders").default_binding is None


@pytest.mark.parametrize("misplaced", ["moved-extension", "malformed", "null"])
def test_packages_reject_any_extends_field_at_the_list_boundary(misplaced: str) -> None:
    document = _document()
    if misplaced == "moved-extension":
        document["packages"].append(document["extensions"].pop())
        index, name = 1, "synthetic-extension"
    else:
        document["packages"][0]["extends"] = (
            "not a parent declaration" if misplaced == "malformed" else None
        )
        index, name = 0, "reminders"
    with pytest.raises(InvalidCatalogEntryError) as raised:
        parse_catalog(yaml.safe_dump(document))
    assert str(raised.value) == (
        f"InvalidCatalogEntryError: packages[{index}] {name!r} has misplaced 'extends'; "
        "move the record to top-level 'extensions'"
    )


def test_package_list_boundary_still_accepts_unknown_additive_keys() -> None:
    document = _document()
    document["packages"][0]["future_metadata"] = {"example": True}
    catalog = parse_catalog(yaml.safe_dump(document))
    assert catalog.get("reminders").name == "reminders"
    assert catalog.get_installable("synthetic-extension").extends.package == "reminders"


@pytest.mark.parametrize("source", [ROOT / "packages/catalog.yaml", FIXTURES / "components.yaml"])
def test_actual_0_7_1_loader_preserves_package_fields_and_selection(source: Path) -> None:
    path = FIXTURES / "released_0_7_1.py"
    contents = path.read_bytes()
    blob = b"blob " + str(len(contents)).encode() + b"\0" + contents
    assert (
        sha1(blob, usedforsecurity=False).hexdigest() == "d48db55ceefff9743e4f04ac952ca676192a8181"
    )
    spec = importlib.util.spec_from_file_location("released_catalog_0_7_1", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        catalog = module.parse_catalog(source.read_text())
        assert catalog.format_version == 1
        expected_names = (
            ["reminders", "tg-channels"]
            if source == ROOT / "packages/catalog.yaml"
            else ["reminders"]
        )
        assert [item.name for item in catalog.packages] == expected_names
        assert not hasattr(catalog, "libraries") and not hasattr(catalog, "extensions")
        reminders = catalog.get("reminders")
        modern = load_catalog(source).get("reminders")
        modern_fields = asdict(modern)
        assert asdict(reminders) == {key: modern_fields[key] for key in asdict(reminders)}
        assert reminders.distribution == "codegen-kit-reminders"
        assert reminders.path == "packages/codegen-kit-reminders"
        assert reminders.summary and reminders.capabilities
        assert reminders.settings[0].name == "reminder_owner_ref"
        assert reminders.environment[0].name == "REDIS_URL"
        assert reminders.environment[0].required is True
        assert reminders.select("2.0.0").version == "0.3.0"
        assert reminders.select("2.1.0").version == "0.4.0"
        assert reminders.select("2.1.0").requires_core == ">=2.1,<3"
        assert reminders.select("2.1.0").tag == "packages/reminders/v0.4.0"
        assert modern.select("2.0.0").version == "0.3.0"
        assert modern.select("2.1.0").version == "0.4.0"
        if source == ROOT / "packages/catalog.yaml":
            assert reminders.select("2.2.0").version == modern.select("2.2.0").version == "0.5.0"
            channels = catalog.get("tg-channels")
            assert channels.select("2.4.0").version == "0.1.2"
            with pytest.raises(module.IncompatibleCatalogVersionError):
                channels.select("2.3.0")
    finally:
        del sys.modules[spec.name]


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("libraries", "functions", "bad", "functions"),
        ("libraries", "module", None, "module"),
        ("libraries", "module", "bad-module", "module"),
        ("extensions", "extends", {"package": "missing", "versions": ">=1"}, "unknown parent"),
        ("extensions", "extends", {"package": "reminders", "versions": "latest"}, "specifier"),
        ("packages", "default_binding", "not-a-resource", "default_binding"),
        ("packages", "recommended_with", [{"library": "textparse"}], "why"),
    ],
)
def test_invalid_metadata_is_named(section: str, field: str, value: Any, message: str) -> None:
    document = _document()
    document[section][0][field] = value
    with pytest.raises(InvalidCatalogEntryError, match=message):
        parse_catalog(yaml.safe_dump(document))


@pytest.mark.parametrize("section", ["libraries", "extensions"])
def test_duplicate_component_names_and_versions(section: str) -> None:
    document = _document()
    document[section].append(document[section][0])
    with pytest.raises(DuplicateCatalogComponentError):
        parse_catalog(yaml.safe_dump(document))
    document = _document()
    versions = document[section][0]["versions"]
    versions.append(versions[0])
    with pytest.raises(DuplicateCatalogVersionError):
        parse_catalog(yaml.safe_dump(document))


@pytest.mark.parametrize("mutation", ["schema", "primary", "duplicate", "range", "identity"])
def test_invalid_function_and_component_contracts(mutation: str) -> None:
    document = _document()
    library = document["libraries"][0]
    function = library["functions"][0]
    if mutation == "schema":
        function["output"]["properties"]["at"]["type"] = "datetime"
    elif mutation == "primary":
        function["value"] = "missing"
    elif mutation == "duplicate":
        library["functions"].append(function)
    elif mutation == "range":
        library["versions"][0]["requires_python"] = "latest"
    else:
        library["distribution"] = "codegen_kit_reminders"
    with pytest.raises((InvalidCatalogEntryError, DuplicateCatalogComponentError)):
        parse_catalog(yaml.safe_dump(document))


@pytest.mark.parametrize(
    "mutation",
    ["boolean", "optional-primary", "undeclared", "duplicate-action", "unknown-recommendation"],
)
def test_invalid_schema_shapes_and_metadata_references(mutation: str) -> None:
    document = _document()
    function = document["libraries"][0]["functions"][0]
    package = document["packages"][0]
    if mutation == "boolean":
        function["output"]["properties"]["at"] = True
    elif mutation == "optional-primary":
        function["output"]["required"] = ["rest"]
    elif mutation == "undeclared":
        package["actions"][0]["input"]["required"].append("missing")
    elif mutation == "duplicate-action":
        package["actions"].append(package["actions"][0])
    else:
        package["recommended_with"][0]["library"] = "missing"
    with pytest.raises(InvalidCatalogEntryError):
        parse_catalog(yaml.safe_dump(document))


def _function(primary: dict[str, Any]) -> CatalogFunction:
    return CatalogFunction(
        "test",
        {"type": "object", "properties": {}},
        {
            "type": "object",
            "properties": {"value": primary},
            "required": ["value"],
        },
        "value",
    )


def test_english_when_primary_matches_reminders_and_never_searches_secondary_fields() -> None:
    catalog = load_catalog(ROOT / "packages/catalog.yaml")
    function = catalog.libraries[0].functions[0]
    parameter = catalog.get("reminders").actions[0].input["properties"]["remind_at"]
    assert primary_output_matches(function, parameter)
    assert primary_output_matches(function, dict(parameter, title="Time", default="ignored"))
    assert not primary_output_matches(replace(function, value="rest"), parameter)
    assert not primary_output_matches(function, {"type": "string"})
    duration = _function({"type": "string", "format": "duration"})
    assert not primary_output_matches(duration, parameter)
    recurrence = replace(
        duration,
        output={
            "type": "object",
            "properties": {
                "value": {"type": "string", "format": "x-rrule"},
                "first": parameter,
            },
            "required": ["value", "first"],
        },
    )
    assert not primary_output_matches(recurrence, parameter)
    nullable = replace(
        function,
        output={
            "anyOf": [
                dict(function.output, type="object"),
                {"type": "null"},
            ]
        },
    )
    assert primary_output_matches(nullable, parameter)
    assert primary_output_matches(
        replace(nullable, output={"oneOf": nullable.output["anyOf"]}), parameter
    )


@pytest.mark.parametrize(
    ("source", "target", "matches"),
    [
        ({"type": "string", "const": "en"}, {"type": "string", "enum": ["en", "fr"]}, True),
        ({"type": "string", "enum": ["en", "fr"]}, {"type": "string", "const": "en"}, False),
        ({"type": "string", "const": "en"}, {"type": "string", "const": "fr"}, False),
        ({"type": "string"}, {"type": "string"}, False),
        (
            {"type": "string", "format": "duration"},
            {"type": "string", "format": "date-time"},
            False,
        ),
        ({"type": "integer", "enum": [1]}, {"type": "string", "enum": ["1"]}, False),
        (
            {"type": "string", "format": "uuid", "maxLength": 36},
            {"type": "string", "format": "uuid"},
            False,
        ),
    ],
)
def test_directional_semantic_matching(source: dict, target: dict, matches: bool) -> None:
    assert primary_output_matches(_function(source), target) is matches


def test_array_object_and_union_matching() -> None:
    instant = {"type": "string", "format": "date-time"}
    array = {"type": "array", "items": instant}
    assert primary_output_matches(_function(array), array)
    assert not primary_output_matches(
        _function(array), {"type": "array", "items": {"type": "string", "format": "duration"}}
    )
    obj = {
        "type": "object",
        "properties": {"at": instant},
        "required": ["at"],
        "additionalProperties": False,
    }
    assert primary_output_matches(_function(obj), obj)
    assert not primary_output_matches(_function(dict(obj, required=[])), obj)
    assert not primary_output_matches(_function(dict(obj, properties={})), obj)
    union = {"anyOf": [instant, {"type": "string", "format": "duration"}]}
    assert primary_output_matches(_function(union), union)
    assert not primary_output_matches(_function(union), instant)
    assert not primary_output_matches(_function(instant), union)


def test_annotations_literals_and_constraints_do_not_create_false_edges() -> None:
    source = {"type": "string", "format": "date-time", "description": "source", "default": "unused"}
    target = {"type": "string", "format": "date-time", "title": "target"}
    assert primary_output_matches(_function(source), target)
    contradictory = {"type": "string", "const": "en", "enum": ["fr"]}
    assert not primary_output_matches(_function({"type": "string", "const": "en"}), contradictory)
    literal = {"type": "object", "const": {"description": "literal", "value": 1}}
    other_literal = {"type": "object", "const": {"description": "different", "value": 1}}
    assert not primary_output_matches(_function(literal), other_literal)
    unsupported = {"anyOf": [{"type": "string", "format": "uuid", "$ref": "#/$defs/id"}]}
    assert not primary_output_matches(_function(unsupported), unsupported)


def test_object_matches_require_guarantees_and_reject_extra_forbidden_fields() -> None:
    instant = {"type": "string", "format": "date-time"}
    target = {
        "type": "object",
        "properties": {"at": instant},
        "required": ["at"],
        "additionalProperties": False,
    }
    extra = dict(target, properties={"at": instant, "rest": {"type": "string"}})
    assert not primary_output_matches(_function(extra), target)
    assert not primary_output_matches(_function(dict(target, additionalProperties=True)), target)
    assert primary_output_matches(_function(extra), dict(target, additionalProperties=True))

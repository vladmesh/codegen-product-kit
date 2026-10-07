"""Platform declarations stay generic, exportable and fail closed."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess

from jsonschema import Draft202012Validator
from pydantic import ValidationError
import pytest
import yaml

from framework.contracts.env_contract import ENV_CONTRACT_VERSION, EnvContractFragment
from framework.spec.packages import PackageManifest, PackageManifestError, parse_package_manifest

ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "tests/fixtures/platform_package/platform_package/package.yaml"
SCHEMA = ROOT / "tests/fixtures/env-contract.schema.json"


def test_contract_schema_fixture_is_the_self_contained_model_export() -> None:
    assert ENV_CONTRACT_VERSION == "1"
    assert json.loads(SCHEMA.read_text()) == EnvContractFragment.model_json_schema()
    Draft202012Validator.check_schema(json.loads(SCHEMA.read_text()))


def test_manifest_schema_accepts_platform_sources_as_data() -> None:
    data = yaml.safe_load(MANIFEST.read_text())
    Draft202012Validator(PackageManifest.model_json_schema()).validate(data)
    manifest = parse_package_manifest(data)
    assert manifest.environment[0].source.model_dump() == data["environment"][0]["source"]
    assert manifest.environment[1].source.model_dump() == data["environment"][1]["source"]
    assert manifest.environment[2].source is None


@pytest.mark.parametrize(
    ("index", "field"), [(0, "service"), (0, "scopes"), (0, "quota"), (1, "url")]
)
def test_platform_source_requires_explicit_data(index: int, field: str) -> None:
    data = yaml.safe_load(MANIFEST.read_text())
    del data["environment"][index]["source"][field]
    with pytest.raises(PackageManifestError, match=f"(?s){field}.*Field required"):
        parse_package_manifest(data)
    assert list(Draft202012Validator(PackageManifest.model_json_schema()).iter_errors(data))


def test_unknown_platform_source_fields_are_refused() -> None:
    data = yaml.safe_load(MANIFEST.read_text())
    data["environment"][0]["source"]["issued_key"] = "never committed"
    with pytest.raises(PackageManifestError, match="unknown nested field"):
        parse_package_manifest(data)
    assert list(Draft202012Validator(PackageManifest.model_json_schema()).iter_errors(data))


@pytest.mark.parametrize(
    ("index", "field", "value", "message"),
    [
        (0, "service", "bad/name", "service.*String should match pattern"),
        (1, "service", "BadName", "service.*String should match pattern"),
        (0, "quota", {"lookups_per_day": -1}, "quota.*greater than or equal to 0"),
        (0, "quota", {"lookups_per_day": True}, "quota.*valid integer"),
        (0, "quota", {"lookups_per_day": 1.5}, "quota.*valid integer"),
        (0, "quota", {"lookups_per_day": "100"}, "quota.*valid integer"),
        (0, "scopes", [""], "scopes.*at least 1 character"),
        (1, "url", "http://platform.example.test/geo-lookup", "url.*String should match pattern"),
        (1, "url", "https://user:secret@platform.example.test", "must not contain credentials"),
        (0, "kind", "unknown", "does not match any of the expected tags"),
    ],
)
def test_manifest_and_contract_refuse_invalid_platform_data(
    index: int, field: str, value: object, message: str
) -> None:
    data = yaml.safe_load(MANIFEST.read_text())
    data["environment"][index]["source"][field] = value
    with pytest.raises(PackageManifestError, match=f"(?s){message}"):
        parse_package_manifest(data)
    source = deepcopy(data["environment"][index]["source"])
    source["source"] = source.pop("kind")
    fragment = {
        "owner": "test",
        "entries": {
            "PLATFORM_VALUE": {
                **source,
                "environments": ["production"],
                "consumers": ["backend"],
                "required": True,
            }
        },
    }
    with pytest.raises(ValidationError):
        EnvContractFragment.model_validate(fragment)
    # Credential-free URL checking is additionally enforced by the model validator.
    if "credentials" not in message:
        assert list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(fragment))


@pytest.mark.parametrize(("index", "sensitive"), [(0, False), (1, True)])
def test_platform_sensitivity_cannot_be_overridden(index: int, sensitive: bool) -> None:
    source = yaml.safe_load(MANIFEST.read_text())["environment"][index]["source"]
    source["source"] = source.pop("kind")
    fragment = {
        "owner": "test",
        "entries": {
            "PLATFORM_VALUE": {
                **source,
                "environments": ["production"],
                "required": True,
                "sensitive": sensitive,
            }
        },
    }
    with pytest.raises(ValidationError, match="sensitive"):
        EnvContractFragment.model_validate(fragment)
    assert list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(fragment))


def test_core_has_no_concrete_platform_service_names() -> None:
    result = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
            "grep",
            "-niE",
            "tg[-_ ]?reader|tg[-_]sources|geo-lookup",
            "--",
            "framework",
            "template",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr

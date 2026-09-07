"""Package requirements compose with product-owned environment declarations."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from framework.contracts.env_contract import merge_env_contract_fragments
from framework.generators.package_environment import (
    PackageEnvironmentContractError,
    PackageEnvironmentGenerator,
)
from framework.spec.events import EventsSpec
from framework.spec.loader import AllSpecs, _validate_and_merge_packages
from framework.spec.models import ModelsSpec
from framework.spec.package_resolution import ActivePackage
from framework.spec.packages import parse_package_manifest


def _package(root: Path, name: str, variable: str, *, required: bool = True) -> ActivePackage:
    manifest = parse_package_manifest(
        {
            "protocol_version": 1,
            "name": name,
            "version": "1.0.0",
            "requires_core": ">=2,<3",
            "http": {"prefix": f"/{name}"},
            "environment": [{"name": variable, "required": required}],
        }
    )
    return ActivePackage(
        name=name,
        manifest=manifest,
        package_root=root / name,
        manifest_sha256="0" * 64,
    )


def _specs(root: Path, *packages: ActivePackage) -> AllSpecs:
    return AllSpecs(
        models=ModelsSpec(models={}),
        events=EventsSpec(),
        packages=list(packages),
    )


def _write_fragment(root: Path, relative: str, entries: dict[str, object]) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"version": "1", "owner": path.parent.name, "entries": entries},
            sort_keys=False,
        )
    )
    return path


def _literal_redis(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "source": "literal",
        "environments": ["local", "production"],
        "consumers": ["backend"],
        "required": True,
        "value": "redis://redis:6379",
    }
    entry.update(overrides)
    return entry


def _generated(root: Path) -> dict[str, object]:
    return yaml.safe_load((root / "services/backend/packages/env.contract.yaml").read_text())


def test_existing_product_declaration_is_reused_without_reading_stale_output(
    tmp_path: Path,
) -> None:
    declaration = _literal_redis(consumers=["backend", "tg_bot"])
    product = _write_fragment(tmp_path, "infra/env.contract.yaml", {"REDIS_URL": declaration})
    output = _write_fragment(
        tmp_path,
        "services/backend/packages/env.contract.yaml",
        {"REDIS_URL": {"source": "invalid"}},
    )
    specs = _specs(tmp_path, _package(tmp_path, "reminders", "REDIS_URL"))

    assert PackageEnvironmentGenerator(specs, tmp_path).generate() == [output]

    generated = _generated(tmp_path)
    assert generated["entries"]["REDIS_URL"] == declaration
    merged = merge_env_contract_fragments([yaml.safe_load(product.read_text()), generated])
    assert merged.entries["REDIS_URL"].source == "literal"


def test_undeclared_requirement_keeps_user_secret_fallback(tmp_path: Path) -> None:
    specs = _specs(tmp_path, _package(tmp_path, "weather", "WEATHER_TOKEN"))

    PackageEnvironmentGenerator(specs, tmp_path).generate()

    assert _generated(tmp_path)["entries"]["WEATHER_TOKEN"] == {
        "source": "user_secret",
        "environments": ["local", "production"],
        "consumers": ["backend"],
        "required": True,
        "description": "Environment value required by package weather",
        "sensitive": True,
    }


def test_repeated_package_requirement_is_deterministic_and_cannot_be_weakened(
    tmp_path: Path,
) -> None:
    optional = _package(tmp_path, "alpha", "SHARED_TOKEN", required=False)
    required = _package(tmp_path, "zeta", "SHARED_TOKEN", required=True)
    first_specs = _specs(tmp_path, optional, required)
    second_specs = _specs(tmp_path, required, optional)

    assert _validate_and_merge_packages(first_specs) == []
    PackageEnvironmentGenerator(first_specs, tmp_path).generate()
    first = (tmp_path / "services/backend/packages/env.contract.yaml").read_text()
    PackageEnvironmentGenerator(second_specs, tmp_path).generate()
    second = (tmp_path / "services/backend/packages/env.contract.yaml").read_text()

    assert first == second
    assert _generated(tmp_path)["entries"]["SHARED_TOKEN"]["required"] is True


@pytest.mark.parametrize(
    ("entry", "invariant"),
    [
        (_literal_redis(consumers=["tg_bot"]), "consumer 'backend'"),
        (_literal_redis(environments=["local"]), "environment 'production'"),
        (_literal_redis(required=False), "optional but the package requirement is required"),
    ],
)
def test_incompatible_product_declaration_names_requirement_and_invariant(
    tmp_path: Path, entry: dict[str, object], invariant: str
) -> None:
    _write_fragment(tmp_path, "infra/env.contract.yaml", {"REDIS_URL": entry})
    specs = _specs(tmp_path, _package(tmp_path, "reminders", "REDIS_URL"))

    with pytest.raises(
        PackageEnvironmentContractError,
        match=rf"REDIS_URL.*{invariant}",
    ):
        PackageEnvironmentGenerator(specs, tmp_path).generate()


def test_invalid_overlapping_product_fragment_fails_with_named_signal(tmp_path: Path) -> None:
    _write_fragment(
        tmp_path,
        "infra/env.contract.yaml",
        {"REDIS_URL": {**_literal_redis(), "source": "user_secret"}},
    )
    specs = _specs(tmp_path, _package(tmp_path, "reminders", "REDIS_URL"))

    with pytest.raises(
        PackageEnvironmentContractError,
        match="REDIS_URL.*product fragment is invalid",
    ):
        PackageEnvironmentGenerator(specs, tmp_path).generate()


def test_conflicting_product_fragments_fail_before_package_merge(tmp_path: Path) -> None:
    _write_fragment(tmp_path, "infra/env.contract.yaml", {"REDIS_URL": _literal_redis()})
    _write_fragment(
        tmp_path,
        "services/backend/env.contract.yaml",
        {"REDIS_URL": _literal_redis(value="redis://other:6379")},
    )
    specs = _specs(tmp_path, _package(tmp_path, "reminders", "REDIS_URL"))

    with pytest.raises(
        PackageEnvironmentContractError,
        match="REDIS_URL.*existing product declarations conflict",
    ) as caught:
        PackageEnvironmentGenerator(specs, tmp_path).generate()
    assert "incompatible environment contract declarations" not in str(caught.value)

"""Generate environment-contract requirements from the active package set."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError
import yaml

from framework.contracts.env_contract import (
    EnvContractEntry,
    EnvContractMergeError,
    merge_env_contract_fragments,
    validate_env_contract_fragment,
)
from framework.generators.base import BaseGenerator


class PackageEnvironmentContractError(ValueError):
    """Raised when a product declaration cannot satisfy a package requirement."""


class PackageEnvironmentGenerator(BaseGenerator):
    """Emit one backend-owned fragment without changing no-package products."""

    def _product_fragments(self, output: Path) -> list[object]:
        ignored_parts = {
            ".git",
            ".mypy_cache",
            ".pytest_cache",
            ".venv",
            "__pycache__",
            "node_modules",
        }
        requirements = {
            requirement.name
            for package in self.specs.packages
            for requirement in package.manifest.environment
        }
        fragments = []
        for path in sorted(self.repo_root.rglob("env.contract.yaml")):
            if path == output or ignored_parts.intersection(path.relative_to(self.repo_root).parts):
                continue
            loaded: Any = None
            try:
                loaded = yaml.safe_load(path.read_text())
                fragment = validate_env_contract_fragment(loaded)
            except (OSError, ValidationError, yaml.YAMLError) as error:
                raw_entries = loaded.get("entries", {}) if isinstance(loaded, dict) else {}
                overlap = sorted(requirements.intersection(raw_entries))
                subject = (
                    f"Package environment requirement {overlap[0]!r}"
                    if overlap
                    else "Product environment contract"
                )
                if isinstance(error, ValidationError):
                    detail: object = [
                        {"type": item["type"], "loc": list(item["loc"])}
                        for item in error.errors(include_url=False, include_input=False)
                    ]
                elif isinstance(error, yaml.YAMLError):
                    detail = "malformed YAML"
                else:
                    detail = error
                raise PackageEnvironmentContractError(
                    f"{subject} cannot use {path.relative_to(self.repo_root)}: "
                    f"the product fragment is invalid ({detail})"
                ) from error
            fragments.append(fragment)
        return fragments

    @staticmethod
    def _require_compatible(name: str, required: bool, entry: EnvContractEntry) -> None:
        environments = set(entry.environments)
        for environment in ("local", "production"):
            if environment not in environments:
                raise PackageEnvironmentContractError(
                    f"Package environment requirement {name!r} cannot reuse the product "
                    f"declaration: missing required environment {environment!r}"
                )
        if "backend" not in entry.consumers:
            raise PackageEnvironmentContractError(
                f"Package environment requirement {name!r} cannot reuse the product "
                "declaration: missing required consumer 'backend'"
            )
        if required and not entry.required:
            raise PackageEnvironmentContractError(
                f"Package environment requirement {name!r} cannot reuse the product "
                "declaration: it is optional but the package requirement is required"
            )

    def generate(self) -> list[Path]:
        output = self.repo_root / "services/backend/packages/env.contract.yaml"
        if not self.specs.packages:
            if output.exists():
                output.unlink()
            return []

        product_fragments = self._product_fragments(output)
        try:
            product_contract = merge_env_contract_fragments(product_fragments)
        except EnvContractMergeError as error:
            variable = str(error).rsplit(" ", 1)[-1]
            requirement_names = {
                requirement.name
                for package in self.specs.packages
                for requirement in package.manifest.environment
            }
            subject = (
                f"Package environment requirement {variable!r}"
                if variable in requirement_names
                else f"Product environment variable {variable!r}"
            )
            raise PackageEnvironmentContractError(
                f"{subject} cannot use the product environment contract: "
                "existing product declarations conflict"
            ) from error

        requirements: dict[str, tuple[bool, set[str]]] = {}
        for package in self.specs.packages:
            for requirement in package.manifest.environment:
                current_required, owners = requirements.get(requirement.name, (False, set()))
                requirements[requirement.name] = (
                    current_required or requirement.required,
                    owners | {package.name},
                )

        entries: dict[str, object] = {}
        for name in sorted(requirements):
            required, owners = requirements[name]
            product_entry = product_contract.entries.get(name)
            if product_entry is not None:
                self._require_compatible(name, required, product_entry)
                entries[name] = product_entry.model_dump(mode="json", exclude_unset=True)
            else:
                package_names = ", ".join(sorted(owners))
                description = (
                    f"Environment value required by package {package_names}"
                    if len(owners) == 1
                    else f"Environment value required by packages {package_names}"
                )
                entries[name] = {
                    "source": "user_secret",
                    "environments": ["local", "production"],
                    "consumers": ["backend"],
                    "required": required,
                    "description": description,
                    "sensitive": True,
                }
        content = yaml.safe_dump(
            {"version": "1", "owner": "packages", "entries": entries},
            sort_keys=False,
        )
        self.write_file(output, content)
        return [output]

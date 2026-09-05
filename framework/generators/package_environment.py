"""Generate environment-contract requirements from the active package set."""

from __future__ import annotations

from pathlib import Path

import yaml

from framework.generators.base import BaseGenerator


class PackageEnvironmentGenerator(BaseGenerator):
    """Emit one backend-owned fragment without changing no-package products."""

    def generate(self) -> list[Path]:
        output = self.repo_root / "services/backend/packages/env.contract.yaml"
        if not self.specs.packages:
            if output.exists():
                output.unlink()
            return []

        entries: dict[str, object] = {}
        for package in self.specs.packages:
            for requirement in package.manifest.environment:
                entries[requirement.name] = {
                    "source": "user_secret",
                    "environments": ["local", "production"],
                    "consumers": ["backend"],
                    "required": requirement.required,
                    "description": f"Environment value required by package {package.name}",
                    "sensitive": True,
                }
        content = yaml.safe_dump(
            {"version": "1", "owner": "packages", "entries": entries},
            sort_keys=False,
        )
        self.write_file(output, content)
        return [output]

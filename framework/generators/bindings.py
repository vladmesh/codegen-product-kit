"""Emit finite Telegram bindings after whole-product preflight."""

import json
from pathlib import Path
from pprint import pformat

from framework.binding_product import BindingPlan
from framework.generators.base import BaseGenerator


class BindingsGenerator(BaseGenerator):
    def __init__(self, specs, repo_root: Path, plan: BindingPlan) -> None:
        super().__init__(specs, repo_root)
        self.plan = plan

    def generate(self) -> list[Path]:
        if not (self.repo_root / "services/tg_bot").is_dir():
            return []
        output = self.repo_root / "services/tg_bot/src/generated/bindings.py"
        if not self.plan.bindings:
            self.write_file(
                output,
                '''"""No product bindings; safe in every product shape."""
def register(application: object, client_factory: object = None) -> None:
    pass

async def start(application: object) -> None:
    pass

async def stop(application: object) -> None:
    pass
''',
            )
            output.with_name("binding_relay.py").unlink(missing_ok=True)
        else:
            data = {
                "bindings": [item.model_dump(mode="json") for item in self.plan.bindings],
                "actions": self.plan.actions,
                "events": self.plan.events,
            }
            imports = "\n".join(
                f"from {module} import when as parse_{name}"
                for name, module in sorted(self.plan.libraries.items())
            )
            parsers = (
                "{"
                + ", ".join(
                    f"{name + '.when'!r}: parse_{name}" for name in sorted(self.plan.libraries)
                )
                + "}"
            )
            self.render_to_file(
                "bindings.py.j2",
                output,
                imports=imports,
                parsers=parsers,
                data=pformat(json.dumps(data, sort_keys=True), width=88),
            )
            relay = output.with_name("binding_relay.py")
            self.render_to_file("binding_relay.py.j2", relay)
            return [output, relay]
        self.format_file(output)
        return [output]

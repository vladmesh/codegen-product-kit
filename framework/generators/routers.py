"""REST router generator for domain specs."""

from pathlib import Path
from typing import Any

from framework.generators.base import BaseGenerator
from framework.generators.context import OperationContextBuilder

# The core users writes. With every core settings call they are durable before they are
# acknowledged: FastAPI runs the session dependency's own commit only after the response is sent,
# so the generated router commits first.
CORE_USER_WRITES = frozenset({"grant", "revoke"})


def _commits_before_response(service_name: str, domain_name: str, operation: str) -> bool:
    if service_name != "backend":
        return False
    return domain_name == "settings" or (domain_name == "users" and operation in CORE_USER_WRITES)


class RoutersGenerator(BaseGenerator):
    """Generate FastAPI routers and service-level router registry."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialize generator."""
        super().__init__(*args, **kwargs)
        self.context_builder = OperationContextBuilder()

    def generate(self) -> list[Path]:
        """Generate routers for all services with REST operations."""
        generated_files: list[Path] = []
        services_data: dict[str, dict[str, Any]] = {}

        for _domain_key, domain in sorted(self.specs.domains.items()):
            operations = domain.get_rest_operations()
            if not domain.config.rest or not operations:
                continue

            handlers = []
            imports: set[str] = set()
            param_type_imports: set[str] = set()
            needs_body = False
            needs_path = False
            needs_query = False
            needs_broker = False

            for operation in operations:
                ctx = self.context_builder.build_for_rest(operation)
                handlers.append(ctx)
                imports.update(ctx.imports)
                param_type_imports.update(ctx.param_type_imports)
                needs_body = needs_body or ctx.input_model is not None
                needs_path = needs_path or any(param.param_source == "path" for param in ctx.params)
                needs_query = needs_query or any(
                    param.param_source == "query" for param in ctx.params
                )
                needs_broker = needs_broker or ctx.publish_channel is not None

            service_name = domain.service_name
            service_data = services_data.setdefault(
                service_name,
                {"domains": [], "needs_broker": False},
            )
            service_data["needs_broker"] = service_data["needs_broker"] or needs_broker

            domain_context = {
                "name": domain.name,
                "module_name": domain.name,
                "router_prefix": domain.config.rest.prefix,
                "router_tags": domain.config.rest.tags,
                "protocol_name": domain.protocol_name,
                "controller_class_name": domain.controller_class_name,
                "handlers": handlers,
                "imports": imports,
                "param_type_imports": sorted(param_type_imports),
                "needs_body": needs_body,
                "needs_path": needs_path,
                "needs_query": needs_query,
                "needs_broker": needs_broker,
                # The core settings contract: a core-owned setting (language) is refused
                # outside its canonical scope here, before any product-owned controller.
                "core_setting_scopes": service_name == "backend" and domain.name == "settings",
                # A core settings call or users write commits before its event and its answer.
                "commit_before_response": {
                    handler.name
                    for handler in handlers
                    if _commits_before_response(service_name, domain.name, handler.name)
                },
            }
            service_data["domains"].append(domain_context)

            output_file = (
                self.repo_root
                / "services"
                / service_name
                / "src"
                / "generated"
                / "routers"
                / f"{domain.name}.py"
            )
            self.render_to_file("router.py.j2", output_file, **domain_context)
            generated_files.append(output_file)

        for service_name, service_data in services_data.items():
            routers_init = (
                self.repo_root
                / "services"
                / service_name
                / "src"
                / "generated"
                / "routers"
                / "__init__.py"
            )
            self.write_file(routers_init, "")
            generated_files.append(routers_init)

            registry_file = (
                self.repo_root / "services" / service_name / "src" / "generated" / "registry.py"
            )
            self.render_to_file(
                "registry.py.j2",
                registry_file,
                service_name=service_name,
                domains=service_data["domains"],
                needs_broker=service_data["needs_broker"],
            )
            generated_files.append(registry_file)

        return generated_files

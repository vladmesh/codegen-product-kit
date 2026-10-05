"""Import real reminder routes with inert core/storage dependencies, without a product."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

from fastapi import FastAPI
import pytest

ROOT = Path(__file__).parents[2]


def real_openapi(monkeypatch: pytest.MonkeyPatch) -> dict:
    # No route, model or Pydantic schema is replaced. Storage and identity aren't executed.
    async def caller_identity() -> str:
        raise AssertionError("OpenAPI generation must not call the identity dependency")

    package = ModuleType("codegen_kit_reminders")
    package.__path__ = [str(ROOT / "packages/codegen-kit-reminders/codegen_kit_reminders")]
    for name, module in {
        "codegen_kit_reminders": package,
        "codegen_kit": SimpleNamespace(caller_identity=caller_identity),
        "sqlalchemy": SimpleNamespace(text=lambda value: value),
        "codegen_kit_reminders.database": SimpleNamespace(database=None),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(package.__path__[0]) / "api.py"
    spec = importlib.util.spec_from_file_location("codegen_kit_reminders.api", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    application = FastAPI()
    application.include_router(module.router, prefix="/reminders")
    return application.openapi()

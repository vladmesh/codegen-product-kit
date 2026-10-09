"""The core language scope is refused in the generated settings router, before any controller.

The settings controller is product-owned (Copier keeps it on update), so the core contract
lives in generated code every product regenerates.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import sys
from types import ModuleType

from fastapi import HTTPException
import pytest

from framework.generators.protocols import ProtocolsGenerator
from framework.generators.routers import RoutersGenerator
from framework.generators.schemas import SchemasGenerator
from framework.generators.settings_manifest import SettingsManifestGenerator
from framework.spec.loader import load_specs

ROOT = Path(__file__).parents[2]
TEMPLATE = ROOT / "template"


def _product(root: Path) -> Path:
    for package in ("shared", "shared/shared", "shared/shared/generated", "services"):
        (root / package).mkdir(parents=True, exist_ok=True)
        (root / package / "__init__.py").write_text("")
    for package in ("services/backend", "services/backend/src", "services/backend/src/generated"):
        (root / package).mkdir(parents=True, exist_ok=True)
        (root / package / "__init__.py").write_text("")
    shutil.copytree(TEMPLATE / "shared/spec", root / "shared/spec")
    (root / "services/backend/spec").mkdir()
    for domain in ("settings", "users"):
        shutil.copy2(
            TEMPLATE / f"services/backend/spec/{domain}.yaml",
            root / f"services/backend/spec/{domain}.yaml",
        )
    shutil.copy2(
        TEMPLATE / "services/backend/manifest.yaml", root / "services/backend/manifest.yaml"
    )
    specs = load_specs(root)
    for generator in (
        SchemasGenerator,
        ProtocolsGenerator,
        RoutersGenerator,
        SettingsManifestGenerator,
    ):
        generator(specs, root).generate()
    return root


def test_template_settings_router_is_the_generated_one(tmp_path: Path) -> None:
    product = _product(tmp_path)
    for name in ("routers/settings.py", "routers/users.py"):
        generated = (product / "services/backend/src/generated" / name).read_text()
        shipped = (TEMPLATE / "services/backend/src/generated" / name).read_text()
        # Formatting is the product's ruff; the guard itself must match exactly.
        assert ("_require_core_scope(payload.key, payload.scope)" in generated) == (
            name == "routers/settings.py"
        )
        assert generated.count("_require_core_scope(") == shipped.count("_require_core_scope(")


def test_core_language_outside_product_scope_never_reaches_the_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = _product(tmp_path)
    for name in ("sqlalchemy", "sqlalchemy.ext", "sqlalchemy.ext.asyncio"):
        module = ModuleType(name)
        module.AsyncSession = object  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, name, module)
    # As in a product: `shared` is the distribution under shared/, ahead of the product root.
    monkeypatch.syspath_prepend(str(product))
    monkeypatch.syspath_prepend(str(product / "shared"))
    for name in [item for item in sys.modules if item.split(".")[0] in {"services", "shared"}]:
        monkeypatch.delitem(sys.modules, name)
    from services.backend.src.generated.routers.settings import create_router
    from shared.generated.schemas import SettingGet, SettingSet

    calls: list[tuple[str, object]] = []

    class Controller:
        async def get(self, session: object, payload: SettingGet) -> object:
            calls.append(("get", payload))
            return payload

        async def set(self, session: object, payload: SettingSet) -> object:
            calls.append(("set", payload))
            return payload

    router = create_router(get_session=lambda: None, get_controller=Controller)
    handlers = {route.path.rsplit("/", 1)[1]: route.endpoint for route in router.routes}
    user = {"key": "language", "scope": "user", "subject_id": 7}
    for name, payload in (
        ("set", SettingSet(**user, value="ru")),
        ("get", SettingGet(**user)),
    ):
        with pytest.raises(HTTPException) as refused:
            asyncio.run(handlers[name](payload=payload, session=None, controller=Controller()))
        assert refused.value.status_code == 422
        assert refused.value.detail == "Setting is only available in product scope"
    assert calls == []

    product_scope = SettingSet(key="language", scope="product", value="en")
    other_key = SettingSet(key="languages", scope="user", subject_id=7, value=["ru"])
    for payload in (product_scope, other_key):
        asyncio.run(handlers["set"](payload=payload, session=None, controller=Controller()))
    default_scope = SettingGet(key="language")
    asyncio.run(handlers["get"](payload=default_scope, session=None, controller=Controller()))
    assert calls == [("set", product_scope), ("set", other_key), ("get", default_scope)]

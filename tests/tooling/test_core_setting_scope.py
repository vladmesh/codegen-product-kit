"""Core settings and users routers: scope refusal, then commit, then event and answer.

The settings and users controllers are product-owned (Copier keeps them on update), so the core
contract lives in generated code every product regenerates.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
import shutil
import sys
from types import ModuleType
from typing import Any

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
        # Every core write commits; the read-only users access lookup does not.
        assert generated.count("await session.commit()") == 2
        assert shipped.count("await session.commit()") == 2


def _import_product(
    product: Path, monkeypatch: pytest.MonkeyPatch, publish_event: object = None
) -> None:
    """Make the generated product importable without a database driver or a broker."""
    for name in ("sqlalchemy", "sqlalchemy.ext", "sqlalchemy.ext.asyncio"):
        module = ModuleType(name)
        module.AsyncSession = object  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, name, module)
    # As in a product: `shared` is the distribution under shared/, ahead of the product root.
    monkeypatch.syspath_prepend(str(product))
    monkeypatch.syspath_prepend(str(product / "shared"))
    for name in [item for item in sys.modules if item.split(".")[0] in {"services", "shared"}]:
        monkeypatch.delitem(sys.modules, name)
    events = ModuleType("shared.generated.events")
    events.publish_event = publish_event  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "shared.generated.events", events)


def test_core_language_outside_product_scope_never_reaches_the_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _import_product(_product(tmp_path), monkeypatch)
    from services.backend.src.generated.routers.settings import create_router
    from shared.generated.schemas import SettingGet, SettingSet

    calls: list[tuple[str, object]] = []

    class Session:
        """Records the commit among the controller calls: stored before the answer is sent."""

        async def commit(self) -> None:
            calls.append(("commit", None))

    session = Session()

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
            asyncio.run(handlers[name](payload=payload, session=session, controller=Controller()))
        assert refused.value.status_code == 422
        assert refused.value.detail == "Setting is only available in product scope"
    assert calls == []

    product_scope = SettingSet(key="language", scope="product", value="en")
    other_key = SettingSet(key="languages", scope="user", subject_id=7, value=["ru"])
    for payload in (product_scope, other_key):
        asyncio.run(handlers["set"](payload=payload, session=session, controller=Controller()))
    default_scope = SettingGet(key="language")
    asyncio.run(handlers["get"](payload=default_scope, session=session, controller=Controller()))
    assert calls == [
        ("set", product_scope),
        ("commit", None),
        ("set", other_key),
        ("commit", None),
        ("get", default_scope),
        ("commit", None),
    ]


class _CommitFailed(Exception):
    """The database refused the commit."""


def _users_router(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: list[tuple[str, object]]
) -> dict[str, Callable[..., Any]]:
    async def publish_event(channel: str, payload: object) -> None:
        calls.append(("publish", channel))

    _import_product(_product(tmp_path), monkeypatch, publish_event)
    from services.backend.src.generated.routers.users import create_router

    router = create_router(get_session=lambda: None, get_controller=lambda: None)
    return {route.path.rsplit("/", 1)[1]: route.endpoint for route in router.routes}


class _UsersController:
    """Records each controller call and answers with the access the caller would receive."""

    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self.calls = calls

    async def grant(self, session: object, payload: object) -> object:
        self.calls.append(("grant", payload))
        return "granted"

    async def revoke(self, session: object, payload: object) -> object:
        self.calls.append(("revoke", payload))
        return "revoked"

    async def resolve(self, session: object, channel: str, external_id: str) -> object:
        self.calls.append(("resolve", external_id))
        return "resolved"


class _Session:
    """Records the commit among the controller and event calls; it may refuse the commit."""

    def __init__(self, calls: list[tuple[str, object]], *, fails: bool = False) -> None:
        self.calls = calls
        self.fails = fails

    async def commit(self) -> None:
        self.calls.append(("commit", None))
        if self.fails:
            raise _CommitFailed


def test_core_user_writes_commit_before_their_event_and_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, object]] = []
    handlers = _users_router(tmp_path, monkeypatch, calls)
    from shared.generated.schemas import UserGrant, UserRevoke

    session, controller = _Session(calls), _UsersController(calls)
    identity = {"channel": "telegram", "external_id": "424242105"}
    grant, revoke = UserGrant(**identity), UserRevoke(**identity)

    answers = [
        asyncio.run(handlers["grant"](payload=grant, session=session, controller=controller)),
        asyncio.run(handlers["revoke"](payload=revoke, session=session, controller=controller)),
        asyncio.run(
            handlers["access"](
                channel="telegram", external_id="424242105", session=session, controller=controller
            )
        ),
    ]

    assert answers == ["granted", "revoked", "resolved"]
    assert calls == [
        ("grant", grant),
        ("commit", None),
        ("publish", "user_granted"),
        ("revoke", revoke),
        ("commit", None),
        # The access lookup stays read-only: nothing to commit.
        ("resolve", "424242105"),
    ]


@pytest.mark.parametrize("name", ["grant", "revoke"])
def test_core_user_write_whose_commit_fails_is_neither_published_nor_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    calls: list[tuple[str, object]] = []
    handlers = _users_router(tmp_path, monkeypatch, calls)
    from shared.generated.schemas import UserGrant, UserRevoke

    payload = {"grant": UserGrant, "revoke": UserRevoke}[name](
        channel="telegram", external_id="424242105"
    )
    with pytest.raises(_CommitFailed):
        asyncio.run(
            handlers[name](
                payload=payload,
                session=_Session(calls, fails=True),
                controller=_UsersController(calls),
            )
        )

    assert calls == [(name, payload), ("commit", None)]

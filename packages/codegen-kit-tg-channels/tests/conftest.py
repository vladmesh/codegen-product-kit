"""Dependency-light package tests never initialize a product or a real database."""

import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

PACKAGE = Path(__file__).parents[1] / "codegen_kit_tg_channels"


@pytest.fixture
def modules(monkeypatch):
    namespace = ModuleType("codegen_kit_tg_channels")
    namespace.__path__ = [str(PACKAGE)]

    async def identity():
        return "telegram:123"

    monkeypatch.setitem(sys.modules, "codegen_kit_tg_channels", namespace)
    monkeypatch.setitem(sys.modules, "codegen_kit", SimpleNamespace(caller_identity=identity))
    monkeypatch.setitem(sys.modules, "sqlalchemy", SimpleNamespace(text=lambda value: value))
    monkeypatch.setitem(
        sys.modules, "codegen_kit_tg_channels.database", SimpleNamespace(database=None)
    )
    names = ("models", "client", "service", "store", "polling", "api")
    for name in names:
        monkeypatch.delitem(sys.modules, f"codegen_kit_tg_channels.{name}", raising=False)
    loaded = {name: importlib.import_module(f"codegen_kit_tg_channels.{name}") for name in names}
    yield SimpleNamespace(**loaded)
    for name in names:
        sys.modules.pop(f"codegen_kit_tg_channels.{name}", None)

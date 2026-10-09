"""Product settings the core owns. Manifests and packages reference them, never declare them."""

from __future__ import annotations

from typing import Any

CORE_OWNER = "core"
#: Canonical schema of every core-owned product setting.
CORE_SETTINGS: dict[str, dict[str, Any]] = {
    "language": {"type": "string", "enum": ["ru", "en"]},
}
#: The scope each core setting is read and written in through the settings API.
CORE_SETTING_SCOPES: dict[str, str] = {"language": "product"}
LANGUAGE_KEY = "language"

"""Stable public API for packages installed into a generated product."""

from .packages import (
    CORE_VERSION,
    PACKAGE_PROTOCOL_VERSION,
    Package,
)

__all__ = ["CORE_VERSION", "PACKAGE_PROTOCOL_VERSION", "Package"]

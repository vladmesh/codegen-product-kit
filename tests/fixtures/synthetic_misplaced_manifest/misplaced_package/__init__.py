"""A package-directory fixture with its manifest deliberately misplaced."""

from typing import Any


class MisplacedPackage:
    """Minimal runtime that must never be loaded by the protocol tests."""

    router: Any = None


package = MisplacedPackage()

"""Synthetic package used to prove real entry-point activation."""

from codegen_kit import Package, package_base, package_session, publish_event
from fastapi import APIRouter

from synthetic_package.status import response

router = APIRouter()


def orm_base() -> object:
    """Build the package-owned metadata registry through the public seam."""

    return package_base("synthetic")


def session() -> object:
    """Open the package transaction through the public seam."""

    return package_session("synthetic")


async def announce_ready(message: object) -> object:
    """Publish the package event without importing a product-generated module."""

    return await publish_event("synthetic.ready", message)


@router.get("/status")
async def status() -> dict[str, bool]:
    """Expose a route that exists only after activation."""

    return response()


class SyntheticPackage:
    """Record lifecycle calls on the generated FastAPI application."""

    router = router

    async def startup(self, application: object) -> None:
        application.state.synthetic_started = True  # type: ignore[attr-defined]

    async def shutdown(self, application: object) -> None:
        application.state.synthetic_stopped = True  # type: ignore[attr-defined]


package: Package = SyntheticPackage()

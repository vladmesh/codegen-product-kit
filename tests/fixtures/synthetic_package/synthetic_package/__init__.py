"""Synthetic package used to prove real entry-point activation."""

from codegen_kit import Package
from fastapi import APIRouter

from synthetic_package.status import response

router = APIRouter()


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

"""In-process read of the provided interface ``tg-channels.posts.v1``."""

from codegen_kit_tg_channels import api
from codegen_kit_tg_channels.models import DigestPost
from codegen_kit_tg_channels.service import ActionError


async def recent_posts(user_ref: str) -> list[DigestPost]:
    """Return the caller's posts exactly as ``digest`` does: same store, window and limits.

    Raises ``ActionError`` (``not_configured``, ``try_later``), or HTTP 503 before startup.
    """
    return await api.service().digest(user_ref)


__all__ = ["ActionError", "DigestPost", "recent_posts"]

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from services.backend.src.app.models.user import User, UserChannel, UserStatus
from services.backend.src.app.repositories.user import UserRepository
from services.backend.src.generated.protocols import UsersControllerProtocol
from shared.generated.schemas import UserAccess, UserGrant, UserRead, UserStatusUpdate


def _get_repo(session: AsyncSession) -> UserRepository:
    return UserRepository(session)


async def _get_user_or_404(repo: UserRepository, user_id: int) -> User:
    user = await repo.get(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def _to_user_read(user: User) -> UserRead:
    return UserRead.model_validate(user, from_attributes=True)


def _to_access(identity: UserChannel) -> UserAccess:
    return UserAccess(
        user_id=identity.user_id,
        status=identity.user.status,
        channel=identity.channel,
        external_id=identity.external_id,
    )


class UsersController(UsersControllerProtocol):
    """Implementation of the generated user capability contract."""

    async def list_users(self, session: AsyncSession) -> list[UserRead]:
        users = await _get_repo(session).list()
        return [_to_user_read(user) for user in users]

    async def grant(self, session: AsyncSession, payload: UserGrant) -> UserAccess:
        """Create or reactivate an external identity without duplicating it."""

        identity = await _get_repo(session).grant(payload.channel, payload.external_id)
        return _to_access(identity)

    async def get_user(self, session: AsyncSession, user_id: int) -> UserRead:
        user = await _get_user_or_404(_get_repo(session), user_id)
        return _to_user_read(user)

    async def set_user_status(
        self, session: AsyncSession, user_id: int, payload: UserStatusUpdate
    ) -> UserRead:
        repo = _get_repo(session)
        user = await _get_user_or_404(repo, user_id)
        updated = await repo.set_status(user, UserStatus(payload.status))
        return _to_user_read(updated)

    async def resolve(self, session: AsyncSession, channel: str, external_id: str) -> UserAccess:
        identity = await _get_repo(session).get_channel(channel, external_id)
        if identity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User identity not found"
            )
        return _to_access(identity)

    async def delete_user(self, session: AsyncSession, user_id: int) -> None:
        repo = _get_repo(session)
        user = await _get_user_or_404(repo, user_id)
        await repo.delete(user)

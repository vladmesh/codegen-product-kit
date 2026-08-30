from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from services.backend.src.app.models.user import User
from services.backend.src.app.repositories.user import UserRepository
from services.backend.src.generated.protocols import UsersControllerProtocol
from shared.generated.schemas import (
    UserCreate,
    UserRead,
    UserUpdate,
)


def _get_repo(session: AsyncSession) -> UserRepository:
    return UserRepository(session)


async def _get_user_or_404(repo: UserRepository, user_id: int) -> User:
    user = await repo.get(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


async def _ensure_unique_telegram(
    repo: UserRepository, telegram_id: int, current_id: int | None = None
) -> None:
    existing = await repo.get_by_telegram_id(telegram_id)
    if existing is not None and existing.id != current_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Telegram user already exists"
        )


def _to_schema(user: User) -> UserRead:
    return UserRead.model_validate(user, from_attributes=True)


class UsersController(UsersControllerProtocol):
    """
    Implementation of UsersControllerProtocol.
    """

    async def list_users(
        self,
        session: AsyncSession,
    ) -> list[UserRead]:
        repo = _get_repo(session)
        users = await repo.list()
        return [_to_schema(u) for u in users]

    async def create_user(
        self,
        session: AsyncSession,
        payload: UserCreate,
    ) -> UserRead:
        """
        Handler for create_user
        """
        repo = _get_repo(session)
        await _ensure_unique_telegram(repo, payload.telegram_id)
        created = await repo.create(payload)
        return _to_schema(created)

    async def get_user(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> UserRead:
        """
        Handler for get_user
        """
        repo = _get_repo(session)
        user = await _get_user_or_404(repo, user_id)
        return _to_schema(user)

    async def update_user(
        self,
        session: AsyncSession,
        user_id: int,
        payload: UserUpdate,
    ) -> UserRead:
        """
        Handler for update_user
        """
        repo = _get_repo(session)
        user = await _get_user_or_404(repo, user_id)
        data = payload.model_dump(exclude_unset=True)
        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="No changes supplied"
            )
        if payload.telegram_id is not None:
            await _ensure_unique_telegram(repo, payload.telegram_id, current_id=user.id)
        updated = await repo.update(user, payload)
        return _to_schema(updated)

    async def delete_user(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> None:
        """
        Handler for delete_user
        """
        repo = _get_repo(session)
        user = await _get_user_or_404(repo, user_id)
        await repo.delete(user)

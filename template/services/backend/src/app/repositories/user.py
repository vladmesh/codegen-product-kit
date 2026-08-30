"""User repository helpers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.generated.schemas import UserCreate, UserUpdate

from ..models.user import User


class UserRepository:
    """Data access methods for users."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self) -> list[User]:
        result = await self.session.execute(select(User))
        return list(result.scalars().all())

    async def get(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, payload: UserCreate) -> User:
        user = User(**payload.model_dump())
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def update(self, user: User, payload: UserUpdate) -> User:
        data = payload.model_dump(exclude_unset=True)
        for field, value in data.items():
            setattr(user, field, value)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def delete(self, user: User) -> None:
        await self.session.delete(user)


__all__ = ["UserRepository"]

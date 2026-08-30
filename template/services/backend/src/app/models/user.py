"""User ORM model."""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, text
from sqlalchemy.orm import Mapped, mapped_column

from services.backend.src.core.orm import ORMBase


class User(ORMBase):
    """Represents an authenticated Telegram user."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


__all__ = ["User"]

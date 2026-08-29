"""Database engine and session management."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from sqlalchemy import DateTime, TypeDecorator, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .settings import get_settings

settings = get_settings()
async_engine = create_async_engine(settings.async_database_url, future=True, echo=False)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine, autoflush=False, autocommit=False, class_=AsyncSession
)


class TzAwareDateTime(TypeDecorator):
    """Restore UTC after SQLite drops tzinfo on round-trip."""

    impl = DateTime
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


class CreatedAtMixin:
    """Mixin that adds created_at timestamp."""

    created_at: Mapped[datetime] = mapped_column(
        TzAwareDateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ORMBase(CreatedAtMixin, Base):
    """Common columns shared by all persisted models (created_at + updated_at)."""

    __abstract__ = True

    updated_at: Mapped[datetime] = mapped_column(
        TzAwareDateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional scope around a series of async operations.

    Automatically commits transactions on successful request completion
    and rolls back on exceptions.
    """

    db = AsyncSessionLocal()
    try:
        yield db
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

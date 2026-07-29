"""Database engine, session factory, the declarative base, and the UTC column."""

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import DateTime, Dialect, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.config import get_settings

settings = get_settings()

# SQLite refuses connections across threads by default, and FastAPI serves
# requests from a thread pool. Harmless on Postgres, which ignores it.
_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class UtcDateTime(TypeDecorator):
    """A timestamp that is always UTC and always aware, on every backend.

    SQLite has no timezone type, so a plain ``DateTime`` column silently drops
    the zone and hands back a naive value; Postgres keeps it. Relying on the
    column type therefore means development and deployment disagree about what
    ``watched_at`` means, and every comparison against ``now()`` is wrong on one
    of them. Normalising on the way in and reattaching UTC on the way out makes
    the two behave identically.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"refusing to store a datetime with no timezone: {value!r}")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        # Naive here means the backend discarded the zone, and everything stored
        # went in as UTC.
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session that always closes."""
    with SessionLocal() as session:
        yield session

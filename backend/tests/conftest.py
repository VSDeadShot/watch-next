"""Shared test fixtures.

The database fixture is a real in-memory SQLite database rather than a mocked
session. Mocking a session proves the importer calls SQLAlchemy; only a real one
proves the unique constraint that makes re-imports idempotent actually holds.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import DEFAULT_USER_ID, ImportRun, WatchEvent

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        # An in-memory database lives inside its connection, so every connection
        # would otherwise get its own empty schema.
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def watched(session: Session):
    """Store one watch event, the way the importer would have.

    Shared rather than local to one module because resolution, the fixer and
    the endpoints all need a library to work on, and they should be looking at
    rows of the same shape.
    """
    run = ImportRun(user_id=DEFAULT_USER_ID, source="netflix", export_format="full")
    session.add(run)
    session.flush()
    counter = iter(range(1_000_000))

    def add(title: str, *, kind: str = "movie", ambiguous: bool = False, **extra) -> WatchEvent:
        event = WatchEvent(
            user_id=DEFAULT_USER_ID,
            import_id=run.id,
            fingerprint=f"fp{next(counter)}",
            source="netflix",
            raw_title=title,
            watched_at=datetime(2024, 3, 14, 20, 12, tzinfo=UTC),
            kind=kind,
            title=title,
            title_ambiguous=ambiguous,
            **extra,
        )
        session.add(event)
        session.flush()
        return event

    return add


@pytest.fixture
def full_export() -> bytes:
    return (FIXTURES / "viewing_activity_full.csv").read_bytes()


@pytest.fixture
def simple_export() -> bytes:
    return (FIXTURES / "netflix_viewing_history_simple.csv").read_bytes()

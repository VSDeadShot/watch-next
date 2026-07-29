"""Shared test fixtures.

The database fixture is a real in-memory SQLite database rather than a mocked
session. Mocking a session proves the importer calls SQLAlchemy; only a real one
proves the unique constraint that makes re-imports idempotent actually holds.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base

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
def full_export() -> bytes:
    return (FIXTURES / "viewing_activity_full.csv").read_bytes()


@pytest.fixture
def simple_export() -> bytes:
    return (FIXTURES / "netflix_viewing_history_simple.csv").read_bytes()

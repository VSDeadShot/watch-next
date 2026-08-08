"""Shared test fixtures.

The database fixture is a real in-memory SQLite database rather than a mocked
session. Mocking a session proves the importer calls SQLAlchemy; only a real one
proves the unique constraint that makes re-imports idempotent actually holds.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import DEFAULT_USER_ID, ImportRun, Offer, Provider, Title, UserProvider, WatchEvent

FIXTURES = Path(__file__).parent / "fixtures"

# The country every availability fixture below defaults to, and the one the API
# tests configure their settings with. Availability is meaningless without one.
COUNTRY = "IN"


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
def offers(session: Session):
    """Cache one offer, the way a resolution or a refresh would have.

    Shared because availability is asked about from three directions now -- the
    recommender, the watchlist and the service that dresses both -- and all
    three should be looking at rows of the same shape.
    """

    def add(
        title: Title,
        provider: str,
        monetization: str = "FLATRATE",
        *,
        country: str = COUNTRY,
        url: str | None = None,
        presentation: str = "HD",
    ) -> Offer:
        offer = Offer(
            title_id=title.id,
            country=country,
            provider_short_name=provider,
            monetization_type=monetization,
            presentation_type=presentation,
            url=url,
            fetched_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(offer)
        session.flush()
        return offer

    return add


@pytest.fixture
def providers(session: Session):
    """Put a service in the catalogue, which is where its display name lives."""

    def add(short_name: str, name: str, *, country: str = COUNTRY) -> Provider:
        provider = Provider(
            country=country,
            short_name=short_name,
            technical_name=short_name,
            name=name,
            icon_url=None,
            monetization_types=["flatrate"],
        )
        session.add(provider)
        session.flush()
        return provider

    return add


@pytest.fixture
def subscribes(session: Session):
    """Say which services somebody pays for -- the availability rule's input."""

    def add(*short_names: str, country: str = COUNTRY, user_id: str = DEFAULT_USER_ID) -> None:
        for short_name in short_names:
            session.add(UserProvider(user_id=user_id, country=country, short_name=short_name))
        session.flush()

    return add


@pytest.fixture
def counting(session: Session):
    """Count the statements a block of work sends.

    A query per row is the failure that does not show up in any assertion about
    the answer -- the page is correct and gets slower the more somebody uses it
    -- so the batching is pinned down by counting rather than by trusting it.
    """

    @contextmanager
    def counter() -> Iterator[list[str]]:
        statements: list[str] = []
        bind = session.get_bind()

        def record(_conn, _cursor, statement, *_rest) -> None:
            statements.append(statement)

        event.listen(bind, "before_cursor_execute", record)
        try:
            yield statements
        finally:
            event.remove(bind, "before_cursor_execute", record)

    return counter


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
        # Defaults rather than fixed values: who watched it and when are the two
        # things a test about history has to be able to vary, and a keyword that
        # collided with a hard-coded one here would fail as a TypeError rather
        # than as anything a reader could act on.
        extra.setdefault("user_id", DEFAULT_USER_ID)
        extra.setdefault("watched_at", datetime(2024, 3, 14, 20, 12, tzinfo=UTC))
        event = WatchEvent(
            import_id=run.id,
            fingerprint=f"fp{next(counter)}",
            source="netflix",
            raw_title=title,
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

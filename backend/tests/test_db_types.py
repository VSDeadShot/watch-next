"""Tests for the UTC datetime column.

SQLite has no timezone type and hands back naive values; Postgres hands back
aware ones. Left alone that difference means the same code behaves differently
in development and in deployment, which is the worst place for a divergence
because tests pass either way.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from app.models import ImportRun, WatchEvent
from app.services.importer import import_netflix_export

MOMENT = datetime(2024, 3, 14, 20, 12, 3, tzinfo=UTC)


def event(session: Session, watched_at: datetime) -> WatchEvent:
    run = ImportRun(source="netflix", export_format="full", filename=None)
    session.add(run)
    session.flush()
    stored = WatchEvent(
        import_id=run.id,
        fingerprint="f" * 64,
        source="netflix",
        raw_title="Inception",
        watched_at=watched_at,
        kind="movie",
        title="Inception",
    )
    session.add(stored)
    session.commit()
    session.expire_all()
    return session.scalars(select(WatchEvent)).one()


class TestUtcDateTime:
    def test_reads_back_as_an_aware_utc_moment(self, session: Session):
        assert event(session, MOMENT).watched_at == MOMENT

    def test_another_zone_is_normalised_to_utc(self, session: Session):
        india = timezone(timedelta(hours=5, minutes=30))

        stored = event(session, MOMENT.astimezone(india))

        assert stored.watched_at == MOMENT
        assert stored.watched_at.utcoffset() == timedelta(0)

    def test_a_naive_moment_is_refused(self, session: Session):
        """Storing it would record a moment whose zone nobody knows.

        SQLAlchemy re-raises the refusal as a StatementError, keeping the
        original message, so the failing column is still named.
        """
        with pytest.raises(StatementError, match="timezone"):
            event(session, datetime(2024, 3, 14, 20, 12, 3))


class TestImportTimestamp:
    def test_the_audit_row_records_when_the_upload_happened(
        self, session: Session, full_export: bytes
    ):
        summary = import_netflix_export(session, full_export)
        session.expire_all()

        run = session.get(ImportRun, summary.import_id)
        assert run is not None
        assert run.created_at.tzinfo is not None

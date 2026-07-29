"""Tests for persisting a parsed Netflix export.

The point of most of these is idempotency. Netflix's export is cumulative, so
users re-upload the same history repeatedly; an importer that inserts blindly
doubles the library every time.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.netflix_parser import NetflixExportError, SkipReason
from app.core.title_parser import TitleKind
from app.models import ImportRun, WatchEvent
from app.services.importer import import_netflix_export

HEADER = (
    "Profile Name,Start Time,Duration,Attributes,Title,"
    "Supplemental Video Type,Device Type,Bookmark,Latest Bookmark,Country"
)


def full_csv(*rows: str) -> bytes:
    return "\n".join([HEADER, *rows, ""]).encode()


def row(title: str = "Inception", start: str = "2024-03-14 20:12:03") -> str:
    return f"Sam,{start},0:48:22,,{title},,Smart TV,,,IN"


def stored_titles(session: Session) -> list[str]:
    return list(session.scalars(select(WatchEvent.raw_title).order_by(WatchEvent.watched_at)))


def event_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(WatchEvent)) or 0


class TestImportingTheFullExport:
    def test_stores_the_rows_the_parser_kept(self, session: Session, full_export: bytes):
        summary = import_netflix_export(session, full_export)

        assert summary.imported == 4
        assert event_count(session) == 4

    def test_reports_counts_that_add_up_to_the_file(self, session: Session, full_export: bytes):
        """A user who can't reconcile the summary against their file stops
        trusting the importer, so the arithmetic has to be visible."""
        summary = import_netflix_export(session, full_export)

        assert summary.total_rows == 7
        assert summary.imported + summary.duplicates + summary.skipped == summary.total_rows

    def test_records_why_rows_were_skipped(self, session: Session, full_export: bytes):
        summary = import_netflix_export(session, full_export)

        assert summary.skipped_by_reason == {
            SkipReason.SUPPLEMENTAL_VIDEO: 2,
            SkipReason.TOO_SHORT: 1,
        }

    def test_keeps_the_columns_the_export_provides(self, session: Session, full_export: bytes):
        import_netflix_export(session, full_export)

        event = session.scalars(
            select(WatchEvent).where(WatchEvent.raw_title == "Mission: Impossible - Fallout")
        ).one()

        assert event.profile_name == "Sam"
        assert event.device_type == "Chrome PC"
        assert event.country == "IN"
        assert event.duration_seconds == 6727

    def test_stores_watched_at_as_an_aware_utc_moment(self, session: Session, full_export: bytes):
        """SQLite has no timezone type, so a plain DateTime column hands back a
        naive value and every later comparison silently shifts."""
        import_netflix_export(session, full_export)
        session.expire_all()

        event = session.scalars(
            select(WatchEvent).where(WatchEvent.raw_title == "Mission: Impossible - Fallout")
        ).one()

        assert event.watched_at == datetime(2024, 3, 13, 22, 4, 10, tzinfo=UTC)
        assert event.watched_at.tzinfo is not None


class TestParsedTitleIsStored:
    def test_an_episode_row_is_broken_into_its_parts(self, session: Session, full_export: bytes):
        import_netflix_export(session, full_export)

        event = session.scalars(
            select(WatchEvent).where(
                WatchEvent.raw_title == "The Office (U.S.): Season 7: Ultimatum"
            )
        ).one()

        assert event.kind == TitleKind.EPISODE
        assert event.title == "The Office (U.S.)"
        assert event.season_number == 7
        assert event.episode_title == "Ultimatum"
        assert event.title_ambiguous is False

    def test_a_film_keeps_its_colon(self, session: Session, full_export: bytes):
        import_netflix_export(session, full_export)

        event = session.scalars(
            select(WatchEvent).where(WatchEvent.raw_title == "Mission: Impossible - Fallout")
        ).one()

        assert event.kind == TitleKind.MOVIE
        assert event.title == "Mission: Impossible - Fallout"
        assert event.season_number is None

    def test_a_guessed_kind_is_flagged_for_the_resolver(self, session: Session):
        import_netflix_export(session, full_csv(row(title="Some Show: Unlabelled")))

        event = session.scalars(select(WatchEvent)).one()

        assert event.title_ambiguous is True

    def test_a_row_with_no_usable_title_is_skipped_not_stored(self, session: Session):
        summary = import_netflix_export(session, full_csv(row(title=":")))

        assert event_count(session) == 0
        assert summary.skipped_by_reason == {SkipReason.MISSING_TITLE: 1}


class TestReimportingIsIdempotent:
    def test_the_same_file_twice_adds_nothing(self, session: Session, full_export: bytes):
        import_netflix_export(session, full_export)
        second = import_netflix_export(session, full_export)

        assert second.imported == 0
        assert second.duplicates == 4
        assert event_count(session) == 4

    def test_a_later_export_adds_only_its_new_rows(self, session: Session):
        """A re-download is the previous file with newer rows prepended."""
        import_netflix_export(
            session, full_csv(row("Inception"), row("Arrival", "2024-03-13 10:00:00"))
        )

        summary = import_netflix_export(
            session,
            full_csv(
                row("Dune", "2024-03-15 21:00:00"),
                row("Inception"),
                row("Arrival", "2024-03-13 10:00:00"),
            ),
        )

        assert summary.imported == 1
        assert summary.duplicates == 2
        assert stored_titles(session) == ["Arrival", "Inception", "Dune"]

    def test_a_same_day_rewatch_in_the_simple_export_is_kept(self, session: Session):
        """The simple export records no clock time, so two identical lines are a
        genuine rewatch rather than a duplicate to collapse."""
        twice = b"Title,Date\nInception,25/12/2023\nInception,25/12/2023\n"

        first = import_netflix_export(session, twice)
        second = import_netflix_export(session, twice)

        assert first.imported == 2
        assert second.imported == 0
        assert event_count(session) == 2

    def test_dedupes_beyond_one_query_batch(self, session: Session):
        """SQLite caps the number of bound parameters in a single statement, and
        a real export runs to thousands of rows."""
        many = full_csv(
            *(
                row(f"Title {index}", f"2024-03-14 {index // 60:02d}:{index % 60:02d}:00")
                for index in range(1200)
            )
        )

        first = import_netflix_export(session, many)
        second = import_netflix_export(session, many)

        assert first.imported == 1200
        assert second.imported == 0
        assert second.duplicates == 1200


class TestImportAudit:
    def test_each_upload_is_recorded(self, session: Session, full_export: bytes):
        import_netflix_export(session, full_export, filename="ViewingActivity.csv")
        import_netflix_export(session, full_export, filename="ViewingActivity.csv")

        runs = list(session.scalars(select(ImportRun).order_by(ImportRun.id)))

        assert len(runs) == 2
        assert [run.imported_rows for run in runs] == [4, 0]
        assert [run.duplicate_rows for run in runs] == [0, 4]
        assert runs[0].filename == "ViewingActivity.csv"

    def test_events_point_at_the_upload_that_created_them(
        self, session: Session, full_export: bytes
    ):
        summary = import_netflix_export(session, full_export)

        event = session.scalars(select(WatchEvent)).first()
        assert event is not None
        assert event.import_id == summary.import_id

    def test_skip_reasons_survive_on_the_audit_row(self, session: Session, full_export: bytes):
        summary = import_netflix_export(session, full_export)

        run = session.get(ImportRun, summary.import_id)
        assert run is not None
        assert run.skipped_detail == {"supplemental_video": 2, "too_short": 1}


class TestSimpleExport:
    def test_imports_every_row(self, session: Session, simple_export: bytes):
        summary = import_netflix_export(session, simple_export)

        assert summary.imported == 4
        assert summary.export_format == "simple"

    def test_date_assumptions_reach_the_caller(self, session: Session):
        """Nothing in this file settles whether 01/02 is January or February."""
        summary = import_netflix_export(session, b"Title,Date\nInception,01/02/2024\n")

        assert summary.assumptions
        assert "day/month/year" in summary.assumptions[0]

    def test_assumptions_are_recorded_on_the_audit_row(self, session: Session):
        summary = import_netflix_export(session, b"Title,Date\nInception,01/02/2024\n")

        run = session.get(ImportRun, summary.import_id)
        assert run is not None
        assert run.assumptions == list(summary.assumptions)


class TestRejectsUnreadableUploads:
    def test_a_file_that_is_not_an_export_is_refused(self, session: Session):
        with pytest.raises(NetflixExportError):
            import_netflix_export(session, b"name,email\nSam,sam@example.com\n")

    def test_nothing_is_stored_when_the_upload_is_refused(self, session: Session):
        with pytest.raises(NetflixExportError):
            import_netflix_export(session, b"name,email\nSam,sam@example.com\n")

        assert event_count(session) == 0
        assert session.scalar(select(func.count()).select_from(ImportRun)) == 0

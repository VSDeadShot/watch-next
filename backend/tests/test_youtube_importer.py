"""Tests for persisting a streamed Takeout watch history.

Two things are being held to here that the Netflix importer does not have to
worry about. The history is too big to hold, so nothing may accumulate in
proportion to the file: one test watches the session persist things and fails if
a single view ever becomes an ORM object, another counts the batches and fails if
the writing waits for the reading to finish. And because it is read as it is
written, a download that ends halfway through is only discovered with rows
already pending, so abandoning the transaction has to leave nothing behind.
"""

import io
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.core.youtube_parser import YouTubeExportError
from app.models import ImportRun, YouTubeView
from app.services.importer import import_youtube_export


def entry(**overrides) -> dict:
    entry = {
        "header": "YouTube",
        "title": "Watched A perfectly ordinary video",
        "titleUrl": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "subtitles": [{"name": "Some Channel", "url": "https://www.youtube.com/channel/UC1"}],
        "time": "2024-03-14T20:12:03.415Z",
        "products": ["YouTube"],
        "activityControls": ["YouTube watch history"],
    }
    entry.update(overrides)
    return entry


def video(index: int, *, minute: int | None = None) -> dict:
    """A distinct view, numbered so a file can be built out of them."""
    return entry(
        title=f"Watched Video number {index}",
        titleUrl=f"https://www.youtube.com/watch?v=vid{index:08d}",
        time=f"2024-03-14T20:{(minute if minute is not None else index % 60):02d}:03.000Z",
    )


def takeout(*entries: dict) -> io.BytesIO:
    return io.BytesIO(json.dumps(list(entries)).encode("utf-8"))


def view_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(YouTubeView)) or 0


def stored(session: Session) -> list[YouTubeView]:
    return list(session.scalars(select(YouTubeView).order_by(YouTubeView.id)))


class TestImportingAHistory:
    def test_stores_the_views_the_parser_kept(self, session: Session):
        summary = import_youtube_export(session, takeout(video(1), video(2), video(3)))

        assert summary.imported == 3
        assert view_count(session) == 3

    def test_reports_counts_that_add_up_to_the_file(self, session: Session):
        summary = import_youtube_export(
            session,
            takeout(video(1), entry(titleUrl=None), entry(details=[{"name": "From Google Ads"}])),
        )

        assert summary.total_rows == 3
        assert summary.imported + summary.duplicates + summary.skipped == summary.total_rows

    def test_keeps_what_the_view_was_and_whose_it_was(self, session: Session):
        import_youtube_export(session, takeout(entry()))

        view = stored(session)[0]
        assert view.video_id == "aaaaaaaaaaa"
        assert view.title == "A perfectly ordinary video"
        assert view.channel_name == "Some Channel"
        assert view.watched_at == datetime(2024, 3, 14, 20, 12, 3, 415000, tzinfo=UTC)

    def test_records_the_upload_as_a_youtube_import(self, session: Session):
        summary = import_youtube_export(session, takeout(video(1)), filename="watch-history.json")

        run = session.get(ImportRun, summary.import_id)
        assert run is not None
        assert run.source == "youtube"
        assert run.export_format == "takeout"
        assert run.filename == "watch-history.json"

    def test_the_run_keeps_the_numbers_the_user_was_shown(self, session: Session):
        summary = import_youtube_export(session, takeout(video(1), entry(titleUrl=None)))

        run = session.get(ImportRun, summary.import_id)
        assert run.total_rows == 2
        assert run.imported_rows == 1
        assert run.skipped_rows == 1
        assert run.skipped_detail == {"unavailable_video": 1}

    def test_keeps_the_reader_s_assumptions(self, session: Session):
        """A history in another language keeps its "Watched" prefix, and the
        summary has to say so or the titles just look wrong."""
        summary = import_youtube_export(
            session,
            takeout(entry(title="Ha visto Uno"), entry(title="Ha visto Dos")),
        )

        assert len(summary.assumptions) == 1
        run = session.get(ImportRun, summary.import_id)
        assert run.assumptions == list(summary.assumptions)

    def test_a_history_with_nothing_in_it_is_not_an_error(self, session: Session):
        summary = import_youtube_export(session, takeout())

        assert summary.imported == 0
        assert summary.total_rows == 0


class TestReimporting:
    """Takeout hands over the whole history every time, so this is the normal
    case rather than a mistake worth guarding against."""

    def test_uploading_the_same_file_twice_adds_nothing(self, session: Session):
        first = import_youtube_export(session, takeout(video(1), video(2)))
        second = import_youtube_export(session, takeout(video(1), video(2)))

        assert first.imported == 2
        assert second.imported == 0
        assert second.duplicates == 2
        assert view_count(session) == 2

    def test_a_later_export_adds_only_what_is_new(self, session: Session):
        import_youtube_export(session, takeout(video(1), video(2)))

        summary = import_youtube_export(session, takeout(video(1), video(2), video(3)))

        assert summary.imported == 1
        assert summary.duplicates == 2
        assert view_count(session) == 3

    def test_the_same_video_at_two_times_is_two_views(self, session: Session):
        """Returning to something is the signal this data is mostly good for, so
        it must not be collapsed into one row."""
        summary = import_youtube_export(
            session,
            takeout(
                entry(time="2024-03-14T20:12:03Z"),
                entry(time="2024-04-01T09:00:00Z"),
            ),
        )

        assert summary.imported == 2

    def test_two_identical_entries_are_both_kept(self, session: Session):
        """Takeout does write these. Numbering them keeps them apart, and keeps
        doing so on the next upload of the same file."""
        first = import_youtube_export(session, takeout(entry(), entry()))
        second = import_youtube_export(session, takeout(entry(), entry()))

        assert first.imported == 2
        assert second.duplicates == 2
        assert view_count(session) == 2

    def test_a_title_edited_since_the_last_export_is_not_a_new_view(self, session: Session):
        """The uploader renamed their video. It is the same thing watched at the
        same moment, and identity is the video id for exactly this reason."""
        import_youtube_export(session, takeout(entry(title="Watched Old name")))

        summary = import_youtube_export(session, takeout(entry(title="Watched New name")))

        assert summary.imported == 0
        assert summary.duplicates == 1


class TestStreaming:
    """The claim the whole step rests on: the file's size does not become the
    process's size."""

    def test_no_orm_object_is_made_per_view(self, session: Session):
        """A bulk insert, not five hundred thousand instances. Counted by
        watching the session persist things rather than by looking in the
        identity map afterwards -- that map holds weak references, so objects
        nobody kept have already gone by the time a test could look."""
        persisted: list[object] = []
        event.listen(
            session,
            "pending_to_persistent",
            lambda _session, instance: persisted.append(instance),
        )

        import_youtube_export(session, takeout(*(video(n, minute=n % 60) for n in range(1500))))

        assert view_count(session) == 1500
        # The audit row goes through the ORM. Nothing else may.
        assert [type(instance).__name__ for instance in persisted] == ["ImportRun"]

    def test_views_are_written_as_the_file_is_read(self, session: Session):
        """Not gathered up and written at the end, which would hold the whole
        history in a list and lose on memory exactly what the streaming won."""
        from app.services import importer

        batches: list[int] = []
        original = importer._store_views

        def counting(session, run, batch, **kwargs):
            batches.append(len(batch))
            return original(session, run, batch, **kwargs)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(importer, "_store_views", counting)
            import_youtube_export(session, takeout(*(video(n) for n in range(1200))))

        assert len(batches) > 1
        assert max(batches) <= importer._IMPORT_BATCH

    def test_imports_a_history_larger_than_one_batch(self, session: Session):
        summary = import_youtube_export(session, takeout(*(video(n) for n in range(1200))))

        assert summary.imported == 1200

    def test_a_repeat_beyond_the_batch_boundary_is_still_recognised(self, session: Session):
        """The duplicate check runs per batch, so it has to see the batches
        already written -- otherwise this collides on the way into the table
        instead of being counted."""
        entries = [entry(), *(video(n) for n in range(1, 900)), entry()]

        summary = import_youtube_export(session, takeout(*entries))

        assert summary.duplicates == 1
        assert summary.imported == 900

    def test_a_repeat_inside_one_batch_is_recognised_too(self, session: Session):
        """Neither row is written yet, so the query cannot see the first one.
        Without a guard these two go into the same insert and collide on the
        unique constraint, which fails the whole import."""
        entries = [entry(), *(video(n) for n in range(1, 10)), entry()]

        summary = import_youtube_export(session, takeout(*entries))

        assert summary.duplicates == 1
        assert summary.imported == 10


class TestSeparateHistories:
    def test_one_person_s_views_do_not_hide_another_s(self, session: Session):
        """The app is single-user today and every row has carried a user from
        the first commit so that stays a config change. A duplicate check that
        forgot the scope would let one account suppress another's history."""
        import_youtube_export(session, takeout(video(1), video(2)), user_id="someone")

        summary = import_youtube_export(session, takeout(video(1), video(2)), user_id="somebody")

        assert summary.imported == 2
        assert summary.duplicates == 0
        assert view_count(session) == 4


class TestABrokenUpload:
    def test_a_truncated_download_leaves_nothing_behind(self, session: Session):
        """Discovered part way through, with rows already pending. Abandoning
        the transaction is what stops half a history being stored under a
        summary that never arrived to describe it."""
        good = json.dumps([video(n) for n in range(800)]).encode("utf-8")

        with pytest.raises(YouTubeExportError):
            import_youtube_export(session, io.BytesIO(good[: len(good) // 2]))
        session.rollback()

        assert view_count(session) == 0
        assert session.scalar(select(func.count()).select_from(ImportRun)) == 0

    def test_the_wrong_file_is_refused_before_anything_is_written(self, session: Session):
        with pytest.raises(YouTubeExportError):
            import_youtube_export(session, io.BytesIO(b"<!DOCTYPE html><html>"))
        session.rollback()

        assert session.scalar(select(func.count()).select_from(ImportRun)) == 0

    def test_a_failed_import_does_not_spoil_the_next_one(self, session: Session):
        with pytest.raises(YouTubeExportError):
            import_youtube_export(session, io.BytesIO(b"not json at all"))
        session.rollback()

        summary = import_youtube_export(session, takeout(video(1)))

        assert summary.imported == 1

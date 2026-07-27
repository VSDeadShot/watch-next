"""Tests for reading Netflix's viewing-history exports.

Netflix ships history in two shapes. The full personal-data download is a zip of
about a dozen folders with the useful file buried at
``CONTENT_INTERACTION/ViewingActivity.csv`` (ten columns, UTC timestamps,
durations). The viewing-activity page offers a much thinner
``NetflixViewingHistory.csv`` with only ``Title,Date``.

Both are supported, because making someone find the right file inside the zip is
exactly the friction this project exists to remove.
"""

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.netflix_parser import (
    ExportFormat,
    NetflixExportError,
    SkipReason,
    parse_netflix_export,
)

FIXTURES = Path(__file__).parent / "fixtures"

FULL_HEADER = (
    "Profile Name,Start Time,Duration,Attributes,Title,"
    "Supplemental Video Type,Device Type,Bookmark,Latest Bookmark,Country"
)


def full_csv(*rows: str) -> bytes:
    """Build a full-format CSV with the given data rows."""
    return ("\n".join([FULL_HEADER, *rows]) + "\n").encode("utf-8")


def zipped(files: dict[str, bytes] | None = None) -> bytes:
    """Build an in-memory zip. Real zip bytes, not a mock."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in (files or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


class TestFormatDetection:
    def test_detects_the_full_export(self):
        result = parse_netflix_export((FIXTURES / "viewing_activity_full.csv").read_bytes())

        assert result.export_format is ExportFormat.FULL

    def test_detects_the_simple_export(self):
        result = parse_netflix_export(
            (FIXTURES / "netflix_viewing_history_simple.csv").read_bytes()
        )

        assert result.export_format is ExportFormat.SIMPLE

    def test_rejects_an_unrecognised_header_and_says_what_it_found(self):
        data = b"Film,When Watched\nInception,2024-01-01\n"

        with pytest.raises(NetflixExportError) as excinfo:
            parse_netflix_export(data)

        message = str(excinfo.value)
        # The user needs to know what was wrong with *their* file, not just that
        # something was.
        assert "Film" in message
        assert "When Watched" in message
        assert "Title" in message

    def test_rejects_a_file_with_no_header_at_all(self):
        with pytest.raises(NetflixExportError):
            parse_netflix_export(b"")

    def test_tolerates_a_utf8_byte_order_mark(self):
        """Netflix's exports are commonly BOM-prefixed; the BOM must not become
        part of the first column name."""
        data = b"\xef\xbb\xbf" + full_csv("Sam,2024-03-14 20:12:03,0:48:22,,Inception,,TV,,,IN")

        result = parse_netflix_export(data)

        assert result.export_format is ExportFormat.FULL
        assert len(result.events) == 1


class TestFullExportRows:
    def test_maps_a_row_onto_a_watch_event(self):
        data = full_csv(
            "Sam,2024-03-14 20:12:03,0:48:22,,Breaking Bad: Season 1: Pilot,,Smart TV,,,IN"
        )

        (event,) = parse_netflix_export(data).events

        assert event.raw_title == "Breaking Bad: Season 1: Pilot"
        assert event.watched_at == datetime(2024, 3, 14, 20, 12, 3, tzinfo=UTC)
        assert event.duration_seconds == 48 * 60 + 22
        assert event.profile_name == "Sam"
        assert event.device_type == "Smart TV"
        assert event.country == "IN"

    def test_start_time_is_treated_as_utc(self):
        """The column is documented as UTC, so the result must be tz-aware --
        naive datetimes would silently shift when compared to anything else."""
        data = full_csv("Sam,2024-03-14 20:12:03,1:00:00,,Inception,,TV,,,IN")

        (event,) = parse_netflix_export(data).events

        assert event.watched_at.tzinfo is not None
        assert event.watched_at.utcoffset().total_seconds() == 0

    def test_accepts_a_single_digit_hour(self):
        data = full_csv("Sam,2024-03-14 3:27:48,1:00:00,,Inception,,TV,,,IN")

        (event,) = parse_netflix_export(data).events

        assert event.watched_at.hour == 3

    def test_parses_durations_longer_than_a_day(self):
        """Left-running sessions produce hour counts above 23, which %H cannot
        parse -- so duration must not go through strptime."""
        data = full_csv("Sam,2024-03-14 20:12:03,26:00:00,,Inception,,TV,,,IN")

        (event,) = parse_netflix_export(data).events

        assert event.duration_seconds == 26 * 3600

    def test_counts_every_data_row_it_saw(self):
        result = parse_netflix_export((FIXTURES / "viewing_activity_full.csv").read_bytes())

        assert result.total_rows == 7


class TestDurationParsing:
    """Durations are read right-to-left, so shorter forms still yield a number.

    A duration that fails to parse becomes None, and None disables the
    too-short filter -- so anything remotely well-formed must produce a value,
    or a two-second view could slip through as a real one.
    """

    def test_reads_hours_minutes_seconds(self):
        data = full_csv("Sam,2024-03-14 20:12:03,1:02:03,,Inception,,TV,,,IN")

        (event,) = parse_netflix_export(data).events

        assert event.duration_seconds == 3600 + 120 + 3

    def test_reads_minutes_and_seconds(self):
        data = full_csv("Sam,2024-03-14 20:12:03,05:00,,Inception,,TV,,,IN")

        (event,) = parse_netflix_export(data).events

        assert event.duration_seconds == 300

    def test_reads_bare_seconds(self):
        data = full_csv("Sam,2024-03-14 20:12:03,90,,Inception,,TV,,,IN")

        (event,) = parse_netflix_export(data).events

        assert event.duration_seconds == 90

    def test_a_short_minutes_seconds_duration_is_still_filtered(self):
        """The regression this class exists for: an unparsed duration disabled
        the threshold, letting a two-second view through as genuine."""
        data = full_csv("Sam,2024-03-14 20:12:03,0:02,,Accidental Click,,TV,,,IN")

        result = parse_netflix_export(data, min_watch_seconds=60)

        assert result.events == ()
        assert result.skipped[SkipReason.TOO_SHORT] == 1

    def test_an_unreadable_duration_is_absent_rather_than_wrong(self):
        data = full_csv("Sam,2024-03-14 20:12:03,not a duration,,Inception,,TV,,,IN")

        (event,) = parse_netflix_export(data).events

        assert event.duration_seconds is None


class TestSkipping:
    """Rows are dropped for stated reasons, and the counts are reported.

    Silent filtering is how a user stops trusting an importer: they know they
    watched 400 things, the app says 250, and nothing explains the gap.
    """

    def test_skips_supplemental_videos(self):
        data = full_csv(
            "Sam,2024-03-14 20:12:03,0:02:00,,Real Show: Season 1: Ep,,TV,,,IN",
            "Sam,2024-03-14 19:00:00,0:02:00,,Some Trailer,TRAILER,TV,,,IN",
            "Sam,2024-03-14 18:00:00,0:02:00,,Some Recap,RECAP,TV,,,IN",
            "Sam,2024-03-14 17:00:00,0:02:00,,Some Hook,HOOK,TV,,,IN",
        )

        result = parse_netflix_export(data)

        assert len(result.events) == 1
        assert result.skipped[SkipReason.SUPPLEMENTAL_VIDEO] == 3

    def test_skips_views_shorter_than_the_threshold(self):
        data = full_csv(
            "Sam,2024-03-14 20:12:03,0:00:18,,Accidental Click,,TV,,,IN",
            "Sam,2024-03-14 19:00:00,0:05:00,,Actually Watched,,TV,,,IN",
        )

        result = parse_netflix_export(data, min_watch_seconds=60)

        assert [event.raw_title for event in result.events] == ["Actually Watched"]
        assert result.skipped[SkipReason.TOO_SHORT] == 1

    def test_keeps_a_view_exactly_at_the_threshold(self):
        data = full_csv("Sam,2024-03-14 20:12:03,0:01:00,,Sixty Seconds,,TV,,,IN")

        result = parse_netflix_export(data, min_watch_seconds=60)

        assert len(result.events) == 1

    def test_threshold_is_configurable(self):
        data = full_csv("Sam,2024-03-14 20:12:03,0:00:30,,Half A Minute,,TV,,,IN")

        assert len(parse_netflix_export(data, min_watch_seconds=10).events) == 1
        assert len(parse_netflix_export(data, min_watch_seconds=60).events) == 0

    def test_skips_rows_with_no_title(self):
        data = full_csv("Sam,2024-03-14 20:12:03,0:05:00,,,,TV,,,IN")

        result = parse_netflix_export(data)

        assert result.events == ()
        assert result.skipped[SkipReason.MISSING_TITLE] == 1

    def test_skips_rows_with_an_unreadable_timestamp_instead_of_crashing(self):
        data = full_csv(
            "Sam,not a date,0:05:00,,Bad Row,,TV,,,IN",
            "Sam,2024-03-14 20:12:03,0:05:00,,Good Row,,TV,,,IN",
        )

        result = parse_netflix_export(data)

        assert [event.raw_title for event in result.events] == ["Good Row"]
        assert result.skipped[SkipReason.BAD_TIMESTAMP] == 1

    def test_reports_a_total_across_all_reasons(self):
        result = parse_netflix_export((FIXTURES / "viewing_activity_full.csv").read_bytes())

        # Two supplemental (TRAILER, HOOK) and one 18-second view.
        assert result.skipped_total == 3
        assert len(result.events) == 4

    def test_a_header_only_file_yields_nothing_without_erroring(self):
        result = parse_netflix_export(full_csv())

        assert result.events == ()
        assert result.total_rows == 0
        assert result.skipped_total == 0


class TestSimpleExport:
    def test_maps_rows_onto_watch_events(self):
        result = parse_netflix_export(
            (FIXTURES / "netflix_viewing_history_simple.csv").read_bytes()
        )

        assert len(result.events) == 4
        assert result.events[0].raw_title == "Breaking Bad: Season 1: Pilot"

    def test_has_no_duration_to_report(self):
        """The simple export omits duration, so there is nothing to threshold on
        and no row may be dropped as too short."""
        result = parse_netflix_export(
            (FIXTURES / "netflix_viewing_history_simple.csv").read_bytes(),
            min_watch_seconds=3600,
        )

        assert all(event.duration_seconds is None for event in result.events)
        assert SkipReason.TOO_SHORT not in result.skipped
        assert len(result.events) == 4


class TestSimpleExportDateFormat:
    """The simple export's dates are locale-dependent and genuinely ambiguous.

    ``01/02/2024`` is 1 February in most of the world and 2 January in the US.
    Guessing silently would shift dates by up to eleven months, so the parser
    decides from evidence across the whole file and records what it assumed.
    """

    def test_infers_day_first_from_a_day_above_twelve(self):
        data = b"Title,Date\nInception,25/12/2023\nArrival,01/02/2024\n"

        result = parse_netflix_export(data)

        assert result.events[0].watched_at.date() == datetime(2023, 12, 25).date()
        # The same evidence resolves the ambiguous row too.
        assert result.events[1].watched_at.date() == datetime(2024, 2, 1).date()

    def test_infers_month_first_from_a_second_component_above_twelve(self):
        data = b"Title,Date\nInception,12/25/2023\nArrival,02/01/2024\n"

        result = parse_netflix_export(data)

        assert result.events[0].watched_at.date() == datetime(2023, 12, 25).date()
        assert result.events[1].watched_at.date() == datetime(2024, 2, 1).date()

    def test_records_an_assumption_when_nothing_disambiguates(self):
        data = b"Title,Date\nInception,01/02/2024\n"

        result = parse_netflix_export(data)

        assert result.assumptions
        assert any("day" in note.lower() for note in result.assumptions)

    def test_makes_no_assumption_when_the_evidence_is_clear(self):
        data = b"Title,Date\nInception,25/12/2023\n"

        assert parse_netflix_export(data).assumptions == ()

    def test_an_explicit_choice_overrides_inference(self):
        data = b"Title,Date\nInception,01/02/2024\n"

        result = parse_netflix_export(data, day_first=False)

        assert result.events[0].watched_at.date() == datetime(2024, 1, 2).date()
        assert result.assumptions == ()

    def test_accepts_iso_dates(self):
        data = b"Title,Date\nInception,2024-02-01\n"

        result = parse_netflix_export(data)

        assert result.events[0].watched_at.date() == datetime(2024, 2, 1).date()


class TestZipArchives:
    """The user should be able to hand over the download untouched."""

    def test_finds_viewing_activity_inside_the_netflix_zip(self):
        csv = (FIXTURES / "viewing_activity_full.csv").read_bytes()
        archive = zipped({"CONTENT_INTERACTION/ViewingActivity.csv": csv})

        result = parse_netflix_export(archive)

        assert result.export_format is ExportFormat.FULL
        assert len(result.events) == 4

    def test_ignores_the_other_dozen_folders(self):
        csv = (FIXTURES / "viewing_activity_full.csv").read_bytes()
        archive = zipped(
            {
                "ACCOUNT/AccountDetails.csv": b"Field,Value\nEmail,someone@example.com\n",
                "CLICKSTREAM/Clickstream.csv": b"a,b\n1,2\n",
                "CONTENT_INTERACTION/ViewingActivity.csv": csv,
                "CONTENT_INTERACTION/Ratings.csv": b"Title,Rating\nInception,5\n",
            }
        )

        result = parse_netflix_export(archive)

        assert len(result.events) == 4

    def test_finds_the_simple_export_inside_a_zip(self):
        csv = (FIXTURES / "netflix_viewing_history_simple.csv").read_bytes()
        archive = zipped({"NetflixViewingHistory.csv": csv})

        result = parse_netflix_export(archive)

        assert result.export_format is ExportFormat.SIMPLE

    def test_explains_itself_when_the_zip_holds_no_history(self):
        archive = zipped({"ACCOUNT/AccountDetails.csv": b"Field,Value\nEmail,x@y.com\n"})

        with pytest.raises(NetflixExportError) as excinfo:
            parse_netflix_export(archive)

        assert "ViewingActivity" in str(excinfo.value)

    def test_reports_an_empty_archive_clearly(self):
        with pytest.raises(NetflixExportError):
            parse_netflix_export(zipped())

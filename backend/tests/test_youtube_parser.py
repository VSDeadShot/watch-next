"""Tests for reading Google Takeout's YouTube watch history.

The file is a single JSON array, routinely 50-200 MB, so the reader streams and
these tests hold it to that: :class:`TestItStreams` fails if the implementation
ever reads the whole document to produce the first event.

The other running theme is that Takeout is localized. Titles, the
``activityControls`` labels and the "video has been removed" sentence are all
translated, so anything that decides an entry's fate by matching English text
would quietly drop every entry in a Spanish export. The structural signals --
a missing ``titleUrl``, a URL that is not a video -- are the same in every
language, and those are what the reader uses.
"""

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.youtube_parser import (
    SkipReason,
    YouTubeExportError,
    open_youtube_export,
    youtube_video_id,
)

FIXTURES = Path(__file__).parent / "fixtures"


def stream_of(*entries: dict) -> io.BytesIO:
    """A Takeout document containing exactly these entries."""
    return io.BytesIO(json.dumps(list(entries)).encode("utf-8"))


def watch_entry(**overrides) -> dict:
    """A well-formed watch entry, before whatever the test wants to break."""
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


def read_all(*entries: dict):
    """Open a document of these entries and drain it, returning the reader."""
    export = open_youtube_export(stream_of(*entries))
    events = list(export.events())
    return export, events


class TestOpeningTheFile:
    def test_reads_the_fixture(self):
        with (FIXTURES / "youtube_takeout_history.json").open("rb") as stream:
            export = open_youtube_export(stream)
            events = list(export.events())

        assert export.total_entries == 9
        assert len(events) == 4

    def test_rejects_the_html_export_and_says_what_to_do(self):
        """Takeout offers YouTube history as HTML by default, so this is the
        most likely wrong file anybody uploads. It must not read as an
        unhelpful parse error."""
        html = b"<!DOCTYPE html>\n<html lang='en'><head><title>Watch history</title>"

        with pytest.raises(YouTubeExportError) as excinfo:
            open_youtube_export(io.BytesIO(html))

        message = str(excinfo.value)
        assert "HTML" in message
        assert "JSON" in message

    def test_rejects_html_that_does_not_start_with_a_doctype(self):
        with pytest.raises(YouTubeExportError):
            open_youtube_export(io.BytesIO(b"\n  <html><body>Watch history</body></html>"))

    def test_rejects_the_takeout_archive_and_says_which_file_to_send(self):
        """The Netflix importer takes its zip as downloaded, so somebody who has
        just done that will try it here. A Takeout archive can be gigabytes and
        Google splits large ones, so this one really does want the file itself
        -- which makes the refusal worth explaining rather than just issuing."""
        with pytest.raises(YouTubeExportError) as excinfo:
            open_youtube_export(io.BytesIO(b"PK\x03\x04" + b"\x00" * 40))

        message = str(excinfo.value)
        assert "archive" in message
        assert "Unzip" in message
        assert "watch-history.json" in message

    def test_rejects_a_json_document_that_is_not_an_array(self):
        with pytest.raises(YouTubeExportError) as excinfo:
            open_youtube_export(io.BytesIO(b'{"entries": []}'))

        assert "array" in str(excinfo.value).lower()

    def test_rejects_an_empty_upload(self):
        with pytest.raises(YouTubeExportError):
            open_youtube_export(io.BytesIO(b""))

    def test_rejects_a_file_of_nothing_but_whitespace(self):
        with pytest.raises(YouTubeExportError):
            open_youtube_export(io.BytesIO(b" " * 5000))

    def test_gives_up_on_a_stream_that_is_only_ever_whitespace(self):
        """The search for the first real character has to stop somewhere. This
        is an upload endpoint, so "keep reading until something turns up" is an
        invitation to hand it a stream that never does."""

        class Endless:
            def read(self, size=-1):
                return b" " * (size if size and size > 0 else 1024)

        with pytest.raises(YouTubeExportError):
            open_youtube_export(Endless())

    def test_tolerates_more_leading_whitespace_than_it_sniffs(self):
        """The check reads a fixed chunk off the front. A document whose first
        chunk is all blanks says nothing yet, and must not be reported as empty
        -- that would send somebody hunting for a problem with their export."""
        data = b"\n" * 4000 + json.dumps([watch_entry()]).encode("utf-8")

        export = open_youtube_export(io.BytesIO(data))

        assert len(list(export.events())) == 1

    def test_tolerates_a_byte_order_mark(self):
        data = b"\xef\xbb\xbf" + json.dumps([watch_entry()]).encode("utf-8")

        export = open_youtube_export(io.BytesIO(data))

        assert len(list(export.events())) == 1

    def test_accepts_an_empty_history(self):
        export, events = read_all()

        assert events == []
        assert export.total_entries == 0

    def test_truncated_json_fails_as_a_bad_export_not_a_crash(self):
        """A half-downloaded file must reach the user as a 400-shaped error
        rather than whatever the JSON library happens to raise."""
        good = json.dumps([watch_entry(), watch_entry()]).encode("utf-8")
        export = open_youtube_export(io.BytesIO(good[: len(good) // 2]))

        with pytest.raises(YouTubeExportError):
            list(export.events())

    def test_the_parse_error_is_one_readable_line(self):
        """ijson draws an ASCII caret under the offending byte, which is helpful
        in a terminal and looks like a broken page when it lands in a sentence
        on screen."""
        good = json.dumps([watch_entry(), watch_entry()]).encode("utf-8")
        export = open_youtube_export(io.BytesIO(good[: len(good) // 2]))

        with pytest.raises(YouTubeExportError) as excinfo:
            list(export.events())

        message = str(excinfo.value)
        assert "\n" not in message
        assert "^" not in message
        assert "partial download" in message


class TestTheFixture:
    """The hand-written sample, read end to end.

    It carries one of every case on purpose, so these counts are the summary a
    user would be shown for it.
    """

    @pytest.fixture
    def export(self):
        with (FIXTURES / "youtube_takeout_history.json").open("rb") as stream:
            reader = open_youtube_export(stream)
            reader.collected = list(reader.events())
        return reader

    def test_every_entry_is_either_kept_or_counted(self, export):
        """The invariant the summary rests on. Numbers that do not reconcile
        tell somebody things went missing without telling them what."""
        assert export.kept + export.skipped_total == export.total_entries

    def test_counts_each_exclusion_by_reason(self, export):
        assert dict(export.skipped) == {
            SkipReason.UNAVAILABLE_VIDEO: 1,
            SkipReason.ADVERT: 1,
            SkipReason.NOT_A_VIDEO: 1,
            SkipReason.BAD_TIMESTAMP: 1,
            SkipReason.MISSING_TITLE: 1,
        }

    def test_keeps_the_real_views_in_file_order(self, export):
        assert [event.video_id for event in export.collected] == [
            "aaaaaaaaaaa",
            "bbbbbbbbbbb",
            "ccccccccccc",
            "aaaaaaaaaaa",
        ]

    def test_reads_a_whole_event(self, export):
        first = export.collected[0]

        assert first.video_id == "aaaaaaaaaaa"
        assert first.title == "Refactoring a legacy service without downtime"
        assert first.channel_name == "Sample Engineering Channel"
        assert first.watched_at == datetime(2024, 3, 14, 20, 12, 3, 415000, tzinfo=UTC)

    def test_an_entry_without_subtitles_has_no_channel(self, export):
        assert export.collected[1].channel_name is None

    def test_makes_no_assumptions_about_an_english_export(self, export):
        assert export.assumptions == ()


class TestSkipping:
    """Nothing is dropped silently: every exclusion is counted by reason."""

    def test_skips_a_removed_video_by_its_missing_url(self):
        """Not by matching "a video that has been removed", which Google
        translates. The absent link is the same fact in every language."""
        export, events = read_all(watch_entry(title="Ha visto un vídeo eliminado", titleUrl=None))

        assert events == []
        assert export.skipped[SkipReason.UNAVAILABLE_VIDEO] == 1

    def test_skips_an_entry_with_no_url_key_at_all(self):
        entry = watch_entry()
        del entry["titleUrl"]

        export, events = read_all(entry)

        assert events == []
        assert export.skipped[SkipReason.UNAVAILABLE_VIDEO] == 1

    def test_skips_adverts(self):
        export, events = read_all(watch_entry(details=[{"name": "From Google Ads"}]))

        assert events == []
        assert export.skipped[SkipReason.ADVERT] == 1

    def test_keeps_an_entry_whose_details_say_something_else(self):
        _, events = read_all(watch_entry(details=[{"name": "From Google Search"}]))

        assert len(events) == 1

    def test_skips_a_search_rather_than_counting_it_as_a_view(self):
        """Whether searches are in the file depends on which Takeout export was
        uploaded. Their URL is a results page, so no video id comes out of it
        and no English label had to be matched to notice."""
        export, events = read_all(
            watch_entry(
                title="Searched for how to poach an egg",
                titleUrl="https://www.youtube.com/results?search_query=how+to+poach+an+egg",
                activityControls=["YouTube search history"],
            )
        )

        assert events == []
        assert export.skipped[SkipReason.NOT_A_VIDEO] == 1

    def test_skips_a_link_that_leaves_youtube(self):
        export, events = read_all(watch_entry(titleUrl="https://example.com/watch?v=aaaaaaaaaaa"))

        assert events == []
        assert export.skipped[SkipReason.NOT_A_VIDEO] == 1

    def test_skips_an_unreadable_timestamp(self):
        export, events = read_all(watch_entry(time="16 March, sometime after lunch"))

        assert events == []
        assert export.skipped[SkipReason.BAD_TIMESTAMP] == 1

    def test_skips_an_entry_with_no_time_at_all(self):
        entry = watch_entry()
        del entry["time"]

        export, _ = read_all(entry)

        assert export.skipped[SkipReason.BAD_TIMESTAMP] == 1

    def test_skips_an_empty_title(self):
        export, events = read_all(watch_entry(title=""))

        assert events == []
        assert export.skipped[SkipReason.MISSING_TITLE] == 1

    def test_skips_a_title_that_is_only_the_watched_prefix(self):
        export, events = read_all(watch_entry(title="Watched "))

        assert events == []
        assert export.skipped[SkipReason.MISSING_TITLE] == 1

    def test_an_entry_that_is_not_an_object_is_counted_not_fatal(self):
        """Takeout has no reason to emit one, but a single stray value must not
        end an import that has already read a hundred thousand good rows."""
        export = open_youtube_export(io.BytesIO(b'["oops", null]'))
        events = list(export.events())

        assert events == []
        assert export.total_entries == 2
        assert export.skipped[SkipReason.MALFORMED_ENTRY] == 2


class TestTitles:
    def test_strips_the_watched_prefix(self):
        _, events = read_all(watch_entry(title="Watched How to fold a fitted sheet"))

        assert events[0].title == "How to fold a fitted sheet"

    def test_leaves_a_title_that_merely_starts_with_the_word(self):
        """Without the trailing space, the word is part of the title itself."""
        _, events = read_all(watch_entry(title="Watchedmen: a review"))

        assert events[0].title == "Watchedmen: a review"

    def test_keeps_a_prefix_it_cannot_recognise(self):
        _, events = read_all(watch_entry(title="Ha visto Cómo doblar una sábana"))

        assert events[0].title == "Ha visto Cómo doblar una sábana"

    def test_says_so_when_the_export_is_not_in_english(self):
        """The prefix is translated and there is no way to know the word for
        "Watched" in every language, so the titles keep it. Silently leaving it
        on would look like the data is wrong; saying so makes it a known cost."""
        export, _ = read_all(
            watch_entry(title="Ha visto Uno"),
            watch_entry(title="Ha visto Dos"),
            watch_entry(title="Ha visto Tres"),
        )

        assert len(export.assumptions) == 1
        assert "Watched" in export.assumptions[0]

    def test_a_tie_leaves_the_export_treated_as_english(self):
        """Half and half is not evidence of anything, and the claim being made
        is that the file is in another language. It has to be the majority."""
        export, _ = read_all(
            watch_entry(title="Watched One"),
            watch_entry(title="Ha visto Dos"),
        )

        assert export.assumptions == ()

    def test_a_single_odd_title_is_not_treated_as_another_language(self):
        export, _ = read_all(
            watch_entry(title="Watched One"),
            watch_entry(title="Watched Two"),
            watch_entry(title="Ha visto Tres"),
        )

        assert export.assumptions == ()


class TestTimestamps:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2024-03-14T20:12:03.415Z", datetime(2024, 3, 14, 20, 12, 3, 415000, tzinfo=UTC)),
            ("2024-03-14T20:12:03Z", datetime(2024, 3, 14, 20, 12, 3, tzinfo=UTC)),
            ("2024-03-14T20:12:03.123456Z", datetime(2024, 3, 14, 20, 12, 3, 123456, tzinfo=UTC)),
            # An offset is the same instant expressed differently, and must land
            # on the same moment or the same view imports twice.
            ("2024-03-15T01:42:03+05:30", datetime(2024, 3, 14, 20, 12, 3, tzinfo=UTC)),
        ],
    )
    def test_reads_takeout_timestamps_as_utc(self, value, expected):
        _, events = read_all(watch_entry(time=value))

        assert events[0].watched_at == expected

    def test_every_timestamp_is_timezone_aware(self):
        """A naive datetime would be read as the importing machine's local time,
        which would make the stored history depend on the server's clock."""
        _, events = read_all(watch_entry())

        assert events[0].watched_at.tzinfo is not None

    def test_converts_an_offset_rather_than_carrying_it(self):
        """Equality alone does not catch this: an offset datetime and its UTC
        form are the same instant and compare equal. It still matters, because
        anything reading the clock off the value -- what hour somebody watches
        at, which day it counted as -- would read 01:42 for a 20:12 view."""
        _, events = read_all(watch_entry(time="2024-03-15T01:42:03+05:30"))

        assert events[0].watched_at.utcoffset() == timedelta(0)
        assert events[0].watched_at.hour == 20

    def test_skips_a_timestamp_with_no_timezone_at_all(self):
        """There is no honest way to place a bare local time on the clock, and
        guessing UTC would put views hours from where they happened. Counted as
        unreadable, which is what it is."""
        export, events = read_all(watch_entry(time="2024-03-14T20:12:03"))

        assert events == []
        assert export.skipped[SkipReason.BAD_TIMESTAMP] == 1


class TestVideoIds:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.youtube.com/watch?v=aaaaaaaaaaa", "aaaaaaaaaaa"),
            ("http://www.youtube.com/watch?v=aaaaaaaaaaa", "aaaaaaaaaaa"),
            ("https://youtube.com/watch?v=aaaaaaaaaaa", "aaaaaaaaaaa"),
            ("https://m.youtube.com/watch?v=aaaaaaaaaaa", "aaaaaaaaaaa"),
            ("https://music.youtube.com/watch?v=aaaaaaaaaaa", "aaaaaaaaaaa"),
            ("https://www.youtube.com/watch?v=aaaaaaaaaaa&t=90s", "aaaaaaaaaaa"),
            ("https://www.youtube.com/shorts/ccccccccccc", "ccccccccccc"),
            ("https://www.youtube.com/live/ddddddddddd", "ddddddddddd"),
            ("https://youtu.be/eeeeeeeeeee", "eeeeeeeeeee"),
            ("HTTPS://WWW.YOUTUBE.COM/watch?v=aaaaaaaaaaa", "aaaaaaaaaaa"),
            # A subdomain nobody thought of, and the retired one that is still
            # sitting in older histories.
            ("https://gaming.youtube.com/watch?v=aaaaaaaaaaa", "aaaaaaaaaaa"),
            # A port and a bare scheme are both ordinary URLs, and neither says
            # anything about whether this is a video.
            ("https://www.youtube.com:443/watch?v=aaaaaaaaaaa", "aaaaaaaaaaa"),
            ("//www.youtube.com/watch?v=aaaaaaaaaaa", "aaaaaaaaaaa"),
            # Not videos.
            ("https://www.youtube.com/results?search_query=eggs", None),
            ("https://www.youtube.com/channel/UC1", None),
            ("https://www.youtube.com/watch?list=PL1", None),
            ("https://www.youtube.com/watch?v=", None),
            ("https://www.youtube.com/shorts/", None),
            ("https://example.com/watch?v=aaaaaaaaaaa", None),
            ("https://notyoutube.com/watch?v=aaaaaaaaaaa", None),
            # Suffix matching has to be anchored on the dot or this is a video.
            ("https://youtube.com.example.net/watch?v=aaaaaaaaaaa", None),
            ("", None),
            ("nonsense", None),
        ],
    )
    def test_pulls_the_id_out_of_a_url(self, url, expected):
        assert youtube_video_id(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=../../etc/passwd",
            "https://www.youtube.com/watch?v=a%20b",
            "https://www.youtube.com/watch?v=" + "x" * 400,
        ],
    )
    def test_refuses_an_id_that_is_not_shaped_like_one(self, url):
        """Whatever these came out of, it was not a video being played. They
        would otherwise be stored and put back into links as if they were."""
        assert youtube_video_id(url) is None


class TestItStreams:
    """The point of the step.

    Takeout's watch history is routinely 50-200 MB. Reading it whole works fine
    on the ten-entry fixture above and falls over on a real one, so the
    difference has to be asserted rather than assumed.
    """

    class Counting(io.BytesIO):
        """A stream that remembers how much of itself was read."""

        def __init__(self, data: bytes) -> None:
            super().__init__(data)
            self.bytes_read = 0

        def read(self, size=-1):
            chunk = super().read(size)
            self.bytes_read += len(chunk)
            return chunk

    def big_document(self, entries: int = 5000) -> "TestItStreams.Counting":
        payload = json.dumps([watch_entry() for _ in range(entries)]).encode("utf-8")
        return self.Counting(payload)

    def test_the_first_event_does_not_read_the_whole_file(self):
        stream = self.big_document()
        # Small next to a real export, big enough that reading it whole is
        # unmistakable in the numbers below.
        assert len(stream.getvalue()) > 1_000_000

        export = open_youtube_export(stream)
        next(export.events())

        assert stream.bytes_read < len(stream.getvalue()) // 4

    def test_reading_all_of_it_still_produces_every_event(self):
        stream = self.big_document(entries=1000)

        export = open_youtube_export(stream)

        assert sum(1 for _ in export.events()) == 1000

    def test_counts_are_only_final_once_the_reader_is_drained(self):
        """Documented rather than incidental: a caller that reports the summary
        before finishing the loop would report zero of everything."""
        export = open_youtube_export(stream_of(watch_entry(), watch_entry()))

        assert export.total_entries == 0

        events = export.events()
        next(events)
        assert export.total_entries == 1

        list(events)
        assert export.total_entries == 2

    def test_a_reader_refuses_to_be_read_twice(self):
        """The stream is gone by then, so a second pass would find the end of
        the file and report the export as corrupt -- blaming somebody's download
        for a mistake in the code calling it."""
        export = open_youtube_export(stream_of(watch_entry()))
        list(export.events())

        with pytest.raises(RuntimeError, match="already been read"):
            list(export.events())

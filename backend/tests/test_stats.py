"""Tests for counting a watch history honestly.

Most of these are about the two ways a raw count lies: letting one binge speak
for a whole taste, and reporting a time nobody measured. The arithmetic itself
is not the interesting part.
"""

from datetime import UTC, date, datetime

from app.core.stats import (
    EARLIEST_RELEASE_YEAR,
    Count,
    MonthCount,
    SessionRecord,
    VideoRecord,
    describe_history,
    describe_youtube,
    months_between,
)
from app.core.taste import MOVIE, SHOW


def at(year: int, month: int, day: int = 1) -> datetime:
    return datetime(year, month, day, 20, 0, tzinfo=UTC)


def session(
    title_id: int = 1,
    *,
    when: datetime | None = None,
    title: str = "Something",
    object_type: str = MOVIE,
    genres: tuple[str, ...] = (),
    release_year: int | None = None,
    duration_seconds: int | None = None,
) -> SessionRecord:
    return SessionRecord(
        title_id=title_id,
        watched_at=when or at(2026, 1),
        title=title,
        object_type=object_type,
        genres=genres,
        release_year=release_year,
        duration_seconds=duration_seconds,
    )


def video(
    *,
    when: datetime | None = None,
    channel_name: str | None = "Some Channel",
    video_id: str = "abc123",
) -> VideoRecord:
    return VideoRecord(watched_at=when or at(2026, 1), channel_name=channel_name, video_id=video_id)


class TestAnEmptyHistory:
    """Every caller runs before the first import, so nothing here may raise."""

    def test_counts_nothing(self):
        stats = describe_history([])

        assert stats.titles == 0
        assert stats.sessions == 0
        assert stats.movies == 0
        assert stats.series == 0

    def test_has_no_dates(self):
        stats = describe_history([])

        assert stats.first_watched is None
        assert stats.last_watched is None

    def test_has_no_lists(self):
        stats = describe_history([])

        assert stats.top_genres == ()
        assert stats.decades == ()
        assert stats.top_titles == ()
        assert stats.by_month == ()

    def test_does_not_claim_nothing_was_watched_for_no_time(self):
        """Null, not zero. Nothing was measured, which is not "nothing ran"."""
        stats = describe_history([])

        assert stats.minutes_watched is None
        assert stats.sessions_timed == 0

    def test_no_youtube_is_not_an_error(self):
        stats = describe_youtube([])

        assert stats.views == 0
        assert stats.videos == 0
        assert stats.channels == 0
        assert stats.top_channels == ()


class TestABingeIsOneTitleAndManySessions:
    """Both numbers are true and they answer different questions."""

    def test_counts_distinct_titles(self):
        stats = describe_history(
            [session(1), session(1), session(1), session(2)],
        )

        assert stats.titles == 2

    def test_counts_every_session(self):
        stats = describe_history(
            [session(1), session(1), session(1), session(2)],
        )

        assert stats.sessions == 4

    def test_splits_films_and_series_by_title_not_by_session(self):
        """Thirty episodes of one show is one series, not thirty."""
        episodes = [session(1, object_type=SHOW) for _ in range(30)]
        stats = describe_history([*episodes, session(2, object_type=MOVIE)])

        assert stats.series == 1
        assert stats.movies == 1

    def test_films_and_series_account_for_every_title(self):
        stats = describe_history(
            [
                session(1, object_type=SHOW),
                session(2, object_type=MOVIE),
                session(3, object_type=MOVIE),
            ]
        )

        assert stats.movies + stats.series == stats.titles


class TestTimeWatched:
    """Measured, never inferred from a runtime."""

    def test_adds_up_what_actually_ran(self):
        stats = describe_history(
            [session(1, duration_seconds=3600), session(2, duration_seconds=1800)]
        )

        assert stats.minutes_watched == 90

    def test_says_how_many_sessions_the_figure_rests_on(self):
        stats = describe_history(
            [
                session(1, duration_seconds=3600),
                session(2, duration_seconds=1800),
                session(3),
            ]
        )

        assert stats.sessions == 3
        assert stats.sessions_timed == 2

    def test_a_session_with_no_duration_adds_no_time(self):
        """Rather than being read as zero minutes, which would be a claim."""
        timed = describe_history([session(1, duration_seconds=3600)])
        mixed = describe_history([session(1, duration_seconds=3600), session(2)])

        assert mixed.minutes_watched == timed.minutes_watched

    def test_a_history_with_no_durations_at_all_reports_nothing(self):
        stats = describe_history([session(1), session(2)])

        assert stats.minutes_watched is None
        assert stats.sessions == 2

    def test_ignores_a_negative_duration(self):
        stats = describe_history(
            [session(1, duration_seconds=3600), session(2, duration_seconds=-500)]
        )

        assert stats.minutes_watched == 60

    def test_rounds_down_rather_than_inventing_a_minute(self):
        stats = describe_history([session(1, duration_seconds=119)])

        assert stats.minutes_watched == 1

    def test_a_session_that_ran_for_no_time_is_not_a_timed_session(self):
        """It contributes no minutes either way, so the only thing it could
        change is the count the figure claims to rest on -- and a zero-length
        session is not evidence of anything having been measured."""
        stats = describe_history(
            [session(1, duration_seconds=3600), session(2, duration_seconds=0)]
        )

        assert stats.minutes_watched == 60
        assert stats.sessions_timed == 1


class TestWhenItHappened:
    def test_finds_the_ends_of_the_history(self):
        stats = describe_history(
            [
                session(1, when=at(2025, 6, 3)),
                session(2, when=at(2023, 2, 9)),
                session(3, when=at(2026, 1, 5)),
            ]
        )

        assert stats.first_watched == at(2023, 2, 9)
        assert stats.last_watched == at(2026, 1, 5)

    def test_one_session_is_both_ends(self):
        stats = describe_history([session(1, when=at(2025, 6, 3))])

        assert stats.first_watched == at(2025, 6, 3)
        assert stats.last_watched == at(2025, 6, 3)


class TestAcrossTheMonths:
    def test_buckets_sessions_by_month(self):
        stats = describe_history(
            [
                session(1, when=at(2026, 1, 2)),
                session(2, when=at(2026, 1, 28)),
                session(3, when=at(2026, 2, 4)),
            ]
        )

        assert stats.by_month == (
            MonthCount(date(2026, 1, 1), 2),
            MonthCount(date(2026, 2, 1), 1),
        )

    def test_a_month_with_nothing_in_it_is_still_a_month(self):
        """A gap in viewing is information. A chart that skipped it would draw
        a lie about the shape of the year."""
        stats = describe_history([session(1, when=at(2026, 1, 5)), session(2, when=at(2026, 4, 5))])

        assert [entry.month for entry in stats.by_month] == [
            date(2026, 1, 1),
            date(2026, 2, 1),
            date(2026, 3, 1),
            date(2026, 4, 1),
        ]
        assert [entry.count for entry in stats.by_month] == [1, 0, 0, 1]

    def test_spans_a_year_boundary(self):
        stats = describe_history(
            [session(1, when=at(2025, 11, 5)), session(2, when=at(2026, 2, 5))]
        )

        assert [entry.month for entry in stats.by_month] == [
            date(2025, 11, 1),
            date(2025, 12, 1),
            date(2026, 1, 1),
            date(2026, 2, 1),
        ]

    def test_every_session_lands_in_exactly_one_month(self):
        records = [session(i, when=at(2026, 1 + i % 6, 3)) for i in range(24)]
        stats = describe_history(records)

        assert sum(entry.count for entry in stats.by_month) == stats.sessions

    def test_months_between_is_inclusive_at_both_ends(self):
        assert months_between(at(2026, 3, 20), at(2026, 5, 1)) == [
            date(2026, 3, 1),
            date(2026, 4, 1),
            date(2026, 5, 1),
        ]

    def test_months_between_one_month_is_that_month(self):
        assert months_between(at(2026, 3, 2), at(2026, 3, 28)) == [date(2026, 3, 1)]


class TestGenres:
    def test_counts_a_genre_once_per_title_however_often_it_was_watched(self):
        """The whole point. Sixty episodes of one sitcom is one vote for comedy."""
        binge = [session(1, object_type=SHOW, genres=("cmy",)) for _ in range(60)]
        stats = describe_history([*binge, session(2, genres=("hrr",))])

        assert stats.top_genres == (Count("cmy", 1), Count("hrr", 1))

    def test_a_title_counts_for_each_of_its_genres(self):
        stats = describe_history([session(1, genres=("cmy", "drm"))])

        assert {entry.label for entry in stats.top_genres} == {"cmy", "drm"}
        assert all(entry.count == 1 for entry in stats.top_genres)

    def test_ranks_the_commonest_first(self):
        stats = describe_history(
            [
                session(1, genres=("drm",)),
                session(2, genres=("drm",)),
                session(3, genres=("drm",)),
                session(4, genres=("cmy",)),
                session(5, genres=("cmy",)),
                session(6, genres=("hrr",)),
            ]
        )

        assert [entry.label for entry in stats.top_genres] == ["drm", "cmy", "hrr"]
        assert [entry.count for entry in stats.top_genres] == [3, 2, 1]

    def test_breaks_ties_on_the_name_so_the_same_history_reads_the_same_way(self):
        stats = describe_history([session(1, genres=("hrr",)), session(2, genres=("cmy",))])

        assert [entry.label for entry in stats.top_genres] == ["cmy", "hrr"]

    def test_keeps_only_the_top_few(self):
        records = [session(i, genres=(f"g{i:02d}",)) for i in range(20)]
        stats = describe_history(records, top=3)

        assert len(stats.top_genres) == 3

    def test_the_same_genre_listed_twice_on_one_title_is_still_one_vote(self):
        """A catalogue row that repeats itself must not outvote one that does
        not. The rule is one title, one vote, and it cannot depend on the
        catalogue being tidy."""
        stats = describe_history([session(1, genres=("cmy", "cmy")), session(2, genres=("drm",))])

        assert stats.top_genres == (Count("cmy", 1), Count("drm", 1))

    def test_a_title_with_no_genres_contributes_nothing(self):
        stats = describe_history([session(1), session(2, genres=("cmy",))])

        assert stats.top_genres == (Count("cmy", 1),)


class TestDecades:
    def test_groups_release_years_into_decades(self):
        stats = describe_history(
            [
                session(1, release_year=1994),
                session(2, release_year=1999),
                session(3, release_year=2003),
            ]
        )

        assert stats.decades == (Count("1990", 2), Count("2000", 1))

    def test_a_year_ending_in_zero_starts_its_decade(self):
        stats = describe_history([session(1, release_year=2000)])

        assert stats.decades == (Count("2000", 1),)

    def test_counts_a_decade_once_per_title(self):
        binge = [session(1, object_type=SHOW, release_year=2015) for _ in range(40)]
        stats = describe_history(binge)

        assert stats.decades == (Count("2010", 1),)

    def test_a_year_that_is_not_a_date_is_left_out_like_a_missing_one(self):
        """A zero or a single digit is a bad row, not a release year, and a bar
        labelled "0" on a histogram reads as broken rather than informative."""
        stats = describe_history(
            [session(1, release_year=0), session(2, release_year=3), session(3, release_year=1994)]
        )

        assert stats.decades == (Count("1990", 1),)

    def test_the_earliest_plausible_year_is_itself_plausible(self):
        """The boundary is inclusive. Written against the constant rather than
        the number so that moving the threshold does not silently move which
        side of it the boundary falls on."""
        stats = describe_history([session(1, release_year=EARLIEST_RELEASE_YEAR)])

        assert stats.decades == (Count(str(EARLIEST_RELEASE_YEAR // 10 * 10), 1),)

    def test_a_year_that_has_not_happened_yet_is_kept(self):
        """The catalogue genuinely carries announced titles, so there is no
        upper bound to guess at."""
        stats = describe_history([session(1, release_year=2031)])

        assert stats.decades == (Count("2030", 1),)

    def test_a_title_with_no_year_is_left_out_rather_than_guessed_at(self):
        stats = describe_history([session(1), session(2, release_year=1988)])

        assert stats.decades == (Count("1980", 1),)

    def test_stay_in_order_even_when_a_later_decade_is_the_bigger_one(self):
        """The one case that tells a date apart from a ranking. Sorted by size
        this would read 2010 first, and a histogram whose axis is reordered by
        its own bars has stopped being a histogram."""
        stats = describe_history(
            [
                session(1, release_year=1985),
                session(2, release_year=2011),
                session(3, release_year=2014),
                session(4, release_year=2017),
            ]
        )

        assert [entry.label for entry in stats.decades] == ["1980", "2010"]
        assert [entry.count for entry in stats.decades] == [1, 3]

    def test_decades_read_oldest_first(self):
        stats = describe_history(
            [
                session(1, release_year=2021),
                session(2, release_year=1977),
                session(3, release_year=1995),
            ]
        )

        assert [entry.label for entry in stats.decades] == ["1970", "1990", "2020"]


class TestWhatWasWatchedMost:
    def test_ranks_titles_by_how_many_sessions_went_into_them(self):
        stats = describe_history(
            [
                *[session(1, title="The Office", object_type=SHOW) for _ in range(12)],
                *[session(2, title="Heat") for _ in range(3)],
                session(3, title="Sicario"),
            ]
        )

        assert [entry.title for entry in stats.top_titles] == ["The Office", "Heat", "Sicario"]
        assert [entry.sessions for entry in stats.top_titles] == [12, 3, 1]

    def test_says_which_kind_of_thing_each_one_is(self):
        """Twelve sessions of a series is twelve episodes; twelve of a film is
        having watched it twelve times. The list must not invite the comparison
        without saying which is which."""
        stats = describe_history(
            [session(1, title="The Office", object_type=SHOW), session(2, title="Heat")]
        )

        by_title = {entry.title: entry.object_type for entry in stats.top_titles}
        assert by_title == {"The Office": SHOW, "Heat": MOVIE}

    def test_carries_the_id_so_a_row_can_be_linked_to(self):
        stats = describe_history([session(7, title="Heat")])

        assert stats.top_titles[0].title_id == 7

    def test_breaks_ties_on_the_title(self):
        stats = describe_history([session(1, title="Sicario"), session(2, title="Heat")])

        assert [entry.title for entry in stats.top_titles] == ["Heat", "Sicario"]

    def test_keeps_only_the_top_few(self):
        records = [session(i, title=f"Film {i:02d}") for i in range(20)]
        stats = describe_history(records, top=5)

        assert len(stats.top_titles) == 5


class TestYouTube:
    def test_counts_views_and_distinct_videos_separately(self):
        """Returning to the same video three times is the interesting bit."""
        stats = describe_youtube([video(video_id="a"), video(video_id="a"), video(video_id="b")])

        assert stats.views == 3
        assert stats.videos == 2

    def test_counts_distinct_channels(self):
        stats = describe_youtube(
            [
                video(channel_name="Tom Scott", video_id="a"),
                video(channel_name="Tom Scott", video_id="b"),
                video(channel_name="Veritasium", video_id="c"),
            ]
        )

        assert stats.channels == 2

    def test_ranks_channels_by_views_not_by_videos(self):
        stats = describe_youtube(
            [
                video(channel_name="Tom Scott", video_id="a"),
                video(channel_name="Tom Scott", video_id="a"),
                video(channel_name="Tom Scott", video_id="a"),
                video(channel_name="Veritasium", video_id="b"),
                video(channel_name="Veritasium", video_id="c"),
            ]
        )

        assert stats.top_channels == (Count("Tom Scott", 3), Count("Veritasium", 2))

    def test_a_view_with_no_channel_is_not_counted_as_a_channel(self):
        """Takeout omits the channel for a video that has since been removed."""
        stats = describe_youtube([video(channel_name=None), video(channel_name="Tom Scott")])

        assert stats.views == 2
        assert stats.channels == 1
        assert stats.top_channels == (Count("Tom Scott", 1),)

    def test_a_channel_named_by_an_empty_string_is_not_a_channel(self):
        """The parser turns a blank name into null before it ever reaches here,
        so this pins the contract rather than a live case: counting requires a
        name to show, and "" would put a row on the page labelled with nothing."""
        stats = describe_youtube([video(channel_name=""), video(channel_name="Tom Scott")])

        assert stats.views == 2
        assert stats.channels == 1
        assert stats.top_channels == (Count("Tom Scott", 1),)

    def test_finds_the_ends_of_the_history(self):
        stats = describe_youtube([video(when=at(2024, 3, 2)), video(when=at(2026, 1, 9))])

        assert stats.first_watched == at(2024, 3, 2)
        assert stats.last_watched == at(2026, 1, 9)

    def test_buckets_views_by_month_with_the_gaps_in(self):
        stats = describe_youtube([video(when=at(2026, 1, 5)), video(when=at(2026, 3, 5))])

        assert [entry.count for entry in stats.by_month] == [1, 0, 1]

    def test_keeps_only_the_top_few_channels(self):
        views = [video(channel_name=f"Channel {i:02d}", video_id=f"v{i}") for i in range(20)]
        stats = describe_youtube(views, top=4)

        assert len(stats.top_channels) == 4

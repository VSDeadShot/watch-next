"""What a watch history looks like, described rather than predicted.

The obvious thing to say about this module is that it counts, and the useful
thing to say is how it differs from :mod:`app.core.taste`, which reads the same
rows. Taste is a *prediction*: it decays with time, normalises against itself
and deliberately forgets, because what somebody watched four years ago is a poor
guide to tonight. This is a *description*: nothing decays, nothing is
normalised, and four years ago counts exactly as much as last week, because a
history that quietly forgot its own beginning would not be a history.

Two counting decisions do most of the work here, and both exist because the raw
numbers lie in specific ways.

**A binge is one title and sixty sessions.** Both numbers are true and they
answer different questions, so both are reported and neither is ever quietly
substituted for the other. Everything grouped -- genres, decades -- is counted
per distinct title rather than per session, for the reason taste rolls up before
weighing: one sitcom watched to the end would otherwise make comedy nine tenths
of somebody's viewing.

**Time watched is measured, not assumed.** Netflix records how long each session
actually ran. Multiplying a runtime by a session count instead would assume
everything was watched to the end, which is exactly the thing the export can
tell us and the arithmetic can only guess at. Sessions with no duration are
counted separately rather than treated as zero, so the figure can say what it is
based on.

Months are bucketed in UTC, which is what the timestamps are stored in. At a
month boundary that puts a late-night session in the neighbouring bucket, and
that is accepted: the shape of a year survives it. Anything finer -- hour of the
day, day of the week -- would not, and is deliberately absent rather than shown
in a timezone nobody watched anything in.

This module is pure: no I/O, no network, no clock of its own.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from app.core.taste import SHOW

# How many entries a ranked list carries. Enough to see a shape, few enough that
# the answer is still a summary -- a page of forty genres is the raw data again.
TOP_N = 8

# A session has to have run longer than this to contribute time. The importer
# already drops accidental starts below its own floor; this only stops a
# nonsensical or negative duration reaching the arithmetic.
MINIMUM_DURATION_SECONDS = 0

# Roughly when the first films were made. A release year below this is not a
# date, it is a bad row -- a zero or a single digit -- and it is left out of the
# decades exactly as a missing year is, because a histogram with a bar labelled
# "0" in it reads as broken rather than as informative.
#
# There is deliberately no upper bound. A title announced for a year that has
# not happened yet is a real thing the catalogue carries, and guessing at a
# ceiling would quietly drop it.
EARLIEST_RELEASE_YEAR = 1880


@dataclass(frozen=True)
class SessionRecord:
    """One viewing session, reduced to what can be counted.

    Deliberately not a database row, for the reason
    :class:`~app.core.taste.WatchRecord` is not one: this is built from a join,
    and taking a plain record is what lets the counting be tested without a
    database anywhere near it.
    """

    title_id: int
    watched_at: datetime
    title: str = ""
    object_type: str = ""
    genres: tuple[str, ...] = ()
    release_year: int | None = None
    # How long this session actually ran. Null for any source that does not
    # record one, which is not the same as zero.
    duration_seconds: int | None = None


@dataclass(frozen=True)
class VideoRecord:
    """One YouTube video watched. No duration -- Takeout does not export one."""

    watched_at: datetime
    channel_name: str | None = None
    video_id: str = ""


@dataclass(frozen=True)
class Count:
    """One labelled number in a ranked list."""

    label: str
    count: int


@dataclass(frozen=True)
class MonthCount:
    """Sessions in one month, dated by its first day."""

    month: date
    count: int


@dataclass(frozen=True)
class TitleCount:
    """A title and how many sessions went into it.

    ``object_type`` travels with it because the number means different things
    either side of it -- twelve sessions of a series is twelve episodes, twelve
    of a film is having watched it twelve times -- and a list that did not say
    which would invite the reader to compare two things that do not compare.
    """

    title_id: int
    title: str
    object_type: str
    sessions: int


@dataclass(frozen=True)
class Statistics:
    """A watch history, counted.

    Every field is empty or null for an empty history rather than absent. Every
    caller runs before the first import, and "nothing yet" is an answer worth
    drawing rather than an error worth raising.
    """

    titles: int = 0
    sessions: int = 0
    # Distinct titles, not sessions. See the module docstring.
    movies: int = 0
    series: int = 0

    # Null when nothing in the history carried a duration, which is not the same
    # as nothing having been watched. ``sessions_timed`` says how much of the
    # history the figure actually rests on, so a partly-timed history can be
    # shown as the lower bound it is.
    minutes_watched: int | None = None
    sessions_timed: int = 0

    first_watched: datetime | None = None
    last_watched: datetime | None = None

    top_genres: tuple[Count, ...] = ()
    decades: tuple[Count, ...] = ()
    top_titles: tuple[TitleCount, ...] = ()
    by_month: tuple[MonthCount, ...] = ()


@dataclass(frozen=True)
class YouTubeStatistics:
    """A YouTube history, counted, and kept apart from the rest.

    Separate from :class:`Statistics` for the reason ``youtube_views`` is a
    separate table: YouTube is a taste and statistics signal and never a
    recommendation candidate, and one shape that could hold both would be the
    first step towards forgetting that.
    """

    views: int = 0
    videos: int = 0
    channels: int = 0
    first_watched: datetime | None = None
    last_watched: datetime | None = None
    top_channels: tuple[Count, ...] = ()
    by_month: tuple[MonthCount, ...] = ()


@dataclass
class _TitleTally:
    """Everything one distinct title contributed, before anything is ranked."""

    title: str = ""
    object_type: str = ""
    genres: tuple[str, ...] = ()
    release_year: int | None = None
    sessions: int = 0


def describe_history(records: Iterable[SessionRecord], *, top: int = TOP_N) -> Statistics:
    """Count a watch history.

    Args:
        top: how many entries the ranked lists carry.
    """
    sessions = list(records)
    if not sessions:
        return Statistics()

    per_title = _roll_up(sessions)
    moments = [record.watched_at for record in sessions]
    timed = [
        record.duration_seconds
        for record in sessions
        if record.duration_seconds is not None
        and record.duration_seconds > MINIMUM_DURATION_SECONDS
    ]

    genres: defaultdict[str, int] = defaultdict(int)
    decades: defaultdict[str, int] = defaultdict(int)
    for tally in per_title.values():
        for genre in tally.genres:
            genres[genre] += 1
        if tally.release_year is not None and tally.release_year >= EARLIEST_RELEASE_YEAR:
            decades[str(tally.release_year // 10 * 10)] += 1

    series = sum(1 for tally in per_title.values() if tally.object_type == SHOW)

    return Statistics(
        titles=len(per_title),
        sessions=len(sessions),
        # Derived rather than counted separately, so the two always account for
        # every title. A catalogue that grew a third object type would otherwise
        # go quietly missing from a page that looks complete.
        movies=len(per_title) - series,
        series=series,
        # Whole minutes only. A part-minute is below the resolution of anything
        # this figure is used to say.
        minutes_watched=sum(timed) // 60 if timed else None,
        sessions_timed=len(timed),
        first_watched=min(moments),
        last_watched=max(moments),
        top_genres=_ranked(genres, top=top),
        # Chronological rather than ranked: a decade histogram is a shape over
        # time, and sorting it by size would destroy the only axis it has.
        decades=tuple(Count(label, decades[label]) for label in sorted(decades)),
        top_titles=_top_titles(per_title, top=top),
        by_month=_by_month(moments),
    )


def describe_youtube(videos: Iterable[VideoRecord], *, top: int = TOP_N) -> YouTubeStatistics:
    """Count a YouTube history.

    Views and distinct videos are both reported, and the gap between them is
    most of what this data is good for: how often somebody goes back to the same
    thing is a stronger signal than how much they watched once.
    """
    views = list(videos)
    if not views:
        return YouTubeStatistics()

    moments = [view.watched_at for view in views]
    channels: defaultdict[str, int] = defaultdict(int)
    for view in views:
        # Takeout omits the channel for a video that has since been removed.
        # An unnamed channel is not a channel, and inventing one to hold these
        # would put a row on the page that names nothing.
        if view.channel_name:
            channels[view.channel_name] += 1

    return YouTubeStatistics(
        views=len(views),
        videos=len({view.video_id for view in views}),
        channels=len(channels),
        first_watched=min(moments),
        last_watched=max(moments),
        top_channels=_ranked(channels, top=top),
        by_month=_by_month(moments),
    )


def months_between(first: datetime, last: datetime) -> list[date]:
    """Every month from one moment to another inclusive, including empty ones.

    Walked a month at a time rather than derived arithmetically because the
    obvious arithmetic is where the off-by-one lives, and this runs once per
    request over a span measured in years.
    """
    months: list[date] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append(date(year, month, 1))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _roll_up(sessions: Sequence[SessionRecord]) -> dict[int, _TitleTally]:
    """Collapse sessions into one entry per distinct title.

    The catalogue metadata is identical on every session for a title, because it
    comes from one catalogue row, so the first one seen is as good as any other.
    """
    per_title: dict[int, _TitleTally] = {}
    for record in sessions:
        tally = per_title.get(record.title_id)
        if tally is None:
            tally = per_title[record.title_id] = _TitleTally(
                title=record.title,
                object_type=record.object_type,
                # Deduplicated, because the counting below relies on one
                # title being one vote and a catalogue row has been known to
                # repeat itself. A title listed as a comedy twice is still one
                # comedy, and without this it would outvote a title that is
                # only listed once.
                genres=tuple(dict.fromkeys(record.genres)),
                release_year=record.release_year,
            )
        tally.sessions += 1
    return per_title


def _ranked(totals: dict[str, int], *, top: int) -> tuple[Count, ...]:
    """The biggest counts, largest first.

    Ties break on the label, for the reason :meth:`~app.core.taste.TasteProfile.
    top_genres` does: a list whose order changes between two identical requests
    reads as a bug, whatever the numbers underneath are doing.
    """
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return tuple(Count(label, count) for label, count in ranked[:top])


def _top_titles(per_title: dict[int, _TitleTally], *, top: int) -> tuple[TitleCount, ...]:
    """The most-watched titles, most sessions first, ties broken by name."""
    ranked = sorted(per_title.items(), key=lambda item: (-item[1].sessions, item[1].title))
    return tuple(
        TitleCount(
            title_id=title_id,
            title=tally.title,
            object_type=tally.object_type,
            sessions=tally.sessions,
        )
        for title_id, tally in ranked[:top]
    )


def _by_month(moments: Sequence[datetime]) -> tuple[MonthCount, ...]:
    """Sessions per month across the whole span, with the gaps still in it.

    Every month between the first and the last appears, including the ones with
    nothing in them. A gap in somebody's viewing is information, and a series
    that simply omitted the empty months would draw a lie about the shape of it.
    """
    if not moments:
        return ()
    counted = Counter(date(moment.year, moment.month, 1) for moment in moments)
    return tuple(
        MonthCount(month, counted.get(month, 0))
        for month in months_between(min(moments), max(moments))
    )

"""Reading a history out of the database so it can be counted.

:mod:`app.core.stats` owns every decision worth arguing about -- what a binge is
worth, what time watched is based on, whether an empty month exists. This module
only fetches, and it fetches the two histories separately because they are
separate things: Netflix viewing joins the catalogue and YouTube deliberately
cannot.

It adds one number the pure counting has no way to know. Everything counted here
comes from watch events that reached a catalogue row, because the join is what
supplies the genres and the runtime -- so a library with unresolved rows in it
would be described as smaller than it is. The count of those rows travels with
the answer rather than being left out, since a summary that quietly understates
itself is exactly the kind of thing this app is meant not to do.

This module is impure: it owns the session.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.stats import (
    TOP_N,
    SessionRecord,
    Statistics,
    VideoRecord,
    YouTubeStatistics,
    describe_history,
    describe_youtube,
)
from app.models import DEFAULT_USER_ID, Title, WatchEvent, YouTubeView


@dataclass(frozen=True)
class Overview:
    """Everything the stats page is drawn from, in one answer."""

    history: Statistics = field(default_factory=Statistics)
    youtube: YouTubeStatistics = field(default_factory=YouTubeStatistics)
    # Watch events that never reached a catalogue row, and so are in none of the
    # numbers above. Actionable rather than decorative: the fix is a pass of the
    # resolver, or a few manual choices on the unresolved list.
    unresolved_sessions: int = 0


def overview(session: Session, *, user_id: str = DEFAULT_USER_ID, top: int = TOP_N) -> Overview:
    """Count both histories and report what could not be counted.

    Three queries, whatever the history is: one for the viewing, one for the
    videos, one for the count of what never resolved. Counting is done in Python
    rather than in SQL on purpose -- the decisions that make these numbers
    honest live in :mod:`app.core.stats`, where they can be read and argued with,
    and a page of ``GROUP BY`` would hide every one of them in a query plan.
    """
    return Overview(
        history=describe_history(_sessions(session, user_id=user_id), top=top),
        youtube=describe_youtube(_videos(session, user_id=user_id), top=top),
        unresolved_sessions=_unresolved(session, user_id=user_id),
    )


def _sessions(session: Session, *, user_id: str) -> Iterator[SessionRecord]:
    """Every watch event that reached a catalogue row, as a countable record.

    Read as columns rather than as mapped objects, as the taste profile is and
    for the same reason: this touches the whole library, and hydrating thousands
    of instances to read seven fields is waste. The whole history still lands in
    memory, which is the same limitation the taste profile carries and worth
    fixing in both places at once rather than half of it here.

    An inner join, so an event the matcher never decided contributes nothing --
    there is no catalogue row to take a genre or a runtime from. Those rows are
    counted separately by :func:`_unresolved` rather than being forgotten.
    """
    rows = session.execute(
        select(
            WatchEvent.title_id,
            WatchEvent.watched_at,
            WatchEvent.duration_seconds,
            Title.title,
            Title.object_type,
            Title.genres,
            Title.release_year,
        )
        .join(Title, WatchEvent.title_id == Title.id)
        .where(WatchEvent.user_id == user_id)
    )
    for title_id, watched_at, duration, title, object_type, genres, year in rows:
        yield SessionRecord(
            title_id=title_id,
            watched_at=watched_at,
            title=title,
            object_type=object_type,
            genres=tuple(genres or ()),
            release_year=year,
            duration_seconds=duration,
        )


def _videos(session: Session, *, user_id: str) -> Iterator[VideoRecord]:
    """Every YouTube view. No join -- there is nothing to join it to."""
    rows = session.execute(
        select(
            YouTubeView.watched_at,
            YouTubeView.channel_name,
            YouTubeView.video_id,
        ).where(YouTubeView.user_id == user_id)
    )
    for watched_at, channel_name, video_id in rows:
        yield VideoRecord(watched_at=watched_at, channel_name=channel_name, video_id=video_id)


def _unresolved(session: Session, *, user_id: str) -> int:
    """How many watch events the matcher never decided.

    Counted in the database rather than fetched and measured: this is the one
    number here that needs no Python judgement, and the rows behind it have
    nothing else worth reading.
    """
    return (
        session.scalar(
            select(func.count())
            .select_from(WatchEvent)
            .where(WatchEvent.user_id == user_id, WatchEvent.title_id.is_(None))
        )
        or 0
    )

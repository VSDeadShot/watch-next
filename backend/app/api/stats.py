"""What the history adds up to.

One route, because the page is one screen and everything on it is read from the
same two tables -- splitting it into a request per panel would mean the page
could show a Netflix history and a YouTube history counted at different moments,
and the only thing that would achieve is a screen that cannot be trusted to
agree with itself.

A GET, and genuinely one: nothing here writes, nothing refreshes, nothing costs
a request against JustWatch. This is the whole app read back.
"""

from fastapi import APIRouter

from app.api.deps import SessionDep, UserDep
from app.core.genres import genre_name
from app.core.stats import Count, MonthCount, Statistics, YouTubeStatistics
from app.schemas import (
    CountResponse,
    HistoryStatsResponse,
    MonthCountResponse,
    StatsResponse,
    TopTitleResponse,
    YouTubeStatsResponse,
)
from app.services.stats import overview

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("", response_model=StatsResponse)
def mine(session: SessionDep, user: UserDep) -> StatsResponse:
    """Count both histories, and say what could not be counted."""
    found = overview(session, user_id=user)
    return StatsResponse(
        history=_history(found.history),
        youtube=_youtube(found.youtube),
        unresolved_sessions=found.unresolved_sessions,
    )


def _history(stats: Statistics) -> HistoryStatsResponse:
    return HistoryStatsResponse(
        titles=stats.titles,
        sessions=stats.sessions,
        movies=stats.movies,
        series=stats.series,
        minutes_watched=stats.minutes_watched,
        sessions_timed=stats.sessions_timed,
        first_watched=stats.first_watched,
        last_watched=stats.last_watched,
        # In English. The codes are JustWatch's private vocabulary; a client
        # given "crm" can only print it or keep a second copy of our table, and
        # the second copy is the one that silently goes stale.
        top_genres=[
            CountResponse(label=genre_name(entry.label), count=entry.count)
            for entry in stats.top_genres
        ],
        decades=_counts(stats.decades),
        top_titles=[
            TopTitleResponse(
                title_id=entry.title_id,
                title=entry.title,
                object_type=entry.object_type,
                sessions=entry.sessions,
            )
            for entry in stats.top_titles
        ],
        by_month=_months(stats.by_month),
    )


def _youtube(stats: YouTubeStatistics) -> YouTubeStatsResponse:
    return YouTubeStatsResponse(
        views=stats.views,
        videos=stats.videos,
        channels=stats.channels,
        first_watched=stats.first_watched,
        last_watched=stats.last_watched,
        # Channel names are already words a person wrote; nothing to translate.
        top_channels=_counts(stats.top_channels),
        by_month=_months(stats.by_month),
    )


def _counts(counts: tuple[Count, ...]) -> list[CountResponse]:
    return [CountResponse(label=entry.label, count=entry.count) for entry in counts]


def _months(months: tuple[MonthCount, ...]) -> list[MonthCountResponse]:
    return [MonthCountResponse(month=entry.month, count=entry.count) for entry in months]

"""One thing to watch tonight, and nothing else.

This is the product. Everything else in the backend exists to make this function
able to answer honestly, and its shape encodes the two promises the app makes.

**Exactly one.** The result carries a single pick, not a list with a length of
one, because a type that cannot express a list is the only kind of constraint
that survives a busy afternoon. Nothing downstream has to be trusted to slice.

**Only things that can actually be watched.** Availability is applied as a hard
filter before anything is scored, and there is no weight anywhere that could
outvote it. Recommending something that turns out to be on a service somebody
does not pay for is the single failure that makes the whole app worthless.

When there is no answer, that is information rather than an error: "nothing on
your services fits forty minutes" tells somebody what to change, and a bare
404 does not. So the result carries a reason and the counts behind it.

This module is impure: it owns the session and the catalogue client.
"""

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.availability import Offer as OfferRecord
from app.core.availability import is_available, watch_options
from app.core.scoring import CandidateTitle, RecommendationRequest, ScoredTitle, rank_titles
from app.core.taste import TasteProfile, WatchRecord, build_taste_profile
from app.models import DEFAULT_USER_ID, Provider, Recommendation, Title, WatchEvent
from app.services.discovery import refresh_pool
from app.services.justwatch_client import CataloguePopular
from app.services.offers import cached_offers_for

_log = logging.getLogger(__name__)

# How long before something already suggested can be suggested again. Being
# told to watch the same film four evenings running is how somebody stops
# opening the app. Long enough to matter, short enough that a modest pool does
# not run dry.
DEFAULT_REPEAT_COOLDOWN = timedelta(days=14)

# How long an answer stays the answer. Asking twice in a row -- a page refresh,
# a second look -- is one question, not two, and the cooldown must not treat the
# reply it just gave as a thing already seen. Without this, refreshing the page
# after being told what to watch replies that there is nothing to suggest.
SAME_SITTING = timedelta(minutes=30)


@dataclass(frozen=True)
class WatchOn:
    """Somewhere a recommendation can be watched, in words a person reads."""

    provider: str
    name: str
    monetization: str
    url: str | None = None
    # False for anything free to everyone, so the interface can say "free on
    # JioHotstar" rather than implying a subscription somebody does not have.
    requires_subscription: bool = True


@dataclass(frozen=True)
class Pick:
    """The one title, and everything needed to justify and act on it."""

    title: Title
    score: float = 0.0
    reasons: tuple[str, ...] = ()
    watch_on: tuple[WatchOn, ...] = ()


@dataclass(frozen=True)
class Considered:
    """How many candidates survived each stage, so a refusal can be explained.

    Reading these in order says where the search collapsed, which is the
    difference between "import something" and "tick a box on the settings page"
    and "ask again with more time".
    """

    pool: int = 0
    available: int = 0
    eligible: int = 0


@dataclass(frozen=True)
class RecommendationResult:
    """One pick, or none and the reason why. Never a list."""

    pick: Pick | None = None
    considered: Considered = Considered()
    reason: str = ""


def recommend(
    session: Session,
    catalogue: CataloguePopular,
    request: RecommendationRequest,
    *,
    now: datetime | None = None,
    exclude_ids: Collection[int] = (),
    cooldown: timedelta = DEFAULT_REPEAT_COOLDOWN,
    user_id: str = DEFAULT_USER_ID,
) -> RecommendationResult:
    """Choose one thing to watch, or explain why there is nothing.

    The country is read off the catalogue client, as everywhere else that deals
    in availability: the offers were fetched for whichever country the request
    was actually made for, and a second source for that answer is a second
    chance to disagree with it.
    """
    when = now or datetime.now(UTC)
    country = catalogue.country
    subscribed = _subscriptions(session, country=country, user_id=user_id)

    # Best effort, and deliberately not fatal. An outage should cost freshness,
    # not the whole feature -- there is very likely a usable pool already.
    refresh_pool(session, catalogue, subscriptions=subscribed, now=when, user_id=user_id)

    candidates = _pool(
        session, when=when, exclude_ids=exclude_ids, cooldown=cooldown, user_id=user_id
    )
    offers = cached_offers_for(session, [title.id for title in candidates], country=country)

    watchable = [
        title for title in candidates if is_available(offers.get(title.id, ()), subscribed)
    ]
    profile = _taste(session, when=when, user_id=user_id)
    ranked = rank_titles([_as_candidate(title) for title in watchable], profile, request)
    counts = Considered(pool=len(candidates), available=len(watchable), eligible=len(ranked))

    if not ranked:
        return RecommendationResult(
            considered=counts,
            reason=_why_nothing(counts, request, subscribed),
        )

    best = ranked[0]
    title = next(item for item in watchable if item.id == best.candidate.title_id)
    pick = Pick(
        title=title,
        score=best.score,
        reasons=best.reasons,
        watch_on=_watch_on(session, offers.get(title.id, ()), subscribed, country=country),
    )
    _record(session, best, request, when=when, user_id=user_id)
    session.commit()
    return RecommendationResult(pick=pick, considered=counts)


def _subscriptions(session: Session, *, country: str, user_id: str) -> set[str]:
    from app.services.providers import subscriptions

    return set(subscriptions(session, country=country, user_id=user_id))


def _pool(
    session: Session,
    *,
    when: datetime,
    exclude_ids: Collection[int],
    cooldown: timedelta,
    user_id: str,
) -> list[Title]:
    """Everything that could be recommended, before availability has its say.

    Three exclusions, and each is a different kind of "no". Already watched is
    permanent. Recently recommended is temporary, and exists so that the app
    does not say the same thing four evenings running. Ruled out by the caller
    is for this request only -- it is what "not this one" is made of.

    The cooldown has a near edge as well as a far one. Something suggested in
    the last half hour is *not* excluded, because asking twice in a row is one
    question and the app has to be able to repeat its own answer -- excluding it
    would mean a page refresh replies that there is nothing to suggest.

    A watch event whose title was never resolved cannot exclude anything, since
    there is no catalogue row to exclude. That is a known and accepted hole: the
    fix is resolving the row, not guessing here.
    """
    watched = select(WatchEvent.title_id).where(
        WatchEvent.user_id == user_id, WatchEvent.title_id.is_not(None)
    )
    recently_suggested = select(Recommendation.title_id).where(
        Recommendation.user_id == user_id,
        Recommendation.created_at > when - cooldown,
        Recommendation.created_at <= when - SAME_SITTING,
    )

    query = select(Title).where(Title.id.not_in(watched), Title.id.not_in(recently_suggested))
    if exclude_ids:
        query = query.where(Title.id.not_in(list(exclude_ids)))
    return list(session.scalars(query.order_by(Title.id)))


def _taste(session: Session, *, when: datetime, user_id: str) -> TasteProfile:
    """Build the profile from every watch event that reached a catalogue row.

    Read as columns rather than as mapped objects: this touches the whole
    library, and hydrating thousands of instances to read six fields is waste.
    """
    rows = session.execute(
        select(
            WatchEvent.title_id,
            WatchEvent.watched_at,
            Title.object_type,
            Title.genres,
            Title.runtime_minutes,
            Title.release_year,
        )
        .join(Title, WatchEvent.title_id == Title.id)
        .where(WatchEvent.user_id == user_id)
    )
    return build_taste_profile(
        (
            WatchRecord(
                title_id=title_id,
                watched_at=watched_at,
                object_type=object_type,
                genres=tuple(genres or ()),
                runtime_minutes=runtime_minutes,
                release_year=release_year,
            )
            for title_id, watched_at, object_type, genres, runtime_minutes, release_year in rows
        ),
        now=when,
    )


def _as_candidate(title: Title) -> CandidateTitle:
    return CandidateTitle(
        title_id=title.id,
        title=title.title,
        object_type=title.object_type,
        genres=tuple(title.genres or ()),
        runtime_minutes=title.runtime_minutes,
        release_year=title.release_year,
        imdb_score=title.imdb_score,
        tmdb_score=title.tmdb_score,
        # Always false for now: there is no watchlist table yet. This is the one
        # line that changes when there is, and the scoring behind it is already
        # written and tested.
        on_watchlist=False,
    )


def _watch_on(
    session: Session,
    offers: Sequence[OfferRecord],
    subscribed: Collection[str],
    *,
    country: str,
) -> tuple[WatchOn, ...]:
    """Turn the watchable offers into something worth putting on a screen."""
    options = watch_options(offers, subscribed)
    names = _provider_names(session, [option.provider for option in options], country=country)
    return tuple(
        WatchOn(
            provider=option.provider,
            name=names.get(option.provider, option.provider),
            monetization=str(option.monetization),
            url=option.url,
            requires_subscription=option.requires_subscription,
        )
        for option in options
    )


def _provider_names(
    session: Session, short_names: Collection[str], *, country: str
) -> dict[str, str]:
    """Display names for the services an offer named.

    Anything missing falls back to its short name at the call site. The provider
    catalogue can be out of date -- it is refreshed on its own schedule -- and
    "watch it on jhs" is a poor label but a true one, which beats refusing to
    say where.
    """
    if not short_names:
        return {}
    rows = session.execute(
        select(Provider.short_name, Provider.name).where(
            Provider.country == country, Provider.short_name.in_(list(short_names))
        )
    )
    return {short_name: name for short_name, name in rows}


def _record(
    session: Session,
    best: ScoredTitle,
    request: RecommendationRequest,
    *,
    when: datetime,
    user_id: str,
) -> None:
    """Keep the receipt: what was suggested, what was asked, and why.

    The same instinct as the rejected candidates on a title resolution. This
    app's claim is that its one answer is defensible, and an answer nobody can
    reconstruct afterwards is not.

    Repeating an answer within the same sitting is not a second recommendation
    and is not written down twice. A refresh is somebody looking again, not the
    app deciding again.
    """
    already = session.scalars(
        select(Recommendation.id).where(
            Recommendation.user_id == user_id,
            Recommendation.title_id == best.candidate.title_id,
            Recommendation.created_at > when - SAME_SITTING,
        )
    ).first()
    if already is not None:
        return

    session.add(
        Recommendation(
            user_id=user_id,
            title_id=best.candidate.title_id,
            created_at=when,
            mood=str(request.mood),
            minutes_available=request.minutes_available,
            kind=str(request.kind),
            score=best.score,
            reasons=list(best.reasons),
        )
    )


def _why_nothing(
    counts: Considered, request: RecommendationRequest, subscribed: Collection[str]
) -> str:
    """Explain a refusal in terms of the thing somebody could change.

    Ordered from the earliest failure, because the earliest one is the real one:
    telling somebody their time budget is too tight when the actual problem is
    an empty library would send them to fix the wrong thing.
    """
    if not counts.pool:
        return (
            "There is nothing left to suggest -- either nothing has been imported or "
            "discovered yet, or everything we know about has been watched or suggested "
            "recently."
        )

    if not counts.available:
        if not subscribed:
            return (
                "Nothing we know about is free to watch, and no subscriptions have been "
                "set up yet -- pick the services you have on the settings page."
            )
        return (
            "Nothing we know about is streaming on the services you have. Adding a "
            "service on the settings page is the quickest fix."
        )

    if request.minutes_available is not None:
        return (
            f"Nothing you can watch fits {request.minutes_available} minutes. "
            "Try allowing a little longer."
        )
    return "Nothing you can watch matches what you asked for. Try a different mood."

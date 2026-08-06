"""Where a title can be watched, in words worth putting on a screen.

:mod:`app.core.availability` owns the rule -- watchable means at no additional
cost, either covered by a subscription the user has or free to everyone. That
rule is pure and knows only about short names: ``nfx``, ``prv``, ``jhs``. This
module is what stands between it and a person, and it supplies the three things
the rule cannot know on its own: which offers were cached for this country,
which services the user actually pays for, and what those services are called.

It exists as a module rather than a helper on the recommender because the answer
is wanted in two places now. A recommendation says where to press play, and so
does a watchlist -- a list of titles with no idea whether any of them can be
watched is a list somebody has to check by hand, which is the work this app was
built to take away.

This module is impure: it owns the session.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.availability import Offer as OfferRecord
from app.core.availability import WatchOption, watch_options
from app.models import DEFAULT_USER_ID, Provider
from app.services.offers import cached_offers_for
from app.services.providers import subscriptions


@dataclass(frozen=True)
class WatchOn:
    """Somewhere a title can be watched, in words a person reads."""

    provider: str
    name: str
    monetization: str
    url: str | None = None
    # False for anything free to everyone, so the interface can say "free on
    # JioHotstar" rather than implying a subscription somebody does not have.
    requires_subscription: bool = True


def watch_on(
    session: Session,
    offers: Sequence[OfferRecord],
    subscribed: Collection[str],
    *,
    country: str,
) -> tuple[WatchOn, ...]:
    """Turn one title's watchable offers into something worth putting on a screen.

    For callers that already hold the offers -- the recommender fetches them for
    its whole pool to decide availability, and would be asking twice otherwise.
    """
    options = watch_options(offers, subscribed)
    names = _provider_names(session, [option.provider for option in options], country=country)
    return _dressed(options, names)


def watch_on_for(
    session: Session,
    title_ids: Collection[int],
    *,
    country: str,
    user_id: str = DEFAULT_USER_ID,
) -> dict[int, tuple[WatchOn, ...]]:
    """The same answer for a list of titles, in a fixed number of queries.

    Every title asked about appears in the result, streaming nowhere included.
    Unlike the offer cache, which leaves out what it has nothing for, "nowhere"
    is a real answer here and one the caller draws on the screen -- so the map
    is total, and no caller has to decide whether a missing key means
    unwatchable or unasked.
    """
    ids = list(dict.fromkeys(title_ids))
    if not ids:
        return {}

    subscribed = set(subscriptions(session, country=country, user_id=user_id))
    offers = cached_offers_for(session, ids, country=country)

    options = {title_id: watch_options(offers.get(title_id, ()), subscribed) for title_id in ids}
    # One lookup for every name on the page rather than one per row: a watchlist
    # is a list, and a query per row is a page that gets slower the more
    # somebody uses it.
    names = _provider_names(
        session,
        {option.provider for found in options.values() for option in found},
        country=country,
    )
    return {title_id: _dressed(found, names) for title_id, found in options.items()}


def _dressed(options: Sequence[WatchOption], names: dict[str, str]) -> tuple[WatchOn, ...]:
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

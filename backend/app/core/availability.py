"""Whether you can actually watch this tonight, and where.

This is the rule the product rests on. Recommending something the user cannot
watch -- because it left Netflix last month, or because it is on a service they
do not pay for -- is the single failure that makes the whole app worthless, so
the rule lives in one pure function rather than being spread across whichever
query happens to need it.

The definition it encodes: watchable means *at no additional cost*. Either the
user's subscription already covers it, or it is free to everyone. Renting and
buying are deliberately excluded -- "you could pay for this" is not "you can
watch this", and blurring the two is exactly the disappointment this exists to
prevent.

Pure: no database, no network, no clock of its own.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Monetization(StrEnum):
    """How JustWatch says a title is paid for.

    A StrEnum because these are stored and compared against strings that came
    back from the API, which may one day include a value not listed here.
    """

    FLATRATE = "FLATRATE"
    FREE = "FREE"
    ADS = "ADS"
    RENT = "RENT"
    BUY = "BUY"


# Free to anyone, subscription or not. ADS is free with advertising, which costs
# patience rather than money and still means the user can press play tonight.
FREE_TO_EVERYONE = frozenset({Monetization.FREE, Monetization.ADS})

# Included in a subscription -- watchable only if it is one the user actually
# has, which is the whole point of asking them which ones those are.
INCLUDED_IN_SUBSCRIPTION = frozenset({Monetization.FLATRATE})

# How the options are ordered when a title can be watched several ways. What
# someone already pays for comes first: it is the app they will actually open,
# and it has no advertising in it. Free beats free-with-ads for the same reason.
_PREFERENCE = (
    Monetization.FLATRATE,
    Monetization.FREE,
    Monetization.ADS,
)


@dataclass(frozen=True)
class Offer:
    """One way JustWatch says a title is available in one country.

    ``provider`` is the short name (``nfx``, ``prv``), which is the only thing
    that joins an offer to a subscription.
    """

    provider: str
    monetization: str
    # HD, _4K, SD. Kept because it is worth showing, never used to decide
    # whether something is watchable.
    presentation: str | None = None
    url: str | None = None
    price_string: str | None = None


@dataclass(frozen=True)
class WatchOption:
    """A way the user can watch a title right now, at no additional cost."""

    provider: str
    monetization: Monetization
    url: str | None
    # False for anything free to everyone, so the UI can say "free on JioHotstar"
    # rather than implying a subscription the user does not have.
    requires_subscription: bool


def watch_options(
    offers: Collection[Offer], subscriptions: Collection[str]
) -> tuple[WatchOption, ...]:
    """Every way the user can watch this at no additional cost, best first.

    One entry per provider: JustWatch lists HD and 4K as separate offers, and
    telling someone they can watch it on Netflix twice is noise. The first
    offer seen for a provider wins, after sorting, so the answer does not depend
    on the order the API happened to return them in.
    """
    subscribed = set(subscriptions)

    usable = [
        (offer, monetization)
        for offer, monetization in ((offer, _monetization(offer)) for offer in offers)
        if monetization is not None and _is_watchable(monetization, offer.provider, subscribed)
    ]
    # Sorted by preference, then by provider so that two offers we have no
    # reason to separate still come back in the same order every time.
    usable.sort(key=lambda pair: (_PREFERENCE.index(pair[1]), pair[0].provider))

    options: dict[str, WatchOption] = {}
    for offer, monetization in usable:
        options.setdefault(
            offer.provider,
            WatchOption(
                provider=offer.provider,
                monetization=monetization,
                url=offer.url,
                requires_subscription=monetization not in FREE_TO_EVERYONE,
            ),
        )
    return tuple(options.values())


def is_available(offers: Collection[Offer], subscriptions: Collection[str]) -> bool:
    """Whether the user can watch this at all. The recommender's hard filter."""
    subscribed = set(subscriptions)
    return any(
        monetization is not None and _is_watchable(monetization, offer.provider, subscribed)
        for offer, monetization in ((offer, _monetization(offer)) for offer in offers)
    )


def is_stale(fetched_at: datetime | None, *, now: datetime, ttl: timedelta) -> bool:
    """Whether cached availability is old enough to be worth fetching again.

    Never fetched counts as stale rather than as unavailable. A title with no
    cached answer is unknown, and treating unknown as "cannot be watched" would
    quietly remove it from consideration for ever.

    A timestamp in the future is treated as fresh. Clock skew between a server
    and its database is not a reason to refetch everything.
    """
    if fetched_at is None:
        return True
    return now - fetched_at > ttl


def _monetization(offer: Offer) -> Monetization | None:
    """The offer's payment type, or None if JustWatch invented a new one.

    Unknown is not treated as free. JustWatch is free to add a value, and
    guessing that an unrecognised way to pay costs nothing would be the wrong
    way to be wrong.
    """
    try:
        return Monetization(offer.monetization)
    except ValueError:
        return None


def _is_watchable(monetization: Monetization, provider: str, subscribed: set[str]) -> bool:
    if monetization in FREE_TO_EVERYONE:
        return True
    return monetization in INCLUDED_IN_SUBSCRIPTION and provider in subscribed

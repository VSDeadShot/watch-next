"""Caching where a title can be watched, and knowing when to ask again.

Availability is the one thing this app cannot afford to be wrong about, and it
is the one thing that goes out of date without telling us: a title leaves
Netflix and nothing announces it. So there are two jobs here. Store what
JustWatch said, and remember how long ago it said it.

Most of this cache fills itself for free. JustWatch returns offers with the
search that resolution already makes, so a library that has been resolved has
its availability alongside it without a single extra request. This module exists
for the other half: replacing those answers once they are old.

This module is impure: it owns the session and the catalogue client.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from simplejustwatchapi.exceptions import JustWatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.availability import Offer as OfferRecord
from app.core.availability import is_stale
from app.models import Offer, Title
from app.services.justwatch_client import CatalogueLookup, OfferEntry

_log = logging.getLogger(__name__)

# How long a cached answer is trusted. Availability changes on the order of
# weeks, not minutes, and every refresh is a request against an unofficial API,
# so checking more eagerly would cost a great deal to learn almost nothing.
DEFAULT_TTL = timedelta(days=7)

# How many titles one refresh pass is willing to spend requests on. At one
# request a second a few hundred is a few minutes, which is a reasonable
# background chore; the whole catalogue at once is not.
DEFAULT_REFRESH_LIMIT = 200


@dataclass(frozen=True)
class RefreshSummary:
    """What one availability refresh did."""

    refreshed: int = 0
    failed: int = 0
    offers_stored: int = 0


def store_offers(
    session: Session,
    title: Title,
    offers: list[OfferEntry] | tuple[OfferEntry, ...],
    *,
    country: str,
    now: datetime | None = None,
) -> int:
    """Replace everything known about where this title plays in one country.

    Replace rather than merge, deliberately. Offers disappear as well as
    appear, and a merge would leave the row for a service the title has left --
    which would then be recommended as available. That is the exact promise
    this app makes, so it is the exact way it must not fail.

    Does not commit: this usually runs inside a larger unit of work, and a
    resolve pass that half-succeeded should take its offers down with it.
    """
    when = now or datetime.now(UTC)

    session.execute(delete(Offer).where(Offer.title_id == title.id, Offer.country == country))
    # Flush the deletes before the inserts, or the unique constraint sees the
    # old rows and the new ones at the same time.
    session.flush()

    seen: set[tuple[str, str, str]] = set()
    stored = 0
    for entry in offers:
        # JustWatch is under no obligation to deduplicate for us, and a unique
        # violation here would abandon the whole surrounding pass.
        key = (entry.provider, entry.monetization, entry.presentation)
        if key in seen:
            continue
        seen.add(key)

        session.add(
            Offer(
                title_id=title.id,
                country=country,
                provider_short_name=entry.provider,
                monetization_type=entry.monetization,
                presentation_type=entry.presentation,
                url=entry.url,
                price_string=entry.price_string,
                price_value=entry.price_value,
                price_currency=entry.price_currency,
                available_to=entry.available_to,
                fetched_at=when,
            )
        )
        stored += 1

    # Recorded even when nothing came back. A title streaming nowhere has no
    # offer rows, so without this "we asked and the answer was nothing" would
    # be indistinguishable from "we have never asked", and it would be fetched
    # again on every pass for ever.
    title.offers_fetched_at = when
    session.flush()
    return stored


def cached_offers(session: Session, title_id: int, *, country: str) -> list[OfferRecord]:
    """What we believe about a title's availability, in the pure shape.

    The availability rule knows nothing about SQLAlchemy and must stay that
    way, so this is where mapped rows become plain records.
    """
    rows = session.scalars(
        select(Offer).where(Offer.title_id == title_id, Offer.country == country)
    )
    return [
        OfferRecord(
            provider=row.provider_short_name,
            monetization=row.monetization_type,
            presentation=row.presentation_type,
            url=row.url,
            price_string=row.price_string,
        )
        for row in rows
    ]


def titles_needing_refresh(
    session: Session,
    *,
    now: datetime,
    ttl: timedelta = DEFAULT_TTL,
    limit: int = DEFAULT_REFRESH_LIMIT,
) -> list[Title]:
    """Catalogue rows whose availability is old enough to be worth re-asking.

    Oldest first, nulls first. A refresh is a budget of requests, and spending
    it on the answers most likely to have changed is the only sensible order.
    """
    # The first sort key is what puts never-asked titles at the front, and it
    # cannot be dropped even though it looks redundant: SQLite sorts NULLs first
    # ascending, but Postgres sorts them last, and this app runs on both. Sorting
    # on the boolean makes the intent explicit and identical on either. No test
    # can hold this -- the suite runs on SQLite, where the wrong version passes.
    candidates = session.scalars(
        select(Title).order_by(Title.offers_fetched_at.is_not(None), Title.offers_fetched_at)
    )
    stale = [title for title in candidates if is_stale(title.offers_fetched_at, now=now, ttl=ttl)]
    return stale[:limit]


def refresh_stale_offers(
    session: Session,
    catalogue: CatalogueLookup,
    *,
    now: datetime | None = None,
    ttl: timedelta = DEFAULT_TTL,
    limit: int = DEFAULT_REFRESH_LIMIT,
) -> RefreshSummary:
    """Re-ask JustWatch about the titles whose availability has gone stale.

    One lookup per title, because ``details`` returns the offers along with
    everything else -- the same trick that makes resolution fill the cache for
    free.
    """
    when = now or datetime.now(UTC)
    summary = RefreshSummary()

    for title in titles_needing_refresh(session, now=when, ttl=ttl, limit=limit):
        try:
            entry = catalogue.details(title.jw_node_id)
        except JustWatchError:
            # Contained, like a resolve pass: one dropped request must not
            # abandon the rest. Nothing is written, so offers_fetched_at stays
            # as it was and the title is tried again next time -- marking it
            # fetched here would buy it another week of being treated as known
            # when nothing was learned about it at all.
            _log.warning("could not refresh availability for %r", title.jw_node_id, exc_info=True)
            summary = RefreshSummary(
                refreshed=summary.refreshed,
                failed=summary.failed + 1,
                offers_stored=summary.offers_stored,
            )
            continue

        stored = store_offers(session, title, entry.offers, country=catalogue.country, now=when)
        summary = RefreshSummary(
            refreshed=summary.refreshed + 1,
            failed=summary.failed,
            offers_stored=summary.offers_stored + stored,
        )

    session.commit()
    return summary

"""Keeping a pool of things worth watching that nobody here has seen.

Every other source of titles in this app starts from somebody's viewing
history, which means every title it knows about is one they have already
watched. A recommender built on that can only ever suggest a rewatch. So there
has to be a second source, and JustWatch's popularity listing is it.

Almost every decision here is about restraint. JustWatch has no public API, one
top-up costs several requests, and what is popular in a country does not change
between two evenings -- so the pool is refilled on a timer rather than on
demand, and a request that finds it fresh spends nothing at all.

One subtlety carries the product rule. Filtering the listing to the services
somebody pays for gives a pool that is almost entirely watchable, which is what
we want -- but it structurally cannot return anything that is *free* on a
service they do not have, and the availability rule says those count. So one
unfiltered page is fetched alongside, purely to keep those reachable.

This module is impure: it owns the session and the catalogue client.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from simplejustwatchapi.exceptions import JustWatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.availability import is_stale
from app.models import DEFAULT_USER_ID, DiscoveryRun
from app.services.justwatch_client import POPULAR_RESULTS, CatalogueEntry, CataloguePopular
from app.services.titles import store_title

_log = logging.getLogger(__name__)

# How long a filled pool is trusted. A day, because popularity moves on the
# order of weeks and every refill is several requests against somebody else's
# infrastructure. Shorter would buy almost nothing; much longer and a title
# leaving a service would sit in the pool unnoticed.
DEFAULT_TTL = timedelta(days=1)

# How many pages of the listing to walk. The pool is filtered hard afterwards --
# by availability, by runtime, by everything already watched -- so a single page
# routinely leaves nothing to recommend.
DEFAULT_PAGES = 3


@dataclass(frozen=True)
class DiscoverySummary:
    """What one top-up did, or why it did nothing."""

    seen: int = 0
    added: int = 0
    requests: int = 0
    # The pool was still fresh and nothing was asked of JustWatch.
    skipped: bool = False
    # Every page failed, so nothing at all was learned. Reported rather than
    # raised: a recommendation request that cannot top the pool up should still
    # answer from the pool it already has.
    failed: bool = False


def pool_is_stale(
    session: Session,
    *,
    country: str,
    now: datetime,
    ttl: timedelta = DEFAULT_TTL,
    user_id: str = DEFAULT_USER_ID,
) -> bool:
    """Whether the pool for one country is old enough to be worth refilling.

    Never filled counts as stale, the same way an unfetched offer does: no
    answer is not the same as an empty one.
    """
    latest = session.scalars(
        select(DiscoveryRun.fetched_at)
        .where(DiscoveryRun.user_id == user_id, DiscoveryRun.country == country)
        .order_by(DiscoveryRun.fetched_at.desc())
        .limit(1)
    ).one_or_none()
    return is_stale(latest, now=now, ttl=ttl)


def refresh_pool(
    session: Session,
    catalogue: CataloguePopular,
    *,
    subscriptions: Sequence[str] = (),
    now: datetime | None = None,
    ttl: timedelta = DEFAULT_TTL,
    pages: int = DEFAULT_PAGES,
    page_size: int = POPULAR_RESULTS,
    force: bool = False,
    user_id: str = DEFAULT_USER_ID,
) -> DiscoverySummary:
    """Top the pool up from JustWatch's popularity listing, if it needs it.

    The country is read off the client rather than passed in, for the same
    reason the offer cache does it: the listing describes whichever country the
    request was actually made for, and a second source for that answer is a
    second chance to disagree with it.
    """
    when = now or datetime.now(UTC)
    country = catalogue.country

    stale = pool_is_stale(session, country=country, now=when, ttl=ttl, user_id=user_id)
    if not (force or stale):
        return DiscoverySummary(skipped=True)

    summary = DiscoverySummary()
    for providers, offset in _requests_to_make(subscriptions, pages=pages, page_size=page_size):
        try:
            entries = catalogue.popular(providers=providers, offset=offset, count=page_size)
        except JustWatchError:
            # Contained like a resolve pass: one dropped request must not
            # abandon the pages that already worked. Paging stops, because a
            # listing that just failed is unlikely to serve the next slice.
            _log.warning("could not fetch popular titles for %r", country, exc_info=True)
            summary = replace(summary, failed=True)
            break

        summary = replace(summary, requests=summary.requests + 1, failed=False)
        summary = _store(session, entries, country=country, when=when, summary=summary)

    if not summary.requests:
        # Nothing was learned, so nothing is recorded. Writing a run here would
        # cache the outage: a failure that might clear in a minute would block
        # discovery for a whole day instead.
        #
        # And nothing is rolled back either. Zero successful requests means
        # nothing was stored, so there is nothing of ours to undo -- while the
        # session belongs to the caller, who may well have pending work of their
        # own. A service that rolls back a session it does not own destroys work
        # it never knew about, which is a spectacularly quiet way to lose data.
        return summary

    session.add(
        DiscoveryRun(
            user_id=user_id,
            country=country,
            fetched_at=when,
            titles_seen=summary.seen,
            titles_added=summary.added,
            requests_made=summary.requests,
        )
    )
    session.commit()
    return summary


def _requests_to_make(
    subscriptions: Sequence[str], *, pages: int, page_size: int
) -> list[tuple[list[str] | None, int]]:
    """The provider filter and offset for each call, in the order to make them.

    The subscribed pages come first because they are the ones most likely to
    yield something watchable, and a failure partway through should cost the
    least useful requests rather than the most.

    The unfiltered page is skipped entirely when there is nothing to filter by:
    with no subscriptions every page is already unfiltered, and a second
    identical request buys nothing.
    """
    wanted = list(subscriptions)
    calls: list[tuple[list[str] | None, int]] = [
        (wanted or None, page * page_size) for page in range(pages)
    ]
    if wanted:
        calls.append((None, 0))
    return calls


def _store(
    session: Session,
    entries: list[CatalogueEntry],
    *,
    country: str,
    when: datetime,
    summary: DiscoverySummary,
) -> DiscoverySummary:
    seen = summary.seen
    added = summary.added
    for entry in entries:
        seen += 1
        _, is_new = store_title(session, entry, country=country, now=when)
        if is_new:
            added += 1
    return replace(summary, seen=seen, added=added)

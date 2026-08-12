"""Talking to JustWatch, politely and with a plan for when it fails.

JustWatch has no public API. ``simplejustwatchapi`` speaks to the GraphQL
endpoint the website uses, which means three things are true at once: it is not
guaranteed to keep working, it can fail transiently in ways a retry would fix,
and the library's own terms ask callers for restraint. This wrapper is where all
three are handled, so nothing above it has to think about any of them.

It also converts the library's ``MediaEntry`` into our own record. That keeps the
library's shape at the edge of the system: if it renames a field, this file
breaks and one test tells us, rather than the change leaking into the resolver,
the models and the API responses.

This module is impure -- it is the network boundary.
"""

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeVar

import httpx
from simplejustwatchapi import details as jw_details
from simplejustwatchapi import popular as jw_popular
from simplejustwatchapi import providers as jw_providers
from simplejustwatchapi import search as jw_search
from simplejustwatchapi.exceptions import JustWatchError, JustWatchHttpError
from simplejustwatchapi.tuples import MediaEntry, Offer, OfferPackage

from app.core.matching import Candidate
from app.core.urls import is_catalogue_image_url, is_web_url

_log = logging.getLogger(__name__)

# Retrying and pacing are the same regardless of what is being asked for, so the
# request helper is generic over the answer rather than duplicated per endpoint.
_Answer = TypeVar("_Answer")

# How many results to ask for per search. More than one on purpose: the matcher
# measures how far the best candidate is clear of the runner-up, and with a
# single result there is no runner-up, so its ambiguity check silently never
# fires. Ten is enough for the "Dune" case without paging.
SEARCH_RESULTS = 10

# How many popular titles to ask for per page. Much larger than a search,
# because a discovery pool is filtered hard afterwards -- by availability, by
# runtime, by what has already been watched -- and asking for a handful would
# routinely leave nothing to recommend. Larger still risks the API refusing the
# query outright for complexity.
POPULAR_RESULTS = 50

# The floor on the gap between two requests. Not a rate limit imposed on us --
# a rate limit we impose on ourselves, because this is someone else's
# infrastructure and we are not a paying customer of it.
MIN_REQUEST_INTERVAL_SECONDS = 1.0

# The most requests one HTTP call may ask a pass to spend. At the interval above
# this is already a quarter of an hour inside a single request, which is well
# past what any caller should be asking for -- the endpoints report `remaining`
# so that batches can be driven in a loop. It is input hygiene rather than a
# safety limit: a caller may still omit the limit entirely and get the whole
# library, and what stops that costing the app anything is
# `services/single_flight`, not this number.
MAX_REQUESTS_PER_PASS = 1000

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0

# Statuses that mean "not now" rather than "not ever". 429 is the API asking us
# to slow down, which is a request we should honour rather than abandon.
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


class UnusableCatalogueEntry(JustWatchError):
    """JustWatch answered, but the entry it returned cannot be used.

    Deliberately a ``JustWatchError``: callers already guard the catalogue with
    one except clause, and a new error that escaped it would turn a single bad
    row into a failed run.
    """


@dataclass(frozen=True)
class OfferEntry:
    """One way a title can be watched in the client's country.

    ``provider`` is the package's short name (``nfx``, ``prv``) because that is
    the only field that joins an offer to a subscription; the rest of the
    package is catalogue data that belongs in the provider table, not repeated
    on every offer.
    """

    provider: str
    monetization: str
    presentation: str = ""
    url: str | None = None
    price_string: str | None = None
    price_value: float | None = None
    price_currency: str | None = None
    available_to: datetime | None = None


@dataclass(frozen=True)
class ProviderEntry:
    """A streaming service as JustWatch lists it for one country."""

    short_name: str
    technical_name: str
    name: str
    monetization_types: tuple[str, ...] = ()
    icon_url: str | None = None


@dataclass(frozen=True)
class CatalogueEntry:
    """What JustWatch knows about one title.

    Frozen and hashable, so it can be collected in a set while resolving without
    anything downstream editing a shared object by accident.
    """

    node_id: str
    title: str
    object_type: str
    release_year: int | None = None
    runtime_minutes: int | None = None
    genres: tuple[str, ...] = ()
    imdb_id: str | None = None
    tmdb_id: str | None = None
    poster_url: str | None = None
    imdb_score: float | None = None
    tmdb_score: float | None = None
    tomatometer: int | None = None
    # Where it can be watched, in the country the client was built for.
    # JustWatch returns this with the search itself, so resolving a library
    # fills the availability cache at no extra cost in requests.
    offers: tuple[OfferEntry, ...] = ()

    def as_candidate(self) -> Candidate:
        """The reduced form the matcher works with.

        The matcher is pure and must stay that way, so it never sees a record
        with a poster URL and a Rotten Tomatoes score on it -- only the four
        fields that bear on which title this is.
        """
        return Candidate(
            node_id=self.node_id,
            title=self.title,
            object_type=self.object_type,
            release_year=self.release_year,
        )


class CatalogueSearch(Protocol):
    """What an automatic resolve pass depends on.

    Narrow by design: the test double has to implement this and nothing else,
    which is what keeps the resolver's tests off the network.
    """

    # Availability is only meaningful per country, and offers are cached as they
    # arrive with a search, so whoever stores them has to know which country
    # they describe. Reading it off the client is what stops that answer and the
    # request that produced it ever disagreeing.
    country: str

    def search(
        self, title: str, *, object_types: Sequence[str] | None = None
    ) -> list[CatalogueEntry]: ...


class CatalogueLookup(Protocol):
    """What a manual fix and an availability refresh depend on.

    Separate from :class:`CatalogueSearch` rather than bundled with it because
    they have different callers: the resolve pass never looks a title up by id,
    and a fixture that had to implement both would be describing capabilities
    the code under test cannot use. ``JustWatchClient`` satisfies both.
    """

    # Same reason as on CatalogueSearch: a lookup returns offers too, and offers
    # are only meaningful alongside the country they were fetched for.
    country: str

    def details(self, node_id: str) -> CatalogueEntry: ...


class CatalogueProviders(Protocol):
    """What a provider-catalogue refresh depends on.

    Narrow like the others, and for the same reason: refreshing the list of
    streaming services has no business being able to search for a title.
    """

    country: str

    def providers(self) -> list[ProviderEntry]: ...


class CataloguePopular(Protocol):
    """What filling the discovery pool depends on.

    The only source in this client of titles nobody has watched. Everything
    else starts from something already in the user's history, which can only
    ever produce a rewatch.
    """

    country: str

    def popular(
        self,
        *,
        providers: Sequence[str] | None = None,
        object_types: Sequence[str] | None = None,
        count: int = POPULAR_RESULTS,
        offset: int = 0,
    ) -> list[CatalogueEntry]: ...


class JustWatchClient:
    """A rate-limited, retrying view of the JustWatch catalogue.

    Every collaborator that makes the class hard to test -- the search function,
    the clock, the sleep -- is an argument, so the retry and pacing policy can be
    verified without a network or a slow test suite.
    """

    def __init__(
        self,
        *,
        country: str,
        language: str,
        results: int = SEARCH_RESULTS,
        min_interval: float = MIN_REQUEST_INTERVAL_SECONDS,
        max_attempts: int = MAX_ATTEMPTS,
        backoff_base: float = BACKOFF_BASE_SECONDS,
        search_fn: Callable[..., list[MediaEntry]] = jw_search,
        details_fn: Callable[..., MediaEntry] = jw_details,
        providers_fn: Callable[..., list[OfferPackage]] = jw_providers,
        popular_fn: Callable[..., list[MediaEntry]] = jw_popular,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        # Public: availability is meaningless without the country it was
        # fetched for, and the country the offers belong to is definitionally
        # the one this client asked about. Callers reading it from here rather
        # than from their own settings cannot disagree with it.
        self.country = country
        self._language = language
        self._results = results
        self._min_interval = min_interval
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._search_fn = search_fn
        self._details_fn = details_fn
        self._providers_fn = providers_fn
        self._popular_fn = popular_fn
        self._sleep = sleep
        self._monotonic = monotonic

        # FastAPI serves synchronous endpoints from a thread pool, so one client
        # can be pacing several requests at once. Without the lock, two threads
        # both read a stale "last request" time and neither waits.
        self._lock = threading.Lock()
        self._last_request_at: float | None = None

    def search(
        self, title: str, *, object_types: Sequence[str] | None = None
    ) -> list[CatalogueEntry]:
        """Search the catalogue, returning candidates best-first."""
        entries = self._call(
            self._search_fn,
            title,
            country=self.country,
            language=self._language,
            count=self._results,
            best_only=True,
            object_types=list(object_types) if object_types else None,
        )
        return [_to_catalogue_entry(entry) for entry in _usable(entries, title)]

    def details(self, node_id: str) -> CatalogueEntry:
        """Look one title up by its JustWatch id.

        This is what a manual fix runs on. A stored candidate carries only
        enough to render a button -- id, title, type, year -- and the catalogue
        row needs the genres, runtime and scores everything downstream reads.

        Raises:
            UnusableCatalogueEntry: if the answer has no id or no title. A
                search drops such a result and keeps the others; a lookup has no
                others, and a manual fix that silently does nothing is worse
                than one that says why it could not.
        """
        entry = self._call(
            self._details_fn,
            node_id,
            country=self.country,
            language=self._language,
            best_only=True,
        )
        if not _is_usable(entry):
            raise UnusableCatalogueEntry(
                f"JustWatch returned no usable entry for {node_id!r}: "
                f"id={entry.entry_id!r} title={entry.title!r}"
            )
        return _to_catalogue_entry(entry)

    def providers(self) -> list[ProviderEntry]:
        """Every streaming service JustWatch knows about in this country.

        Refreshed occasionally rather than per request: services are added and
        renamed on the order of months, and this is the list the settings page
        renders for someone to tick.
        """
        packages = self._call(self._providers_fn, self.country)
        return [_to_provider_entry(package) for package in packages if package.short_name]

    def popular(
        self,
        *,
        providers: Sequence[str] | None = None,
        object_types: Sequence[str] | None = None,
        count: int = POPULAR_RESULTS,
        offset: int = 0,
    ) -> list[CatalogueEntry]:
        """What is being watched in this country, optionally on given services.

        The recommender's only source of titles nobody has seen: search and
        lookup both start from something already in the history, so a pool built
        from them could only ever offer a rewatch.

        An empty provider selection is sent as no selection at all. Somebody
        with no subscriptions can still watch anything that is free, so an empty
        list has to mean "everything" rather than "nothing" -- and the library
        would reach the same answer by accident, which is not the same as
        meaning it.
        """
        entries = self._call(
            self._popular_fn,
            self.country,
            language=self._language,
            count=count,
            best_only=True,
            offset=offset,
            providers=list(providers) if providers else None,
            object_types=list(object_types) if object_types else None,
        )
        return [_to_catalogue_entry(entry) for entry in _usable(entries, "popular titles")]

    def _call(self, request: Callable[..., _Answer], *args, **kwargs) -> _Answer:
        """Make one request, retrying only what a retry could fix."""
        for attempt in range(1, self._max_attempts + 1):
            self._wait_for_turn()
            try:
                return request(*args, **kwargs)
            except JustWatchError as error:
                if attempt == self._max_attempts or not _is_retryable(error):
                    raise
                # Each wait is longer than the last. An API that is already
                # struggling is not helped by a client that retries at full
                # speed, and a client that does tends to stop being served.
                self._sleep(self._backoff_base * 2 ** (attempt - 1))
        raise AssertionError("unreachable: the loop returns or raises")

    def _wait_for_turn(self) -> None:
        """Hold off until enough time has passed since the last request.

        The sleep happens while holding the lock, which serialises requests
        rather than merely spacing each thread's own. That is the intent: the
        limit is on what we send JustWatch in total, not per worker.
        """
        with self._lock:
            if self._last_request_at is not None:
                remaining = self._min_interval - (self._monotonic() - self._last_request_at)
                if remaining > 0:
                    self._sleep(remaining)
            self._last_request_at = self._monotonic()


def _is_retryable(error: JustWatchError) -> bool:
    """Whether sending the same request again could plausibly work.

    A malformed query, an unknown node or a bad country code fails identically
    however many times it is sent, so retrying it only wastes time and spends
    request budget that is not ours to spend.
    """
    if not isinstance(error, JustWatchHttpError):
        # JustWatchApiError: the GraphQL layer answered and refused. Asking
        # again gets the same refusal.
        return False

    status = _status_code(error)
    # No status at all means the request never got an answer -- a timeout or a
    # dropped connection, which is the case retrying exists for.
    if status is None:
        return True
    return status in RETRYABLE_STATUS_CODES


def _status_code(error: JustWatchHttpError) -> int | None:
    """Recover the HTTP status the library discarded.

    ``JustWatchHttpError`` flattens "the server said 404" and "the connection
    timed out" into one class with no status on it. Those need opposite
    responses, and the distinction survives on the underlying httpx error that
    the library chains as ``__cause__``.
    """
    cause = error.__cause__
    if isinstance(cause, httpx.HTTPStatusError):
        return cause.response.status_code
    return None


def _is_usable(entry: MediaEntry) -> bool:
    """Whether a result can safely be passed on.

    Two fields are load-bearing rather than merely nice to have. Without a title
    the matcher cannot run at all -- ``normalize_title`` is typed for a string
    and a null takes down the whole resolve run over one bad row. Without an id
    the entry cannot be stored, because ``titles.jw_node_id`` is what identifies
    the row.
    """
    return bool(entry.entry_id) and bool((entry.title or "").strip())


def _usable(entries: list[MediaEntry], query: str) -> list[MediaEntry]:
    """Drop the results that cannot be used, saying so.

    Neither defect is recoverable and neither is worth abandoning a search over,
    so the bad row goes and the good ones stay. Dropping silently would leave a
    title mysteriously unresolvable with nothing to explain why.
    """
    kept = []
    for entry in entries:
        if _is_usable(entry):
            kept.append(entry)
        else:
            _log.warning(
                "dropping an unusable JustWatch result for %r: id=%r title=%r",
                query,
                entry.entry_id,
                entry.title,
            )
    return kept


def _to_provider_entry(package: OfferPackage) -> ProviderEntry:
    return ProviderEntry(
        short_name=package.short_name,
        technical_name=package.technical_name or "",
        name=package.name or package.short_name,
        monetization_types=tuple(kind for kind in (package.monetization_types or ()) if kind),
        icon_url=_catalogue_image(package.icon, kind="provider icon"),
    )


def _to_offer_entry(offer: Offer) -> OfferEntry:
    return OfferEntry(
        provider=offer.package.short_name,
        monetization=offer.monetization_type,
        # Blank rather than null: the offer cache deduplicates on this column,
        # and SQL does not consider two nulls equal.
        presentation=offer.presentation_type or "",
        url=_play_link(offer.url),
        price_string=offer.price_string,
        price_value=offer.price_value,
        price_currency=offer.price_currency,
        available_to=_expiry(offer.available_to),
    )


def _play_link(url: str | None) -> str | None:
    """Keep the deep link only if it is somewhere a browser can be sent.

    Only the link is dropped, never the offer: availability is the valuable
    part and does not depend on having a link, so refusing the whole row would
    make a watchable title look unwatchable -- the one failure this app exists
    to avoid. The frontend already renders a plain label when there is nowhere
    to send anybody.

    Said out loud rather than dropped quietly, like every other thing this
    module declines. `core.urls` explains what the check is worth and what it
    is not; `core.availability` applies the same rule on the way out, for the
    rows cached before this existed.
    """
    if url is None or is_web_url(url):
        return url

    _log.warning("dropping an unusable offer link from JustWatch: %r", url)
    return None


def _catalogue_image(url: str | None, *, kind: str) -> str | None:
    """Keep an image URL only if it names the host these are all built from.

    Stricter than the deep link above, because the two are not the same risk: a
    link waits for a click, and an `src` is fetched as the page draws. `core.urls`
    carries the reasoning and the shapes that make it necessary.

    Empty becomes null. The library returns "" for a package with no icon, and a
    blank string in a URL column is a value that has to be remembered about
    everywhere it is read.
    """
    if not url:
        return None
    if is_catalogue_image_url(url):
        return url

    _log.warning("dropping a %s from an unexpected host: %r", kind, url)
    return None


def _usable_offers(offers: list[Offer] | None) -> tuple[OfferEntry, ...]:
    """Convert the offers worth keeping.

    An offer with no package has no short name, and the short name is the only
    thing that can match it to a subscription -- so it could never make a title
    watchable and would only ever be dead weight in the cache.
    """
    return tuple(
        _to_offer_entry(offer)
        for offer in (offers or ())
        if offer.package is not None and offer.package.short_name
    )


def _expiry(value: str | None) -> datetime | None:
    """Read ``available_to``, which arrives as an unparsed string.

    Always aware and always UTC: the timestamp column refuses a naive value
    outright, so a bare date left unattached would fail at write time, one
    layer away from the thing that caused it.

    A format we cannot read costs us the expiry, not the offer. Knowing where
    to watch something is the point; knowing when it leaves is a nicety, and
    losing a whole search over it would be badly out of proportion.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _log.warning("could not read an offer expiry date: %r", value)
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _to_catalogue_entry(entry: MediaEntry) -> CatalogueEntry:
    """Convert one library result into our own record."""
    scoring = entry.scoring
    return CatalogueEntry(
        node_id=entry.entry_id,
        title=entry.title,
        object_type=entry.object_type,
        release_year=entry.release_year,
        runtime_minutes=entry.runtime_minutes,
        # A genre node without a short name is not a genre. Keeping the null
        # would put it in the JSON column, where the taste profile would later
        # weigh it as though it were one.
        genres=tuple(genre for genre in (entry.genres or ()) if genre),
        imdb_id=entry.imdb_id,
        # JustWatch returns TMDB ids as numbers about as often as strings, and
        # the column is text either way.
        tmdb_id=str(entry.tmdb_id) if entry.tmdb_id is not None else None,
        poster_url=_catalogue_image(entry.poster, kind="poster"),
        # Obscure titles come back with no scoring block at all.
        imdb_score=scoring.imdb_score if scoring else None,
        tmdb_score=scoring.tmdb_score if scoring else None,
        tomatometer=scoring.tomatometer if scoring else None,
        offers=_usable_offers(entry.offers),
    )

"""Tests for the JustWatch wrapper.

None of these touch the network. The client takes the library's ``search`` and
``details`` functions, a sleep and a clock as arguments, so every behaviour worth
having -- retrying, backing off, spacing requests out -- is observable by passing
in fakes and reading what the client did.

The point of the wrapper is that JustWatch is an unofficial API. It can be slow,
it can fail, and the library asks callers for restraint. All three of those are
policy decisions, and policy that lives in a tested object is policy you can
change on purpose.
"""

from datetime import UTC, datetime

import httpx
import pytest
from simplejustwatchapi.exceptions import JustWatchApiError, JustWatchError, JustWatchHttpError
from simplejustwatchapi.tuples import MediaEntry, Offer, OfferPackage, Scoring

from app.services.justwatch_client import (
    BACKOFF_BASE_SECONDS,
    MAX_ATTEMPTS,
    CatalogueEntry,
    JustWatchClient,
    UnusableCatalogueEntry,
)

_MEDIA_ENTRY_DEFAULTS = {
    "entry_id": "tm12345",
    "object_id": 12345,
    "object_type": "MOVIE",
    "title": "Inception",
    "url": "https://www.justwatch.com/in/movie/inception",
    "release_year": 2010,
    "release_date": "2010-07-16",
    "runtime_minutes": 148,
    "short_description": "A thief who steals corporate secrets.",
    "genres": ["act", "scf"],
    "imdb_id": "tt1375666",
    "tmdb_id": "27205",
    "poster": "https://images.justwatch.com/poster/inception.jpg",
    "backdrops": [],
    "age_certification": "UA",
    "scoring": Scoring(
        imdb_score=8.8,
        imdb_votes=2_500_000,
        tmdb_popularity=90.1,
        tmdb_score=8.4,
        tomatometer=87,
        certified_fresh=True,
        jw_rating=0.99,
    ),
    "interactions": None,
    "streaming_charts": None,
    "offers": [],
    "total_season_count": None,
    "total_episode_count": None,
    "season_number": None,
    "episode_number": None,
}


_OFFER_PACKAGE_DEFAULTS = {
    "id": "cGF8OA==",
    "package_id": 8,
    "name": "Netflix",
    "technical_name": "netflix",
    "short_name": "nfx",
    "monetization_types": ["FLATRATE"],
    "icon": "https://images.justwatch.com/icon/207360008/s100/netflix.png",
}

_OFFER_DEFAULTS = {
    "id": "b2Z8dG05MjY0MTpJTjpuZng6ZmxhdHJhdGU6aGQ=",
    "monetization_type": "FLATRATE",
    "presentation_type": "HD",
    "price_string": None,
    "price_value": None,
    "price_currency": "INR",
    "last_change_retail_price_value": None,
    "type": "STANDARD",
    "package": None,  # filled in by offer() so the default package is shared
    "url": "https://www.netflix.com/title/70131314",
    "element_count": 0,
    "available_to": None,
    "deeplink_roku": None,
    "subtitle_languages": ["en"],
    "video_technology": [""],
    "audio_technology": ["_5_POINT_1"],
    "audio_languages": ["en", "hi"],
}


def offer_package(**overrides) -> OfferPackage:
    return OfferPackage(**{**_OFFER_PACKAGE_DEFAULTS, **overrides})


def offer(**overrides) -> Offer:
    """One availability row, built from the library's own named tuple.

    ``package`` defaults to Netflix rather than to None; pass ``package=None``
    explicitly to describe a malformed one.
    """
    values = {**_OFFER_DEFAULTS, **overrides}
    if "package" not in overrides:
        values["package"] = offer_package()
    return Offer(**values)


def media_entry(**overrides) -> MediaEntry:
    """A library result, built from the real named tuple.

    Deliberately not a stub: constructing the library's own type means a field
    it renames or drops breaks these tests instead of silently breaking
    resolution against the live API.
    """
    return MediaEntry(**{**_MEDIA_ENTRY_DEFAULTS, **overrides})


def http_error(status_code: int) -> JustWatchHttpError:
    """The library's error as it arrives from a non-2xx response.

    It flattens status errors and network errors into one class, so the client
    recovers the status from the underlying httpx error. Building it the same
    way the library does keeps that inference honest.
    """
    request = httpx.Request("POST", "https://apis.justwatch.com/graphql")
    response = httpx.Response(status_code, request=request, text="upstream said no")
    cause = httpx.HTTPStatusError("boom", request=request, response=response)
    error = JustWatchHttpError(str(cause), response.text)
    error.__cause__ = cause
    return error


def network_error() -> JustWatchHttpError:
    """A timeout or a dropped connection: no status code to read."""
    error = JustWatchHttpError("timed out")
    error.__cause__ = httpx.ConnectTimeout("timed out")
    return error


class RecordingCall:
    """Stands in for one of the library's request functions.

    Each element of ``results`` is either a value to return or an exception to
    raise, so a test can describe "fails twice, then works". The last outcome
    repeats once the script runs out, so a test that only cares about the steady
    state does not have to count calls to write it.
    """

    def __init__(self, *results):
        self.results = list(results)
        self.calls: list[dict] = []

    def __call__(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        outcome = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClock:
    """A monotonic clock that only moves when something sleeps."""

    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def build_client(search, clock: FakeClock, **kwargs) -> JustWatchClient:
    return JustWatchClient(
        country="IN",
        language="en",
        search_fn=search,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        **kwargs,
    )


def build_lookup_client(details, clock: FakeClock, **kwargs) -> JustWatchClient:
    return build_client(RecordingCall([]), clock, details_fn=details, **kwargs)


class TestSearchResults:
    def test_a_result_becomes_a_catalogue_entry(self, clock: FakeClock):
        client = build_client(RecordingCall([media_entry()]), clock)

        [entry] = client.search("Inception")

        assert isinstance(entry, CatalogueEntry)
        assert entry.node_id == "tm12345"
        assert entry.title == "Inception"
        assert entry.object_type == "MOVIE"
        assert entry.release_year == 2010
        assert entry.runtime_minutes == 148

    def test_scores_are_flattened_out_of_the_scoring_tuple(self, clock: FakeClock):
        client = build_client(RecordingCall([media_entry()]), clock)

        [entry] = client.search("Inception")

        assert entry.imdb_score == 8.8
        assert entry.tmdb_score == 8.4
        assert entry.tomatometer == 87

    def test_a_title_with_no_scores_still_resolves(self, clock: FakeClock):
        """Obscure titles come back with ``scoring=None``, which must not crash."""
        client = build_client(RecordingCall([media_entry(scoring=None)]), clock)

        [entry] = client.search("Some Obscure Thing")

        assert entry.imdb_score is None
        assert entry.tomatometer is None

    def test_genres_come_back_hashable(self, clock: FakeClock):
        """The entry is frozen, so a mutable list inside it would be a lie."""
        client = build_client(RecordingCall([media_entry()]), clock)

        [entry] = client.search("Inception")

        assert entry.genres == ("act", "scf")
        hash(entry)

    def test_an_entry_converts_to_a_matcher_candidate(self, clock: FakeClock):
        """The matcher is pure and knows nothing about JustWatch, so the client
        hands it the reduced form rather than its own record."""
        client = build_client(RecordingCall([media_entry()]), clock)

        [entry] = client.search("Inception")
        candidate = entry.as_candidate()

        assert candidate.node_id == "tm12345"
        assert candidate.title == "Inception"
        assert candidate.object_type == "MOVIE"
        assert candidate.release_year == 2010

    def test_no_results_is_an_empty_list_not_an_error(self, clock: FakeClock):
        client = build_client(RecordingCall([]), clock)

        assert client.search("Nothing At All") == []


class TestUnusableResults:
    """A malformed result must not be allowed to reach the rest of the app.

    An entry with no title crashes the matcher outright -- ``normalize_title``
    is typed for a string -- so one bad row among ten would abort resolution for
    an entire library. An entry with no id cannot be stored, because
    ``titles.jw_node_id`` is the identity of the row. Neither is recoverable and
    neither should be fatal, so they are dropped and the usable results kept.
    """

    def test_a_result_with_no_id_is_dropped(self, clock: FakeClock):
        client = build_client(RecordingCall([media_entry(entry_id=None)]), clock)

        assert client.search("Inception") == []

    def test_a_result_with_no_title_is_dropped(self, clock: FakeClock):
        client = build_client(RecordingCall([media_entry(title=None)]), clock)

        assert client.search("Inception") == []

    def test_a_result_with_a_blank_title_is_dropped(self, clock: FakeClock):
        client = build_client(RecordingCall([media_entry(title="   ")]), clock)

        assert client.search("Inception") == []

    def test_the_usable_results_survive_alongside_a_bad_one(self, clock: FakeClock):
        """Dropping the broken row must not cost us the good ones."""
        search = RecordingCall(
            [
                media_entry(entry_id=None),
                media_entry(entry_id="tm999", title="Interstellar"),
            ]
        )
        client = build_client(search, clock)

        [entry] = client.search("Interstellar")

        assert entry.node_id == "tm999"

    def test_missing_genre_names_are_dropped_rather_than_stored_as_null(self, clock: FakeClock):
        """These feed the taste profile, which weighs genres by name. A null in
        that list is not a genre and would be counted as though it were one."""
        client = build_client(RecordingCall([media_entry(genres=["act", None])]), clock)

        [entry] = client.search("Inception")

        assert entry.genres == ("act",)


class TestLookingOneTitleUp:
    """``details`` exists for the manual fixer.

    When someone picks a title by hand, all we have is the id from a stored
    candidate -- enough to render a button and nothing else. The catalogue row
    needs the genres, runtime and scores that the taste profile and the
    recommender read, so the id is exchanged for the full entry.
    """

    def test_a_node_id_becomes_a_catalogue_entry(self, clock: FakeClock):
        client = build_lookup_client(RecordingCall(media_entry()), clock)

        entry = client.details("tm12345")

        assert isinstance(entry, CatalogueEntry)
        assert entry.node_id == "tm12345"
        assert entry.title == "Inception"
        assert entry.genres == ("act", "scf")
        assert entry.runtime_minutes == 148

    def test_the_configured_country_and_language_are_used(self, clock: FakeClock):
        details = RecordingCall(media_entry())
        client = build_lookup_client(details, clock)

        client.details("tm12345")

        assert details.calls[0]["query"] == "tm12345"
        assert details.calls[0]["country"] == "IN"
        assert details.calls[0]["language"] == "en"

    def test_a_transient_failure_is_retried(self, clock: FakeClock):
        details = RecordingCall(network_error(), media_entry())
        client = build_lookup_client(details, clock)

        assert client.details("tm12345").node_id == "tm12345"
        assert len(details.calls) == 2

    def test_a_lookup_waits_its_turn_behind_a_search(self, clock: FakeClock):
        """One budget, not two. The rate limit is on what we send JustWatch in
        total, so a lookup must not get a free request by using another method."""
        search = RecordingCall([media_entry()])
        details = RecordingCall(media_entry())
        client = build_client(search, clock, details_fn=details, min_interval=2.0)

        client.search("Inception")
        searched_at = clock.now
        client.details("tm12345")

        assert clock.now - searched_at >= 2.0

    def test_an_unusable_entry_is_an_error_rather_than_a_silent_null(self, clock: FakeClock):
        """A search drops a malformed result and keeps the rest. A lookup has no
        rest to keep, and a manual fix that quietly does nothing is worse than
        one that says why it could not."""
        client = build_lookup_client(RecordingCall(media_entry(entry_id=None)), clock)

        with pytest.raises(UnusableCatalogueEntry):
            client.details("tm12345")

    def test_an_unusable_entry_can_be_caught_as_any_other_justwatch_failure(self, clock: FakeClock):
        """Callers already guard the catalogue with one except clause; a new
        error that escapes it would take down a resolve pass."""
        client = build_lookup_client(RecordingCall(media_entry(title=None)), clock)

        with pytest.raises(JustWatchError):
            client.details("tm12345")


class TestOffersOnASearchResult:
    """Availability arrives with the search, so it costs nothing extra.

    JustWatch returns where a title can be watched in the same response that
    answers what the title is. Reading it here means resolving a library also
    fills the availability cache, instead of spending a second request per
    title to ask a question we were already told the answer to.
    """

    def test_offers_come_through_with_the_entry(self, clock: FakeClock):
        client = build_client(RecordingCall([media_entry(offers=[offer()])]), clock)

        [entry] = client.search("Inception")

        assert len(entry.offers) == 1

    def test_the_provider_short_name_is_lifted_out_of_the_package(self, clock: FakeClock):
        """The short name is the only thing that joins an offer to a
        subscription, and it is buried a level down in the library's shape."""
        client = build_client(RecordingCall([media_entry(offers=[offer()])]), clock)

        [entry] = client.search("Inception")

        assert entry.offers[0].provider == "nfx"
        assert entry.offers[0].monetization == "FLATRATE"
        assert entry.offers[0].presentation == "HD"

    def test_a_price_is_kept_whole(self, clock: FakeClock):
        rental = offer(monetization_type="RENT", price_string="₹149", price_value=149.0)
        client = build_client(RecordingCall([media_entry(offers=[rental])]), clock)

        [entry] = client.search("Inception")

        assert entry.offers[0].price_string == "₹149"
        assert entry.offers[0].price_value == 149.0
        assert entry.offers[0].price_currency == "INR"

    def test_entries_stay_hashable_with_offers_on_them(self, clock: FakeClock):
        """The record is frozen, so a list of offers inside it would be a lie."""
        client = build_client(RecordingCall([media_entry(offers=[offer()])]), clock)

        [entry] = client.search("Inception")

        hash(entry)

    def test_no_offers_is_an_empty_tuple(self, clock: FakeClock):
        client = build_client(RecordingCall([media_entry(offers=[])]), clock)

        [entry] = client.search("Inception")

        assert entry.offers == ()

    def test_an_offer_with_no_package_is_dropped(self, clock: FakeClock):
        """Without a short name it can never match a subscription, so it can
        only ever be dead weight in the cache."""
        client = build_client(RecordingCall([media_entry(offers=[offer(package=None)])]), clock)

        [entry] = client.search("Inception")

        assert entry.offers == ()

    def test_an_offer_whose_provider_has_a_blank_short_name_is_dropped(self, clock: FakeClock):
        """The nastier version of the same defect: the package is there, so a
        null check waves it through, and the offer is stored against a provider
        named "". A subscription can never match it -- but a *free* offer needs
        no subscription, so it would make the title look watchable and the UI
        would offer to send someone to nowhere.
        """
        nameless = offer(package=offer_package(short_name=""), monetization_type="FREE")
        client = build_client(RecordingCall([media_entry(offers=[nameless])]), clock)

        [entry] = client.search("Inception")

        assert entry.offers == ()


class TestWhenAnOfferExpires:
    """``available_to`` is the "leaving Netflix in nine days" signal.

    The library hands it over as an unparsed string, so this is where it
    becomes a timestamp -- and where a value we have never seen has to not take
    down the search that carried it.
    """

    def test_an_expiry_becomes_an_aware_utc_timestamp(self, clock: FakeClock):
        leaving = offer(available_to="2026-08-31T00:00:00Z")
        client = build_client(RecordingCall([media_entry(offers=[leaving])]), clock)

        [entry] = client.search("Inception")

        assert entry.offers[0].available_to == datetime(2026, 8, 31, tzinfo=UTC)

    def test_a_bare_date_is_read_as_utc_rather_than_left_naive(self, clock: FakeClock):
        """A naive datetime is rejected outright by the timestamp column, so
        leaving one unattached would fail at write time rather than here."""
        leaving = offer(available_to="2026-08-31")
        client = build_client(RecordingCall([media_entry(offers=[leaving])]), clock)

        [entry] = client.search("Inception")

        assert entry.offers[0].available_to == datetime(2026, 8, 31, tzinfo=UTC)

    def test_no_expiry_is_none(self, clock: FakeClock):
        client = build_client(RecordingCall([media_entry(offers=[offer()])]), clock)

        [entry] = client.search("Inception")

        assert entry.offers[0].available_to is None

    def test_an_unreadable_expiry_does_not_lose_the_offer(self, clock: FakeClock):
        """Knowing where to watch something matters; knowing when it leaves is a
        nicety. A date format we cannot read should cost us the second, not the
        first, and certainly not the whole search."""
        leaving = offer(available_to="sometime next August")
        client = build_client(RecordingCall([media_entry(offers=[leaving])]), clock)

        [entry] = client.search("Inception")

        assert entry.offers[0].available_to is None
        assert entry.offers[0].provider == "nfx"


class TestProviderCatalogue:
    def test_providers_come_back_as_records(self, clock: FakeClock):
        client = build_client(
            RecordingCall([]), clock, providers_fn=RecordingCall([offer_package()])
        )

        [provider] = client.providers()

        assert provider.short_name == "nfx"
        assert provider.technical_name == "netflix"
        assert provider.name == "Netflix"
        assert provider.monetization_types == ("FLATRATE",)
        assert provider.icon_url.endswith("netflix.png")

    def test_the_configured_country_is_used(self, clock: FakeClock):
        catalogue = RecordingCall([offer_package()])
        client = build_client(RecordingCall([]), clock, providers_fn=catalogue)

        client.providers()

        assert catalogue.calls[0]["query"] == "IN"

    def test_a_provider_with_no_short_name_is_dropped(self, clock: FakeClock):
        """The short name is the join key. A provider without one cannot be
        subscribed to, matched against an offer, or usefully shown."""
        client = build_client(
            RecordingCall([]), clock, providers_fn=RecordingCall([offer_package(short_name=None)])
        )

        assert client.providers() == []

    def test_fetching_the_catalogue_waits_its_turn(self, clock: FakeClock):
        """It is a request like any other, and the limit is on the total."""
        client = build_client(
            RecordingCall([media_entry()]),
            clock,
            providers_fn=RecordingCall([offer_package()]),
            min_interval=2.0,
        )

        client.search("Inception")
        searched_at = clock.now
        client.providers()

        assert clock.now - searched_at >= 2.0

    def test_a_transient_failure_is_retried(self, clock: FakeClock):
        catalogue = RecordingCall(network_error(), [offer_package()])
        client = build_client(RecordingCall([]), clock, providers_fn=catalogue)

        assert len(client.providers()) == 1
        assert len(catalogue.calls) == 2


class TestSearchArguments:
    def test_the_configured_country_and_language_are_used(self, clock: FakeClock):
        search = RecordingCall([media_entry()])
        client = build_client(search, clock)

        client.search("Inception")

        assert search.calls[0]["country"] == "IN"
        assert search.calls[0]["language"] == "en"

    def test_more_than_one_candidate_is_requested(self, clock: FakeClock):
        """The matcher needs a runner-up to measure its margin against, so
        asking for a single best result would disable the ambiguity check."""
        search = RecordingCall([media_entry()])
        client = build_client(search, clock)

        client.search("Dune")

        assert search.calls[0]["count"] > 1

    def test_object_types_are_passed_through_when_given(self, clock: FakeClock):
        search = RecordingCall([media_entry()])
        client = build_client(search, clock)

        client.search("Fargo", object_types=["SHOW"])

        assert search.calls[0]["object_types"] == ["SHOW"]


class TestRetries:
    def test_a_timeout_is_retried_and_then_succeeds(self, clock: FakeClock):
        search = RecordingCall(network_error(), [media_entry()])
        client = build_client(search, clock)

        [entry] = client.search("Inception")

        assert entry.node_id == "tm12345"
        assert len(search.calls) == 2

    def test_a_server_error_is_retried(self, clock: FakeClock):
        search = RecordingCall(http_error(503), [media_entry()])
        client = build_client(search, clock)

        assert len(client.search("Inception")) == 1
        assert len(search.calls) == 2

    def test_rate_limiting_is_retried(self, clock: FakeClock):
        """429 means "slow down", not "stop" -- and the backoff is the point."""
        search = RecordingCall(http_error(429), [media_entry()])
        client = build_client(search, clock)

        assert len(client.search("Inception")) == 1
        assert len(search.calls) == 2

    def test_giving_up_raises_the_last_error(self, clock: FakeClock):
        search = RecordingCall(*[network_error()] * MAX_ATTEMPTS)
        client = build_client(search, clock)

        with pytest.raises(JustWatchHttpError):
            client.search("Inception")

        assert len(search.calls) == MAX_ATTEMPTS

    def test_backoff_grows_between_attempts(self, clock: FakeClock):
        """Hammering an unofficial API that is already struggling is how a
        client gets blocked, so each wait is longer than the last."""
        search = RecordingCall(*[network_error()] * MAX_ATTEMPTS)
        client = build_client(search, clock)

        with pytest.raises(JustWatchHttpError):
            client.search("Inception")

        backoffs = [slept for slept in clock.slept if slept >= BACKOFF_BASE_SECONDS]
        assert backoffs == sorted(backoffs)
        assert backoffs[-1] > backoffs[0]


class TestDoesNotRetryWhatWillNotChange:
    @pytest.mark.parametrize("status", [400, 403, 404, 422])
    def test_a_client_error_is_not_retried(self, clock: FakeClock, status: int):
        """A malformed query fails identically however many times it is sent.

        Retrying it wastes the user's time and spends someone else's request
        budget on a request that cannot succeed.
        """
        search = RecordingCall(http_error(status), [media_entry()])
        client = build_client(search, clock)

        with pytest.raises(JustWatchHttpError):
            client.search("Inception")

        assert len(search.calls) == 1

    def test_an_api_level_error_is_not_retried(self, clock: FakeClock):
        """The GraphQL layer answered; it just refused. Asking again is futile."""
        error = JustWatchApiError([{"message": "invalid country", "code": "BAD"}])
        search = RecordingCall(error, [media_entry()])
        client = build_client(search, clock)

        with pytest.raises(JustWatchApiError):
            client.search("Inception")

        assert len(search.calls) == 1


class TestRateLimiting:
    def test_consecutive_requests_are_spaced_apart(self, clock: FakeClock):
        search = RecordingCall([media_entry()], [media_entry()])
        client = build_client(search, clock, min_interval=2.0)

        client.search("Inception")
        first_call_at = clock.now
        client.search("Interstellar")

        assert clock.now - first_call_at >= 2.0

    def test_a_slow_caller_is_not_delayed(self, clock: FakeClock):
        """The limiter enforces a floor on the gap, not a fixed cadence. Time
        already spent waiting on the network counts towards it."""
        search = RecordingCall([media_entry()], [media_entry()])
        client = build_client(search, clock, min_interval=2.0)

        client.search("Inception")
        clock.advance(5.0)
        clock.slept.clear()
        client.search("Interstellar")

        assert clock.slept == []

    def test_the_first_request_is_not_delayed(self, clock: FakeClock):
        search = RecordingCall([media_entry()])
        client = build_client(search, clock, min_interval=2.0)

        client.search("Inception")

        assert clock.slept == []

    def test_a_retry_also_waits_its_turn(self, clock: FakeClock):
        """A failure is not a licence to send the next request immediately."""
        search = RecordingCall(network_error(), [media_entry()])
        client = build_client(search, clock, min_interval=2.0)

        client.search("Inception")

        assert sum(clock.slept) >= 2.0

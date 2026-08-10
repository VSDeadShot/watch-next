"""Tests for the availability cache.

Availability is the one thing this app cannot afford to be wrong about, and it
is also the thing that goes out of date on its own: a title leaves Netflix and
nothing tells us. So the cache is about two questions -- what did JustWatch say,
and how long ago did it say it.

Nothing here touches the network.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.availability import Monetization
from app.models import Offer, Title
from app.services.justwatch_client import CatalogueEntry, OfferEntry
from app.services.offers import (
    DEFAULT_REFRESH_LIMIT,
    cached_offers,
    refresh_stale_offers,
    store_offers,
    titles_needing_refresh,
)

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def entry(provider: str, monetization: str = Monetization.FLATRATE, **extra) -> OfferEntry:
    return OfferEntry(provider=provider, monetization=monetization, **extra)


@pytest.fixture
def title(session: Session) -> Title:
    row = Title(jw_node_id="tm1", object_type="MOVIE", title="Inception")
    session.add(row)
    session.flush()
    return row


class TestStoringWhatJustWatchSaid:
    def test_an_offer_is_kept_with_everything_worth_showing(self, session: Session, title: Title):
        store_offers(
            session,
            title,
            [
                entry(
                    "nfx",
                    presentation="HD",
                    url="https://netflix.com/x",
                    price_string="₹149",
                    price_value=149.0,
                    price_currency="INR",
                )
            ],
            country="IN",
            now=NOW,
        )

        stored = session.scalars(select(Offer)).one()
        assert stored.provider_short_name == "nfx"
        assert stored.monetization_type == Monetization.FLATRATE
        assert stored.presentation_type == "HD"
        assert stored.url == "https://netflix.com/x"
        assert stored.price_string == "₹149"
        assert stored.price_value == 149.0
        assert stored.price_currency == "INR"

    def test_the_country_is_recorded_on_every_row(self, session: Session, title: Title):
        """An offer without a country is not an answer to anything: the same
        title is on different services in different places."""
        store_offers(session, title, [entry("nfx")], country="IN", now=NOW)

        assert session.scalars(select(Offer)).one().country == "IN"

    def test_when_it_was_asked_is_recorded_on_the_title(self, session: Session, title: Title):
        store_offers(session, title, [entry("nfx")], country="IN", now=NOW)

        assert title.offers_fetched_at == NOW

    def test_a_title_available_nowhere_still_records_that_we_asked(
        self, session: Session, title: Title
    ):
        """The case that makes this a column on the title rather than something
        inferred from the rows. No offers means no rows, so without this,
        "we asked and it is streaming nowhere" is indistinguishable from "we
        have never asked" -- and it would be re-fetched for ever.
        """
        store_offers(session, title, [], country="IN", now=NOW)

        assert session.scalars(select(Offer)).all() == []
        assert title.offers_fetched_at == NOW

    def test_an_expiry_is_kept(self, session: Session, title: Title):
        leaving = NOW + timedelta(days=9)
        store_offers(session, title, [entry("nfx", available_to=leaving)], country="IN", now=NOW)

        assert session.scalars(select(Offer)).one().available_to == leaving

    def test_two_qualities_from_one_provider_are_two_rows(self, session: Session, title: Title):
        """They are genuinely different offers, and the unique key says so. It
        is the recommendation that collapses them, not the cache."""
        store_offers(
            session,
            title,
            [entry("nfx", presentation="HD"), entry("nfx", presentation="_4K")],
            country="IN",
            now=NOW,
        )

        assert len(session.scalars(select(Offer)).all()) == 2

    def test_a_duplicate_in_one_response_does_not_break_the_write(
        self, session: Session, title: Title
    ):
        """JustWatch is under no obligation to deduplicate for us, and a unique
        constraint violation here would abandon a whole resolve pass."""
        store_offers(
            session,
            title,
            [entry("nfx", presentation="HD"), entry("nfx", presentation="HD")],
            country="IN",
            now=NOW,
        )

        assert len(session.scalars(select(Offer)).all()) == 1


class TestRefreshingReplacesRatherThanAdds:
    def test_an_offer_that_disappeared_is_removed(self, session: Session, title: Title):
        """The failure this guards against is the important direction. A title
        that left Netflix but whose stale row survives would be recommended as
        available, which is the exact promise the app makes and breaks."""
        store_offers(session, title, [entry("nfx"), entry("prv")], country="IN", now=NOW)

        store_offers(session, title, [entry("prv")], country="IN", now=NOW)

        providers = {offer.provider_short_name for offer in session.scalars(select(Offer))}
        assert providers == {"prv"}

    def test_another_country_is_left_alone(self, session: Session, title: Title):
        """Refreshing India must not wipe what we know about the same title in
        the United States."""
        store_offers(session, title, [entry("nfx")], country="US", now=NOW)

        store_offers(session, title, [entry("prv")], country="IN", now=NOW)

        countries = {offer.country for offer in session.scalars(select(Offer))}
        assert countries == {"US", "IN"}

    def test_another_title_is_left_alone(self, session: Session, title: Title):
        other = Title(jw_node_id="tm2", object_type="MOVIE", title="Interstellar")
        session.add(other)
        session.flush()
        store_offers(session, other, [entry("nfx")], country="IN", now=NOW)

        store_offers(session, title, [entry("prv")], country="IN", now=NOW)

        assert len(session.scalars(select(Offer)).all()) == 2


class TestReadingItBack:
    def test_cached_offers_come_back_in_the_pure_shape(self, session: Session, title: Title):
        """The availability rule is pure and knows nothing about SQLAlchemy, so
        the cache hands it plain records rather than mapped rows."""
        store_offers(session, title, [entry("nfx", presentation="HD")], country="IN", now=NOW)

        [offer] = cached_offers(session, title.id, country="IN")

        assert offer.provider == "nfx"
        assert offer.monetization == Monetization.FLATRATE
        assert offer.presentation == "HD"

    def test_only_the_country_asked_for_comes_back(self, session: Session, title: Title):
        store_offers(session, title, [entry("nfx")], country="IN", now=NOW)
        store_offers(session, title, [entry("prv")], country="US", now=NOW)

        [offer] = cached_offers(session, title.id, country="US")

        assert offer.provider == "prv"

    def test_a_title_with_nothing_cached_is_an_empty_list(self, session: Session, title: Title):
        assert cached_offers(session, title.id, country="IN") == []


class TestKnowingWhatHasGoneStale:
    def _title(self, session: Session, node: str, fetched_at: datetime | None) -> Title:
        row = Title(jw_node_id=node, object_type="MOVIE", title=node, offers_fetched_at=fetched_at)
        session.add(row)
        session.flush()
        return row

    def test_a_title_never_asked_about_needs_refreshing(self, session: Session):
        self._title(session, "tm1", None)

        stale = titles_needing_refresh(session, now=NOW, ttl=timedelta(days=7))

        assert [title.jw_node_id for title in stale] == ["tm1"]

    def test_a_recently_fetched_title_does_not(self, session: Session):
        self._title(session, "tm1", NOW - timedelta(days=1))

        assert titles_needing_refresh(session, now=NOW, ttl=timedelta(days=7)) == []

    def test_an_old_title_does(self, session: Session):
        self._title(session, "tm1", NOW - timedelta(days=30))

        assert len(titles_needing_refresh(session, now=NOW, ttl=timedelta(days=7))) == 1

    def test_the_oldest_are_offered_first(self, session: Session):
        """A refresh is a budget of requests. Spending it on the answers most
        likely to have changed is the only sensible order."""
        self._title(session, "recent", NOW - timedelta(days=8))
        self._title(session, "ancient", NOW - timedelta(days=90))
        self._title(session, "never", None)

        stale = titles_needing_refresh(session, now=NOW, ttl=timedelta(days=7))

        assert [title.jw_node_id for title in stale] == ["never", "ancient", "recent"]

    def test_the_number_asked_for_is_a_ceiling(self, session: Session):
        """Each one costs a request against an unofficial API, so a refresh has
        to be able to say how much it is willing to spend."""
        for index in range(5):
            self._title(session, f"tm{index}", None)

        stale = titles_needing_refresh(session, now=NOW, ttl=timedelta(days=7), limit=2)

        assert len(stale) == 2


class FakeLookup:
    """Answers a by-id lookup with scripted offers."""

    def __init__(self, entries: dict, country: str = "IN"):
        self.country = country
        self.entries = entries
        self.looked_up: list[str] = []

    def details(self, node_id: str) -> CatalogueEntry:
        self.looked_up.append(node_id)
        outcome = self.entries[node_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def catalogue_entry(node_id: str, offers: tuple[OfferEntry, ...] = ()) -> CatalogueEntry:
    return CatalogueEntry(node_id=node_id, title=node_id, object_type="MOVIE", offers=offers)


class TestRefreshingStaleTitles:
    def _stale(self, session: Session, node: str) -> Title:
        row = Title(jw_node_id=node, object_type="MOVIE", title=node, offers_fetched_at=None)
        session.add(row)
        session.flush()
        return row

    def test_it_fetches_and_stores(self, session: Session):
        title = self._stale(session, "tm1")
        lookup = FakeLookup({"tm1": catalogue_entry("tm1", (entry("nfx"),))})

        summary = refresh_stale_offers(session, lookup, now=NOW, ttl=timedelta(days=7))

        assert summary.refreshed == 1
        assert [offer.provider for offer in cached_offers(session, title.id, country="IN")] == [
            "nfx"
        ]

    def test_a_refreshed_title_is_not_refreshed_again(self, session: Session):
        self._stale(session, "tm1")
        lookup = FakeLookup({"tm1": catalogue_entry("tm1", (entry("nfx"),))})
        refresh_stale_offers(session, lookup, now=NOW, ttl=timedelta(days=7))

        summary = refresh_stale_offers(session, lookup, now=NOW, ttl=timedelta(days=7))

        assert summary.refreshed == 0
        assert lookup.looked_up == ["tm1"]

    def test_one_failure_does_not_abandon_the_rest(self, session: Session):
        """Same contract as resolution: a refresh walks the whole catalogue, and
        losing all of it to one dropped request would mean starting over."""
        from simplejustwatchapi.exceptions import JustWatchHttpError

        self._stale(session, "tm1")
        self._stale(session, "tm2")
        lookup = FakeLookup(
            {
                "tm1": JustWatchHttpError("timed out"),
                "tm2": catalogue_entry("tm2", (entry("nfx"),)),
            }
        )

        summary = refresh_stale_offers(session, lookup, now=NOW, ttl=timedelta(days=7))

        assert summary.refreshed == 1
        assert summary.failed == 1

    def test_a_failed_title_is_not_marked_as_fetched(self, session: Session):
        """Otherwise a network blip buys a title another week of being treated
        as freshly known when nothing was learned about it at all."""
        from simplejustwatchapi.exceptions import JustWatchHttpError

        title = self._stale(session, "tm1")
        lookup = FakeLookup({"tm1": JustWatchHttpError("timed out")})

        refresh_stale_offers(session, lookup, now=NOW, ttl=timedelta(days=7))

        session.refresh(title)
        assert title.offers_fetched_at is None


class TestHowMuchIsLeft:
    """`remaining` is what makes a refresh drivable in batches.

    The pass paces itself at a request a second, so a catalogue of any size is
    minutes inside one HTTP call. Splitting it needs a number that says whether
    to ask again -- the same argument, and the same trap, as resolution.
    """

    def _stale(self, session: Session, node: str) -> Title:
        row = Title(jw_node_id=node, object_type="MOVIE", title=node, offers_fetched_at=None)
        session.add(row)
        session.flush()
        return row

    def test_it_counts_what_a_limited_pass_left_behind(self, session: Session):
        for node in ("tm1", "tm2", "tm3"):
            self._stale(session, node)
        lookup = FakeLookup({node: catalogue_entry(node) for node in ("tm1", "tm2", "tm3")})

        summary = refresh_stale_offers(session, lookup, now=NOW, ttl=timedelta(days=7), limit=1)

        assert summary.refreshed == 1
        assert summary.remaining == 2

    def test_it_reaches_zero_when_nothing_is_stale(self, session: Session):
        self._stale(session, "tm1")
        lookup = FakeLookup({"tm1": catalogue_entry("tm1")})

        summary = refresh_stale_offers(session, lookup, now=NOW, ttl=timedelta(days=7))

        assert summary.refreshed == 1
        assert summary.remaining == 0

    def test_a_failed_title_is_still_counted_as_remaining(self, session: Session):
        """Nothing was learned about it, so it is still work to do. This is also
        why `remaining` alone cannot end a run -- a caller that ignored `failed`
        would loop here for as long as the API stayed down."""
        from simplejustwatchapi.exceptions import JustWatchHttpError

        self._stale(session, "tm1")
        lookup = FakeLookup({"tm1": JustWatchHttpError("timed out")})

        summary = refresh_stale_offers(session, lookup, now=NOW, ttl=timedelta(days=7))

        assert summary.refreshed == 0
        assert summary.failed == 1
        assert summary.remaining == 1

    def test_it_counts_past_the_size_of_one_batch(self, session: Session):
        """`remaining` is what is left, not what the next pass would take.

        Counting it with the batch limit would cap it at `DEFAULT_REFRESH_LIMIT`
        and a caller would watch a progress bar sit still while work carried on.
        Found by a mutation probe that survived every other test here, all of
        which use catalogues far smaller than one batch.
        """
        nodes = [f"tm{n}" for n in range(DEFAULT_REFRESH_LIMIT + 5)]
        for node in nodes:
            self._stale(session, node)
        lookup = FakeLookup({node: catalogue_entry(node) for node in nodes})

        summary = refresh_stale_offers(session, lookup, now=NOW, ttl=timedelta(days=7), limit=1)

        assert summary.refreshed == 1
        assert summary.remaining == DEFAULT_REFRESH_LIMIT + 4

    def test_a_second_batch_carries_on_from_the_first(self, session: Session):
        for node in ("tm1", "tm2", "tm3"):
            self._stale(session, node)
        lookup = FakeLookup({node: catalogue_entry(node) for node in ("tm1", "tm2", "tm3")})

        refresh_stale_offers(session, lookup, now=NOW, ttl=timedelta(days=7), limit=2)
        summary = refresh_stale_offers(session, lookup, now=NOW, ttl=timedelta(days=7), limit=2)

        assert summary.refreshed == 1
        assert summary.remaining == 0
        assert sorted(lookup.looked_up) == ["tm1", "tm2", "tm3"]

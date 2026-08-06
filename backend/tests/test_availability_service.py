"""Tests for turning cached offers into somewhere a person can press play.

The rule about what counts as watchable is pure and tested in
``test_availability.py``. What is tested here is everything that rule cannot
know on its own: which offers belong to this country, which services the user
actually pays for, what those services are called in words, and how many
queries it takes to answer all of that for a whole list at once.
"""

import pytest
from sqlalchemy.orm import Session

from app.core.availability import Offer as OfferRecord
from app.models import Title
from app.services.availability import WatchOn, watch_on, watch_on_for
from tests.conftest import COUNTRY


@pytest.fixture
def titles(session: Session):
    counter = iter(range(1_000_000))

    def add(name: str = "Arrival") -> Title:
        title = Title(
            jw_node_id=f"tm{next(counter)}",
            object_type="MOVIE",
            title=name,
        )
        session.add(title)
        session.flush()
        return title

    return add


class TestNamingTheService:
    def test_uses_the_name_the_catalogue_gives_it(self, session, providers):
        providers("nfx", "Netflix")

        options = watch_on(
            session,
            [OfferRecord(provider="nfx", monetization="FLATRATE")],
            {"nfx"},
            country=COUNTRY,
        )

        assert [option.name for option in options] == ["Netflix"]

    def test_falls_back_to_the_short_name_when_the_catalogue_has_not_heard_of_it(
        self, session, providers
    ):
        """The catalogue refreshes on its own schedule and can be behind. "Watch
        it on jhs" is a poor label but a true one, which beats refusing to say."""
        options = watch_on(
            session, [OfferRecord(provider="jhs", monetization="FREE")], set(), country=COUNTRY
        )

        assert [option.name for option in options] == ["jhs"]

    def test_a_name_from_another_country_is_not_borrowed(self, session, providers):
        """The same short name is a different service in another market, and a
        label taken from the wrong one would be confidently wrong."""
        providers("hst", "Hotstar Elsewhere", country="US")

        options = watch_on(
            session,
            [OfferRecord(provider="hst", monetization="FLATRATE")],
            {"hst"},
            country=COUNTRY,
        )

        assert [option.name for option in options] == ["hst"]

    def test_says_when_something_is_free_to_everybody(self, session, providers):
        providers("yt", "YouTube")

        options = watch_on(
            session, [OfferRecord(provider="yt", monetization="ADS")], set(), country=COUNTRY
        )

        assert options == (
            WatchOn(
                provider="yt",
                name="YouTube",
                monetization="ADS",
                url=None,
                requires_subscription=False,
            ),
        )

    def test_asks_the_catalogue_nothing_when_there_is_nowhere_to_watch(self, session, counting):
        with counting() as statements:
            assert watch_on(session, [], set(), country=COUNTRY) == ()

        assert statements == []


class TestAWholeList:
    def test_answers_for_every_title_asked_about(
        self, session, titles, offers, providers, subscribes
    ):
        """Including the ones streaming nowhere. "Nowhere" is a real answer the
        caller draws, so the map is total and no caller has to guess whether a
        missing key means unwatchable or unasked."""
        providers("nfx", "Netflix")
        subscribes("nfx")
        somewhere = titles("Arrival")
        nowhere = titles("Paddington")
        offers(somewhere, "nfx")

        found = watch_on_for(session, [somewhere.id, nowhere.id], country=COUNTRY)

        assert set(found) == {somewhere.id, nowhere.id}
        assert [option.name for option in found[somewhere.id]] == ["Netflix"]
        assert found[nowhere.id] == ()

    def test_only_what_the_user_pays_for(self, session, titles, offers, providers, subscribes):
        providers("nfx", "Netflix")
        providers("prv", "Prime Video")
        subscribes("nfx")
        film = titles("Arrival")
        offers(film, "nfx")
        offers(film, "prv")

        found = watch_on_for(session, [film.id], country=COUNTRY)

        assert [option.provider for option in found[film.id]] == ["nfx"]

    def test_free_counts_wherever_it_is(self, session, titles, offers, providers, subscribes):
        """Free to everybody is watchable on a service nobody subscribes to --
        the product decision that "available" means at no additional cost."""
        subscribes("nfx")
        film = titles("Arrival")
        offers(film, "yt", "FREE")

        found = watch_on_for(session, [film.id], country=COUNTRY)

        assert [option.provider for option in found[film.id]] == ["yt"]
        assert found[film.id][0].requires_subscription is False

    def test_renting_is_not_watching(self, session, titles, offers, subscribes):
        subscribes("prv")
        film = titles("Arrival")
        offers(film, "prv", "RENT")

        assert watch_on_for(session, [film.id], country=COUNTRY)[film.id] == ()

    def test_offers_from_another_country_are_not_counted(self, session, titles, offers, subscribes):
        subscribes("nfx")
        film = titles("Arrival")
        offers(film, "nfx", country="US")

        assert watch_on_for(session, [film.id], country=COUNTRY)[film.id] == ()

    def test_another_person_s_subscriptions_do_not_apply(self, session, titles, offers, subscribes):
        subscribes("nfx", user_id="someone-else")
        film = titles("Arrival")
        offers(film, "nfx")

        assert watch_on_for(session, [film.id], country=COUNTRY)[film.id] == ()

    def test_and_do_apply_when_it_is_them_asking(self, session, titles, offers, subscribes):
        """The other half of the sentence above. Without it, an answer that
        ignored who was asking and always read the default user's services
        would look exactly the same from every test here."""
        subscribes("nfx", user_id="someone-else")
        film = titles("Arrival")
        offers(film, "nfx")

        found = watch_on_for(session, [film.id], country=COUNTRY, user_id="someone-else")

        assert [option.provider for option in found[film.id]] == ["nfx"]

    def test_what_you_pay_for_comes_first(self, session, titles, offers, providers, subscribes):
        subscribes("nfx")
        film = titles("Arrival")
        offers(film, "yt", "ADS")
        offers(film, "nfx", "FLATRATE")

        found = watch_on_for(session, [film.id], country=COUNTRY)

        assert [option.provider for option in found[film.id]] == ["nfx", "yt"]

    def test_nothing_asked_about_asks_the_database_nothing(self, session, counting):
        with counting() as statements:
            assert watch_on_for(session, [], country=COUNTRY) == {}

        assert statements == []

    def test_a_longer_list_is_not_more_queries(
        self, session, counting, titles, offers, providers, subscribes
    ):
        """The reason this function takes a list at all. A watchlist page is a
        list of rows, and a query per row is a page that gets slower the more
        somebody uses it."""
        providers("nfx", "Netflix")
        subscribes("nfx")
        films = [titles(f"Film {n}") for n in range(30)]
        for film in films:
            offers(film, "nfx")

        with counting() as one:
            watch_on_for(session, [films[0].id], country=COUNTRY)

        with counting() as thirty:
            watch_on_for(session, [film.id for film in films], country=COUNTRY)

        assert len(thirty) == len(one)

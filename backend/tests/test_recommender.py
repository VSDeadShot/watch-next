"""Tests for the one thing this app exists to do.

Everything else is machinery. This is the promise: one title, that the person
asking can actually press play on tonight, with a reason attached.

The hard filter is the whole product. Recommending something that turns out to
be on a service somebody does not pay for, or that left it last month, is the
single failure that makes the app worthless -- so a good half of these are about
what must *never* come back, rather than about what should.

No network: the catalogue is a fake that records what it was asked for.
"""

from datetime import UTC, datetime, timedelta

import pytest
from simplejustwatchapi.exceptions import JustWatchApiError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.moods import Mood
from app.core.scoring import KindPreference, RecommendationRequest
from app.models import DEFAULT_USER_ID, Provider, Recommendation, Title
from app.services.justwatch_client import CatalogueEntry, OfferEntry
from app.services.providers import set_subscriptions
from app.services.recommender import DEFAULT_REPEAT_COOLDOWN, recommend
from app.services.titles import store_title

NOW = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)

NETFLIX = "nfx"
PRIME = "prv"
HOTSTAR = "jhs"


def entry(node_id: str, **overrides) -> CatalogueEntry:
    values = {
        "title": f"Title {node_id}",
        "object_type": "MOVIE",
        "release_year": 2021,
        "runtime_minutes": 100,
        "genres": ("cmy",),
        "imdb_score": 7.5,
        "offers": (OfferEntry(provider=NETFLIX, monetization="FLATRATE"),),
    }
    return CatalogueEntry(node_id=node_id, **{**values, **overrides})


class FakeCatalogue:
    """A popularity listing that answers from a script."""

    country = "IN"

    def __init__(self, *pages):
        self.pages = list(pages) or [[]]
        self.calls: list[dict] = []

    def popular(self, *, providers=None, object_types=None, count=50, offset=0):
        self.calls.append({"providers": providers, "offset": offset})
        outcome = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        if isinstance(outcome, Exception):
            raise outcome
        return list(outcome)


@pytest.fixture
def subscribed(session: Session):
    """Somebody with Netflix, which is what makes a Netflix offer watchable."""
    session.add(
        Provider(country="IN", short_name=NETFLIX, technical_name="netflix", name="Netflix")
    )
    session.add(
        Provider(country="IN", short_name=PRIME, technical_name="amazonprime", name="Prime Video")
    )
    session.add(
        Provider(country="IN", short_name=HOTSTAR, technical_name="hotstar", name="JioHotstar")
    )
    session.flush()
    set_subscriptions(session, [NETFLIX], country="IN")
    return session


def pool(session: Session, *entries: CatalogueEntry) -> list[Title]:
    """Put titles in the pool directly, as a discovery pass would have."""
    stored = [store_title(session, item, country="IN", now=NOW)[0] for item in entries]
    session.flush()
    return stored


def ask(session: Session, catalogue=None, **kwargs):
    request = kwargs.pop("request", RecommendationRequest())
    return recommend(
        session,
        catalogue if catalogue is not None else FakeCatalogue([]),
        request,
        now=kwargs.pop("now", NOW),
        **kwargs,
    )


class TestOneThing:
    def test_returns_a_single_title(self, subscribed: Session):
        pool(subscribed, entry("tm1"), entry("tm2"), entry("tm3"))

        result = ask(subscribed)

        assert result.pick is not None
        assert result.pick.title.jw_node_id in {"tm1", "tm2", "tm3"}

    def test_the_same_question_gets_the_same_answer(self, subscribed: Session):
        """A recommender that changes its mind between two identical requests
        looks broken rather than clever."""
        pool(subscribed, entry("tm1", imdb_score=8.0), entry("tm2", imdb_score=8.0))

        first = ask(subscribed)
        second = ask(subscribed, exclude_ids=())

        assert first.pick.title.id == second.pick.title.id

    def test_it_says_why(self, subscribed: Session):
        pool(subscribed, entry("tm1", imdb_score=8.6))

        result = ask(subscribed)

        assert result.pick.reasons

    def test_it_says_where(self, subscribed: Session):
        pool(subscribed, entry("tm1"))

        result = ask(subscribed)

        [option] = result.pick.watch_on
        assert option.provider == NETFLIX
        assert option.name == "Netflix"
        assert option.requires_subscription

    def test_where_it_can_be_watched_carries_the_link(self, subscribed: Session):
        watch_here = OfferEntry(
            provider=NETFLIX, monetization="FLATRATE", url="https://netflix.com/title/1"
        )
        pool(subscribed, entry("tm1", offers=(watch_here,)))

        result = ask(subscribed)

        assert result.pick.watch_on[0].url == "https://netflix.com/title/1"

    def test_a_service_missing_from_the_catalogue_still_gets_named(self, subscribed: Session):
        """Somebody has to be told where to watch it even if the provider table
        is out of date, and a short name is a poor label but a real one."""
        free_somewhere = OfferEntry(provider="zzz", monetization="FREE")
        pool(subscribed, entry("tm1", offers=(free_somewhere,)))

        result = ask(subscribed)

        assert result.pick.watch_on[0].name == "zzz"


class TestWhatMustNeverComeBack:
    def test_never_something_on_a_service_you_do_not_have(self, subscribed: Session):
        """The entire product. Everything else here is in service of this."""
        on_prime = OfferEntry(provider=PRIME, monetization="FLATRATE")
        pool(subscribed, entry("tm1", offers=(on_prime,)))

        result = ask(subscribed)

        assert result.pick is None

    def test_never_something_streaming_nowhere_at_all(self, subscribed: Session):
        pool(subscribed, entry("tm1", offers=()))

        assert ask(subscribed).pick is None

    def test_never_something_you_can_only_rent(self, subscribed: Session):
        """ "You could pay for this" is not "you can watch this"."""
        rental = OfferEntry(provider=NETFLIX, monetization="RENT")
        pool(subscribed, entry("tm1", offers=(rental,)))

        assert ask(subscribed).pick is None

    def test_never_something_already_watched(self, subscribed: Session, watched):
        titles = pool(subscribed, entry("tm1"))
        event = watched("Title tm1")
        event.title_id = titles[0].id
        subscribed.flush()

        assert ask(subscribed).pick is None

    def test_never_something_ruled_out_by_the_caller(self, subscribed: Session):
        """What "not this one" does. The re-roll has to be able to move on."""
        titles = pool(subscribed, entry("tm1"))

        assert ask(subscribed, exclude_ids=(titles[0].id,)).pick is None

    def test_never_the_same_thing_two_evenings_running(self, subscribed: Session):
        pool(subscribed, entry("tm1"))

        first = ask(subscribed)
        again = ask(subscribed, now=NOW + timedelta(days=1))

        assert first.pick is not None
        assert again.pick is None

    def test_but_it_comes_back_round_eventually(self, subscribed: Session):
        pool(subscribed, entry("tm1"))
        ask(subscribed)

        later = ask(subscribed, now=NOW + DEFAULT_REPEAT_COOLDOWN + timedelta(days=1))

        assert later.pick is not None
        # And suggesting it a second time is a second recommendation, months
        # after the first: the receipt is a log, not a set of titles ever shown.
        assert len(subscribed.scalars(select(Recommendation)).all()) == 2


class TestFreeCountsAsWatchable:
    def test_free_on_a_service_you_do_not_pay_for_is_offered(self, subscribed: Session):
        """A deliberate product decision: free is free, and refusing to mention
        something somebody could watch right now at no cost would be a strange
        way to be careful."""
        free_elsewhere = OfferEntry(provider=HOTSTAR, monetization="FREE")
        pool(subscribed, entry("tm1", offers=(free_elsewhere,)))

        result = ask(subscribed)

        assert result.pick is not None
        assert not result.pick.watch_on[0].requires_subscription

    def test_free_with_ads_counts_too(self, subscribed: Session):
        with_ads = OfferEntry(provider=HOTSTAR, monetization="ADS")
        pool(subscribed, entry("tm1", offers=(with_ads,)))

        assert ask(subscribed).pick is not None

    def test_somebody_with_no_subscriptions_can_still_be_told_something(self, session: Session):
        free_anywhere = OfferEntry(provider=HOTSTAR, monetization="FREE")
        pool(session, entry("tm1", offers=(free_anywhere,)))

        assert ask(session).pick is not None


class TestWhatWasAskedFor:
    def test_a_film_when_a_film_was_asked_for(self, subscribed: Session):
        pool(subscribed, entry("tm1", object_type="SHOW"), entry("tm2", object_type="MOVIE"))

        result = ask(subscribed, request=RecommendationRequest(kind=KindPreference.MOVIE))

        assert result.pick.title.object_type == "MOVIE"

    def test_nothing_longer_than_the_evening(self, subscribed: Session):
        pool(subscribed, entry("tm1", runtime_minutes=180))

        result = ask(subscribed, request=RecommendationRequest(minutes_available=45))

        assert result.pick is None

    def test_what_somebody_actually_watches_steers_the_choice(self, subscribed: Session, watched):
        """With no mood asked for, the history is what is left to go on -- so a
        library full of comedy has to be able to pick the comedy out of two
        titles that are otherwise identical."""
        history = pool(subscribed, *(entry(f"seen{index}", genres=("cmy",)) for index in range(6)))
        for title in history:
            event = watched(title.title)
            event.title_id = title.id
        subscribed.flush()
        pool(subscribed, entry("tm1", genres=("doc",)), entry("tm2", genres=("cmy",)))

        result = ask(subscribed)

        assert result.pick.title.jw_node_id == "tm2"

    def test_the_mood_steers_the_choice(self, subscribed: Session):
        pool(subscribed, entry("tm1", genres=("cmy",)), entry("tm2", genres=("trl",)))

        result = ask(subscribed, request=RecommendationRequest(mood=Mood.THRILL))

        assert result.pick.title.jw_node_id == "tm2"


class TestSayingWhyThereIsNothing:
    def test_an_empty_pool_says_so(self, subscribed: Session):
        result = ask(subscribed)

        assert result.pick is None
        assert "nothing" in result.reason.lower()

    def test_nothing_on_your_services_says_that_instead(self, subscribed: Session):
        """A different problem with a different fix, so it gets a different
        sentence: this one is solved on the settings page."""
        on_prime = OfferEntry(provider=PRIME, monetization="FLATRATE")
        pool(subscribed, entry("tm1", offers=(on_prime,)))

        result = ask(subscribed)

        assert "subscri" in result.reason.lower() or "service" in result.reason.lower()

    def test_nothing_that_fits_the_evening_says_that(self, subscribed: Session):
        pool(subscribed, entry("tm1", runtime_minutes=180))

        result = ask(subscribed, request=RecommendationRequest(minutes_available=45))

        assert "45" in result.reason

    def test_having_no_subscriptions_at_all_is_called_out(self, session: Session):
        """The commonest first-run failure by a distance, and the one a generic
        "nothing found" would leave somebody hunting for."""
        on_netflix = OfferEntry(provider=NETFLIX, monetization="FLATRATE")
        pool(session, entry("tm1", offers=(on_netflix,)))

        result = ask(session)

        assert "subscri" in result.reason.lower()

    def test_the_counts_show_where_it_collapsed(self, subscribed: Session):
        on_prime = OfferEntry(provider=PRIME, monetization="FLATRATE")
        pool(subscribed, entry("tm1"), entry("tm2", offers=(on_prime,)))

        result = ask(subscribed, request=RecommendationRequest(minutes_available=10))

        assert result.considered.pool == 2
        assert result.considered.available == 1
        assert result.considered.eligible == 0


class TestKeepingThePoolTopped:
    def test_an_empty_pool_is_filled_before_answering(self, subscribed: Session):
        catalogue = FakeCatalogue([entry("tm1")])

        result = ask(subscribed, catalogue)

        assert catalogue.calls
        assert result.pick is not None

    def test_a_fresh_pool_costs_no_requests(self, subscribed: Session):
        """The common case by a distance, and it has to be free."""
        ask(subscribed, FakeCatalogue([entry("tm1")]))
        catalogue = FakeCatalogue([entry("tm2")])

        ask(subscribed, catalogue, now=NOW + timedelta(minutes=5))

        assert catalogue.calls == []

    def test_the_listing_is_filtered_to_what_the_user_pays_for(self, subscribed: Session):
        catalogue = FakeCatalogue([entry("tm1")])

        ask(subscribed, catalogue)

        assert catalogue.calls[0]["providers"] == [NETFLIX]

    def test_justwatch_being_down_still_answers_from_what_we_have(self, subscribed: Session):
        """An outage should cost freshness, not the whole feature."""
        pool(subscribed, entry("tm1"))

        result = ask(subscribed, FakeCatalogue(JustWatchApiError("down")))

        assert result.pick is not None


class TestKeepingAReceipt:
    def test_what_was_recommended_is_recorded(self, subscribed: Session):
        pool(subscribed, entry("tm1"))

        result = ask(subscribed)

        [recorded] = subscribed.scalars(select(Recommendation)).all()
        assert recorded.title_id == result.pick.title.id
        assert recorded.user_id == DEFAULT_USER_ID

    def test_the_question_is_recorded_with_it(self, subscribed: Session):
        """ "Why did it tell me to watch a documentary" has a good answer only if
        we still know they had asked for something to think about."""
        pool(subscribed, entry("tm1", genres=("doc",)))

        ask(
            subscribed,
            request=RecommendationRequest(mood=Mood.THINK, minutes_available=120),
        )

        [recorded] = subscribed.scalars(select(Recommendation)).all()
        assert recorded.mood == Mood.THINK
        assert recorded.minutes_available == 120

    def test_the_working_is_recorded_too(self, subscribed: Session):
        pool(subscribed, entry("tm1", imdb_score=8.8))

        result = ask(subscribed)

        [recorded] = subscribed.scalars(select(Recommendation)).all()
        assert recorded.score == pytest.approx(result.pick.score)
        assert recorded.reasons == list(result.pick.reasons)

    def test_asking_again_in_the_same_sitting_is_not_a_second_recommendation(
        self, subscribed: Session
    ):
        """A refresh is somebody looking again, not the app deciding again."""
        pool(subscribed, entry("tm1"))

        ask(subscribed)
        ask(subscribed, now=NOW + timedelta(minutes=2))

        assert len(subscribed.scalars(select(Recommendation)).all()) == 1

    def test_asking_again_the_next_evening_is(self, subscribed: Session):
        pool(subscribed, entry("tm1"), entry("tm2"))

        ask(subscribed)
        ask(subscribed, now=NOW + timedelta(days=1))

        assert len(subscribed.scalars(select(Recommendation)).all()) == 2

    def test_nothing_is_recorded_when_nothing_was_recommended(self, subscribed: Session):
        result = ask(subscribed)

        assert result.pick is None
        assert subscribed.scalars(select(Recommendation)).all() == []

"""Tests for the availability rule.

This is the rule the whole product rests on. Recommending something the user
cannot actually watch is the single failure that makes the app worthless, so
these are less about edge cases than about pinning the definition of "you can
watch this tonight".

Pure functions over plain records: no database, no network, no offers cache.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.availability import (
    Monetization,
    Offer,
    is_available,
    is_stale,
    watch_options,
)

NETFLIX = "nfx"
PRIME = "prv"
HOTSTAR = "jhs"


def offer(provider: str, monetization: str, **extra) -> Offer:
    return Offer(provider=provider, monetization=monetization, **extra)


class TestWhatCountsAsWatchable:
    def test_a_subscription_you_pay_for_makes_it_watchable(self):
        offers = [offer(NETFLIX, Monetization.FLATRATE)]

        assert is_available(offers, subscriptions={NETFLIX})

    def test_a_subscription_you_do_not_have_does_not(self):
        """The entire point of the app. A title streaming on a service the user
        does not pay for is not something they can watch tonight."""
        offers = [offer(NETFLIX, Monetization.FLATRATE)]

        assert not is_available(offers, subscriptions={PRIME})

    def test_free_is_watchable_on_any_provider(self):
        """Free needs no subscription, so requiring one would hide things the
        user genuinely can watch at no cost."""
        offers = [offer(HOTSTAR, Monetization.FREE)]

        assert is_available(offers, subscriptions=set())

    def test_free_with_ads_is_watchable_on_any_provider(self):
        offers = [offer(HOTSTAR, Monetization.ADS)]

        assert is_available(offers, subscriptions=set())

    @pytest.mark.parametrize("monetization", [Monetization.RENT, Monetization.BUY])
    def test_paying_again_is_not_watchable(self, monetization: str):
        """ "You could buy this" is not "you can watch this". Blurring the two is
        exactly the disappointment the availability filter exists to prevent."""
        offers = [offer(NETFLIX, monetization)]

        assert not is_available(offers, subscriptions={NETFLIX})

    def test_renting_on_a_service_you_subscribe_to_is_still_renting(self):
        offers = [offer(PRIME, Monetization.RENT)]

        assert not is_available(offers, subscriptions={PRIME})

    def test_nothing_on_offer_is_not_available(self):
        assert not is_available([], subscriptions={NETFLIX})

    def test_one_watchable_offer_among_unwatchable_ones_is_enough(self):
        offers = [
            offer(NETFLIX, Monetization.BUY),
            offer(PRIME, Monetization.RENT),
            offer(HOTSTAR, Monetization.ADS),
        ]

        assert is_available(offers, subscriptions=set())

    def test_a_monetization_type_we_have_never_seen_is_not_watchable(self):
        """JustWatch is free to add one, and guessing that an unknown way to pay
        is free would be the wrong way to be wrong."""
        offers = [offer(NETFLIX, "CINEMA")]

        assert not is_available(offers, subscriptions={NETFLIX})


class TestHowToWatchIt:
    """A recommendation has to say where, not just that it exists."""

    def test_it_names_the_provider_and_how(self):
        offers = [offer(NETFLIX, Monetization.FLATRATE, url="https://netflix.com/x")]

        [option] = watch_options(offers, subscriptions={NETFLIX})

        assert option.provider == NETFLIX
        assert option.monetization == Monetization.FLATRATE
        assert option.url == "https://netflix.com/x"

    def test_it_says_whether_a_subscription_is_needed(self):
        offers = [offer(NETFLIX, Monetization.FLATRATE), offer(HOTSTAR, Monetization.ADS)]

        options = {option.provider: option for option in watch_options(offers, {NETFLIX})}

        assert options[NETFLIX].requires_subscription
        assert not options[HOTSTAR].requires_subscription

    def test_unwatchable_offers_are_left_out(self):
        offers = [offer(NETFLIX, Monetization.FLATRATE), offer(PRIME, Monetization.RENT)]

        assert [option.provider for option in watch_options(offers, {NETFLIX})] == [NETFLIX]

    def test_one_provider_offering_several_qualities_is_one_option(self):
        """JustWatch lists HD and 4K separately. Telling someone they can watch
        it on Netflix twice is noise, not information."""
        offers = [
            offer(NETFLIX, Monetization.FLATRATE, presentation="HD"),
            offer(NETFLIX, Monetization.FLATRATE, presentation="_4K"),
        ]

        assert len(watch_options(offers, {NETFLIX})) == 1

    def test_a_subscription_is_offered_before_something_free_elsewhere(self):
        """What the user already pays for wins: it is the thing they will
        actually open, and it has no ads in it."""
        offers = [offer(HOTSTAR, Monetization.ADS), offer(NETFLIX, Monetization.FLATRATE)]

        options = watch_options(offers, subscriptions={NETFLIX})

        assert [option.provider for option in options] == [NETFLIX, HOTSTAR]

    def test_free_is_offered_before_free_with_ads(self):
        offers = [offer(PRIME, Monetization.ADS), offer(HOTSTAR, Monetization.FREE)]

        options = watch_options(offers, subscriptions=set())

        assert [option.provider for option in options] == [HOTSTAR, PRIME]

    def test_the_order_is_stable_when_nothing_separates_two_offers(self):
        """Two runs of the recommender should not disagree about where to watch
        the same thing."""
        offers = [offer(PRIME, Monetization.FREE), offer(HOTSTAR, Monetization.FREE)]

        first = [option.provider for option in watch_options(offers, set())]
        second = [option.provider for option in watch_options(list(reversed(offers)), set())]

        assert first == second

    def test_nothing_watchable_is_an_empty_tuple_not_an_error(self):
        assert watch_options([offer(NETFLIX, Monetization.BUY)], set()) == ()


class TestStaleness:
    """Availability changes, so a cached answer has a shelf life.

    It changes on the order of weeks, not minutes, which is why this is a TTL
    rather than a check on every request against an unofficial API.
    """

    NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)

    def test_a_fresh_answer_is_not_stale(self):
        assert not is_stale(self.NOW - timedelta(days=2), now=self.NOW, ttl=timedelta(days=7))

    def test_an_old_answer_is_stale(self):
        assert is_stale(self.NOW - timedelta(days=8), now=self.NOW, ttl=timedelta(days=7))

    def test_never_fetched_is_stale(self):
        """A title with no cached availability has to be treated as unknown, not
        as unavailable -- otherwise it silently never gets recommended."""
        assert is_stale(None, now=self.NOW, ttl=timedelta(days=7))

    def test_the_boundary_is_not_stale(self):
        assert not is_stale(self.NOW - timedelta(days=7), now=self.NOW, ttl=timedelta(days=7))

    def test_a_timestamp_from_the_future_is_not_stale(self):
        """Clock skew between a server and a database should not cause a storm
        of refetching."""
        assert not is_stale(self.NOW + timedelta(hours=1), now=self.NOW, ttl=timedelta(days=7))


class TestAHostileLinkOnAnOtherwiseGoodOffer:
    """The URL is JustWatch's, and it goes straight into an `href`.

    Dropped here rather than only at ingest, because the cache already holds
    rows written before anything checked them -- and offers live for a week, so
    trusting a refresh to clean them means a week of serving them. See
    `core/urls` for what the check is and, more usefully, what it is not.
    """

    def test_the_offer_still_counts_as_watchable(self):
        """The link is not what makes something watchable. Dropping the offer
        would tell the user they cannot watch a thing they can."""
        offers = [offer(NETFLIX, Monetization.FLATRATE, url="javascript:alert(1)")]

        assert is_available(offers, subscriptions={NETFLIX})

    def test_but_the_link_is_not_handed_out(self):
        offers = [offer(NETFLIX, Monetization.FLATRATE, url="javascript:alert(1)")]

        [option] = watch_options(offers, subscriptions={NETFLIX})

        assert option.provider == NETFLIX
        assert option.url is None

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "data:text/html,x",
            "//evil.test",
            "https://",
        ],
    )
    def test_nothing_unfollowable_survives(self, url: str):
        offers = [offer(NETFLIX, Monetization.FLATRATE, url=url)]

        assert watch_options(offers, subscriptions={NETFLIX})[0].url is None

    def test_an_ordinary_link_is_passed_through_untouched(self):
        """The other half. A check that dropped everything would be noticed
        only as posters and buttons quietly losing their links."""
        offers = [offer(NETFLIX, Monetization.FLATRATE, url="https://netflix.com/title/1")]

        assert (
            watch_options(offers, subscriptions={NETFLIX})[0].url == "https://netflix.com/title/1"
        )

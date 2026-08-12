"""Tests for the provider catalogue and the user's subscriptions.

Two different things live here and the difference matters. The catalogue is
JustWatch's data, refreshed periodically and disposable. The subscriptions are
the user's data, entered by hand, and the availability filter's only input. A
refresh of the first must never be able to damage the second.

Nothing here touches the network.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Provider, UserProvider
from app.services.justwatch_client import ProviderEntry
from app.services.providers import (
    UnknownProvider,
    available_providers,
    refresh_providers,
    set_subscriptions,
    subscriptions,
)

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def listing(short_name: str, name: str | None = None, **extra) -> ProviderEntry:
    return ProviderEntry(
        short_name=short_name,
        technical_name=extra.pop("technical_name", f"{short_name}tech"),
        name=name or short_name.upper(),
        **extra,
    )


class FakeCatalogue:
    """Answers the provider listing with a script, and counts being asked."""

    def __init__(self, entries: list[ProviderEntry], country: str = "IN"):
        self.country = country
        self._entries = entries
        self.calls = 0

    def providers(self) -> list[ProviderEntry]:
        self.calls += 1
        return list(self._entries)


def stored(session: Session, country: str = "IN") -> dict[str, Provider]:
    rows = session.scalars(select(Provider).where(Provider.country == country))
    return {row.short_name: row for row in rows}


class TestRefreshingTheCatalogue:
    def test_a_service_is_stored_with_everything_the_picker_needs(self, session: Session):
        """The settings page renders this list, so a bare short name is not
        enough -- nobody recognises "nfx"."""
        catalogue = FakeCatalogue(
            [
                listing(
                    "nfx",
                    "Netflix",
                    technical_name="netflix",
                    icon_url="https://images.justwatch.com/icon/207360008/s100/netflix.png",
                    monetization_types=("FLATRATE", "BUY"),
                )
            ]
        )

        refresh_providers(session, catalogue, now=NOW)

        row = stored(session)["nfx"]
        assert row.name == "Netflix"
        assert row.technical_name == "netflix"
        assert row.icon_url == "https://images.justwatch.com/icon/207360008/s100/netflix.png"
        assert row.monetization_types == ["FLATRATE", "BUY"]
        assert row.country == "IN"
        assert row.fetched_at == NOW

    def test_the_country_comes_from_the_client_that_asked(self, session: Session):
        """Not from a setting read separately. The list describes whichever
        country the request was made for, and those two must not be able to
        disagree."""
        catalogue = FakeCatalogue([listing("nfx")], country="US")

        refresh_providers(session, catalogue, now=NOW)

        assert stored(session, "US")["nfx"].country == "US"

    def test_refreshing_twice_updates_rather_than_duplicates(self, session: Session):
        """The unique key would reject the second write outright, so this is the
        difference between a refresh working and a refresh raising."""
        catalogue = FakeCatalogue([listing("nfx", "Netflix")])
        refresh_providers(session, catalogue, now=NOW)

        refresh_providers(session, catalogue, now=NOW + timedelta(days=30))

        assert len(session.scalars(select(Provider)).all()) == 1

    def test_a_renamed_service_is_renamed(self, session: Session):
        refresh_providers(session, FakeCatalogue([listing("mxp", "HBO Max")]), now=NOW)

        refresh_providers(session, FakeCatalogue([listing("mxp", "Max")]), now=NOW)

        assert stored(session)["mxp"].name == "Max"

    def test_a_service_that_left_the_catalogue_is_dropped(self, session: Session):
        """A picker offering a service JustWatch no longer lists would let
        someone subscribe to something no offer can ever match."""
        refresh_providers(session, FakeCatalogue([listing("nfx"), listing("gone")]), now=NOW)

        refresh_providers(session, FakeCatalogue([listing("nfx")]), now=NOW)

        assert set(stored(session)) == {"nfx"}

    def test_another_country_is_left_alone(self, session: Session):
        refresh_providers(session, FakeCatalogue([listing("hst")], country="IN"), now=NOW)

        refresh_providers(session, FakeCatalogue([listing("hlu")], country="US"), now=NOW)

        assert set(stored(session, "IN")) == {"hst"}
        assert set(stored(session, "US")) == {"hlu"}

    def test_the_summary_says_what_changed(self, session: Session):
        refresh_providers(session, FakeCatalogue([listing("nfx"), listing("gone")]), now=NOW)

        summary = refresh_providers(
            session, FakeCatalogue([listing("nfx"), listing("new")]), now=NOW
        )

        assert summary.fetched == 2
        assert summary.added == 1
        assert summary.updated == 1
        assert summary.removed == 1

    def test_an_empty_answer_does_not_wipe_what_we_have(self, session: Session):
        """The guard that matters. Deleting on an empty response would empty the
        settings picker over a bad answer, and an empty picker means no
        subscriptions, which means every recommendation is unavailable.
        """
        refresh_providers(session, FakeCatalogue([listing("nfx")]), now=NOW)

        summary = refresh_providers(session, FakeCatalogue([]), now=NOW)

        assert set(stored(session)) == {"nfx"}
        assert summary.fetched == 0
        assert summary.removed == 0


class TestThePicker:
    def test_only_the_country_asked_for_is_offered(self, session: Session):
        refresh_providers(session, FakeCatalogue([listing("hst")], country="IN"), now=NOW)
        refresh_providers(session, FakeCatalogue([listing("hlu")], country="US"), now=NOW)

        assert [row.short_name for row in available_providers(session, country="US")] == ["hlu"]

    def test_they_come_back_in_a_readable_order(self, session: Session):
        """Alphabetical by the name a person sees, not by insertion or id. A
        picker whose order changes between visits is a picker people misread."""
        catalogue = FakeCatalogue(
            [listing("zee", "Zee5"), listing("nfx", "Netflix"), listing("aha", "aha")]
        )
        refresh_providers(session, catalogue, now=NOW)

        assert [row.name for row in available_providers(session, country="IN")] == [
            "aha",
            "Netflix",
            "Zee5",
        ]

    def test_nothing_fetched_yet_is_an_empty_list(self, session: Session):
        assert available_providers(session, country="IN") == []


class TestSubscriptions:
    @pytest.fixture(autouse=True)
    def catalogue(self, session: Session):
        refresh_providers(
            session,
            FakeCatalogue([listing("nfx"), listing("prv"), listing("hst")]),
            now=NOW,
        )

    def test_what_was_set_comes_back(self, session: Session):
        set_subscriptions(session, ["nfx", "prv"], country="IN")

        assert subscriptions(session, country="IN") == ["nfx", "prv"]

    def test_nothing_set_is_an_empty_list(self, session: Session):
        """Not an error, and not "everything". Someone who has told us nothing
        has told us nothing, and the filter has to be able to say so."""
        assert subscriptions(session, country="IN") == []

    def test_setting_replaces_rather_than_adds(self, session: Session):
        set_subscriptions(session, ["nfx", "prv"], country="IN")

        set_subscriptions(session, ["hst"], country="IN")

        assert subscriptions(session, country="IN") == ["hst"]

    def test_cancelling_everything_is_allowed(self, session: Session):
        set_subscriptions(session, ["nfx"], country="IN")

        set_subscriptions(session, [], country="IN")

        assert subscriptions(session, country="IN") == []

    def test_the_same_service_twice_is_stored_once(self, session: Session):
        """The unique key would reject the second row, taking the whole request
        with it, over what is only a sloppy client."""
        set_subscriptions(session, ["nfx", "nfx"], country="IN")

        assert subscriptions(session, country="IN") == ["nfx"]
        assert len(session.scalars(select(UserProvider)).all()) == 1

    def test_a_service_we_have_never_heard_of_is_refused(self, session: Session):
        """A typo stored as a subscription is invisible: it matches no offer, so
        the user is simply never recommended anything on the service they think
        they told us about. Better to refuse it while somebody is watching."""
        with pytest.raises(UnknownProvider):
            set_subscriptions(session, ["nfx", "nope"], country="IN")

    def test_a_refused_request_stores_none_of_it(self, session: Session):
        """All or nothing: half-applied settings are worse than rejected ones,
        because nobody is told which half."""
        set_subscriptions(session, ["hst"], country="IN")

        with pytest.raises(UnknownProvider):
            set_subscriptions(session, ["nfx", "nope"], country="IN")

        assert subscriptions(session, country="IN") == ["hst"]

    def test_a_service_that_exists_elsewhere_is_still_refused_here(self, session: Session):
        """Availability is per country. Subscribing to a service JustWatch does
        not list in your country would never match an offer."""
        refresh_providers(session, FakeCatalogue([listing("hlu")], country="US"), now=NOW)

        with pytest.raises(UnknownProvider):
            set_subscriptions(session, ["hlu"], country="IN")

    def test_another_country_keeps_its_own(self, session: Session):
        refresh_providers(session, FakeCatalogue([listing("hlu")], country="US"), now=NOW)
        set_subscriptions(session, ["hlu"], country="US")

        set_subscriptions(session, ["nfx"], country="IN")

        assert subscriptions(session, country="US") == ["hlu"]
        assert subscriptions(session, country="IN") == ["nfx"]

    def test_another_user_keeps_their_own(self, session: Session):
        set_subscriptions(session, ["nfx"], country="IN", user_id="someone-else")

        set_subscriptions(session, ["prv"], country="IN", user_id="local")

        assert subscriptions(session, country="IN", user_id="someone-else") == ["nfx"]
        assert subscriptions(session, country="IN", user_id="local") == ["prv"]


class TestARefreshCannotDamageSettings:
    def test_subscriptions_survive_a_catalogue_refresh(self, session: Session):
        """The reason user_providers is not a foreign key into providers. A
        refresh rewrites the catalogue wholesale, and a cascade would quietly
        delete somebody's settings every time it ran."""
        refresh_providers(session, FakeCatalogue([listing("nfx"), listing("prv")]), now=NOW)
        set_subscriptions(session, ["nfx", "prv"], country="IN")

        refresh_providers(session, FakeCatalogue([listing("nfx"), listing("prv")]), now=NOW)

        assert subscriptions(session, country="IN") == ["nfx", "prv"]

    def test_a_subscription_survives_its_service_leaving_the_catalogue(self, session: Session):
        """Deliberate. The catalogue is JustWatch's data and disposable; the
        subscription is the user's and is not. If the service comes back -- or
        the listing was wrong -- the setting is still there."""
        refresh_providers(session, FakeCatalogue([listing("nfx"), listing("prv")]), now=NOW)
        set_subscriptions(session, ["nfx", "prv"], country="IN")

        refresh_providers(session, FakeCatalogue([listing("nfx")]), now=NOW)

        assert subscriptions(session, country="IN") == ["nfx", "prv"]

    def test_a_delisted_subscription_can_still_be_saved_again(self, session: Session):
        """Surviving the refresh is not enough on its own. Settings are saved as
        a whole set, so a client that reads its current subscriptions and sends
        them back must not be refused for including one the catalogue has since
        dropped -- that would lock somebody out of changing their settings at
        all, citing a service the picker can no longer even show them.
        """
        refresh_providers(session, FakeCatalogue([listing("nfx"), listing("prv")]), now=NOW)
        set_subscriptions(session, ["nfx", "prv"], country="IN")
        refresh_providers(session, FakeCatalogue([listing("nfx")]), now=NOW)

        set_subscriptions(session, ["nfx", "prv"], country="IN")

        assert subscriptions(session, country="IN") == ["nfx", "prv"]

    def test_a_typo_is_still_refused_alongside_a_delisted_one(self, session: Session):
        """The concession above is exactly as wide as it needs to be: something
        we accepted before stays acceptable, and nothing else becomes so."""
        refresh_providers(session, FakeCatalogue([listing("nfx"), listing("prv")]), now=NOW)
        set_subscriptions(session, ["nfx", "prv"], country="IN")
        refresh_providers(session, FakeCatalogue([listing("nfx")]), now=NOW)

        with pytest.raises(UnknownProvider):
            set_subscriptions(session, ["nfx", "prv", "nope"], country="IN")

    def test_dropping_a_delisted_subscription_is_permanent(self, session: Session):
        """Once it has been let go it is gone: it is no longer stored, so it is
        no longer something we previously accepted."""
        refresh_providers(session, FakeCatalogue([listing("nfx"), listing("prv")]), now=NOW)
        set_subscriptions(session, ["nfx", "prv"], country="IN")
        refresh_providers(session, FakeCatalogue([listing("nfx")]), now=NOW)
        set_subscriptions(session, ["nfx"], country="IN")

        with pytest.raises(UnknownProvider):
            set_subscriptions(session, ["nfx", "prv"], country="IN")

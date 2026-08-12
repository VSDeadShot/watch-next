"""Tests for the settings endpoints.

These go through the real app with a real (in-memory) database and a fake
catalogue. Nothing here touches the network.

The distinction under test throughout is between JustWatch's data and the user's.
Listing services is free and offline; refreshing that list costs a request; and
neither is allowed to disturb the subscriptions, which are the only thing here
that cannot be fetched again from anywhere.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from simplejustwatchapi.exceptions import JustWatchHttpError
from sqlalchemy.orm import Session

from app.api.deps import get_catalogue
from app.config import Settings, get_settings
from app.db import get_db
from app.main import app
from app.services.justwatch_client import ProviderEntry

CATALOGUE = "/api/providers"
REFRESH = "/api/providers/refresh"
MINE = "/api/providers/mine"

NETFLIX = ProviderEntry(
    short_name="nfx",
    technical_name="netflix",
    name="Netflix",
    monetization_types=("FLATRATE",),
    icon_url="https://images.justwatch.com/icon/207360008/s100/netflix.png",
)
PRIME = ProviderEntry(
    short_name="prv",
    technical_name="amazonprimevideo",
    name="Amazon Prime Video",
    monetization_types=("FLATRATE", "RENT", "BUY"),
)
HOTSTAR = ProviderEntry(short_name="hst", technical_name="hotstar", name="JioHotstar")


class FakeCatalogue:
    """Answers the provider listing from a script, and counts being asked."""

    def __init__(self, listed: list[ProviderEntry] | Exception | None = None, country: str = "IN"):
        self.country = country
        self.listed = listed if listed is not None else [NETFLIX, PRIME, HOTSTAR]
        self.calls = 0

    def providers(self) -> list[ProviderEntry]:
        self.calls += 1
        if isinstance(self.listed, Exception):
            raise self.listed
        return list(self.listed)


@pytest.fixture
def catalogue() -> FakeCatalogue:
    return FakeCatalogue()


@pytest.fixture
def client(session: Session, catalogue: FakeCatalogue) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_catalogue] = lambda: catalogue
    # Pinned rather than inherited, so a developer with a different country in
    # their own .env does not get different test results from everybody else.
    app.dependency_overrides[get_settings] = lambda: Settings(jw_country="IN", jw_language="en")
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


class TestListingWhatExists:
    def test_nothing_is_listed_before_a_refresh(self, client: TestClient):
        """An empty list rather than an error: this is the state a fresh install
        is in, and it is the frontend's cue to offer a refresh."""
        body = client.get(CATALOGUE).json()

        assert body["providers"] == []
        assert body["country"] == "IN"

    def test_reading_the_list_asks_justwatch_nothing(
        self, client: TestClient, catalogue: FakeCatalogue
    ):
        """The settings page opens on every visit. Making it a live request
        would spend the budget on a list that changes a few times a year."""
        client.post(REFRESH)

        client.get(CATALOGUE)
        client.get(CATALOGUE)

        assert catalogue.calls == 1

    def test_a_service_comes_back_with_what_the_picker_renders(self, client: TestClient):
        client.post(REFRESH)

        [netflix] = [p for p in client.get(CATALOGUE).json()["providers"] if p["name"] == "Netflix"]

        assert netflix["short_name"] == "nfx"
        assert netflix["technical_name"] == "netflix"
        assert netflix["icon_url"] == "https://images.justwatch.com/icon/207360008/s100/netflix.png"
        assert netflix["monetization_types"] == ["FLATRATE"]

    def test_they_are_ordered_for_a_person_to_read(self, client: TestClient):
        client.post(REFRESH)

        names = [provider["name"] for provider in client.get(CATALOGUE).json()["providers"]]

        assert names == ["Amazon Prime Video", "JioHotstar", "Netflix"]

    def test_an_icon_already_stored_from_another_host_does_not_come_back(
        self, client: TestClient, catalogue: FakeCatalogue
    ):
        """The case the check on the way out exists for.

        This fake catalogue hands the service a record directly, which is exactly
        the shape of a row written before the client learned to refuse one -- and
        a provider row has no TTL, so nothing would ever revisit it. The refresh
        stores it and the listing declines to serve it.

        The value is what the client library builds from a JSON field of
        "@evil.test/x.jpg": its own host, then the field, nothing in between. An
        `src` is fetched as the settings page draws, so serving this would hand
        that host the viewer's address for the price of opening the page.
        """
        catalogue.listed = [
            ProviderEntry(
                short_name="nfx",
                technical_name="netflix",
                name="Netflix",
                icon_url="https://images.justwatch.com@evil.test/x.png",
            )
        ]
        client.post(REFRESH)

        [netflix] = client.get(CATALOGUE).json()["providers"]

        assert netflix["icon_url"] is None
        # Still listed, and still selectable. Losing a service from the picker
        # would cost the user a subscription they actually have, which is worse
        # than a tile with two letters on it.
        assert netflix["short_name"] == "nfx"
        assert netflix["name"] == "Netflix"


class TestRefreshing:
    def test_it_reports_what_changed(self, client: TestClient):
        body = client.post(REFRESH).json()

        assert body["fetched"] == 3
        assert body["added"] == 3
        assert body["removed"] == 0
        assert body["country"] == "IN"

    def test_a_second_refresh_updates_rather_than_adds(self, client: TestClient):
        client.post(REFRESH)

        body = client.post(REFRESH).json()

        assert body["added"] == 0
        assert body["updated"] == 3

    def test_an_outage_is_a_502(self, client: TestClient, catalogue: FakeCatalogue):
        """Nothing was written, so the honest answer is "we could not ask", and
        retrying the same request is safe."""
        catalogue.listed = JustWatchHttpError("timed out")

        assert client.post(REFRESH).status_code == 502

    def test_an_outage_leaves_the_stored_list_alone(
        self, client: TestClient, catalogue: FakeCatalogue
    ):
        client.post(REFRESH)
        catalogue.listed = JustWatchHttpError("timed out")

        client.post(REFRESH)

        assert len(client.get(CATALOGUE).json()["providers"]) == 3

    def test_an_empty_answer_keeps_what_we_have_and_says_so(
        self, client: TestClient, catalogue: FakeCatalogue
    ):
        """Not an error, but not a wipe either. An emptied picker means no
        subscriptions can be set, which makes every title unavailable."""
        client.post(REFRESH)
        catalogue.listed = []

        body = client.post(REFRESH).json()

        assert body["fetched"] == 0
        assert body["removed"] == 0
        assert len(client.get(CATALOGUE).json()["providers"]) == 3


class TestSubscriptions:
    @pytest.fixture(autouse=True)
    def stocked(self, client: TestClient):
        client.post(REFRESH)

    def test_nobody_is_subscribed_to_anything_at_first(self, client: TestClient):
        """Empty is the honest starting answer, and it is not the same as "all".
        A filter that read it as "all" would recommend things nobody can watch."""
        body = client.get(MINE).json()

        assert body["short_names"] == []
        assert body["country"] == "IN"

    def test_what_was_put_comes_back(self, client: TestClient):
        client.put(MINE, json={"short_names": ["nfx", "prv"]})

        assert client.get(MINE).json()["short_names"] == ["nfx", "prv"]

    def test_the_put_answers_with_the_stored_set(self, client: TestClient):
        """So the page can render from the response rather than re-fetching and
        hoping the two agree."""
        body = client.put(MINE, json={"short_names": ["prv", "nfx"]}).json()

        assert body["short_names"] == ["nfx", "prv"]

    def test_putting_replaces_the_whole_set(self, client: TestClient):
        client.put(MINE, json={"short_names": ["nfx", "prv"]})

        client.put(MINE, json={"short_names": ["hst"]})

        assert client.get(MINE).json()["short_names"] == ["hst"]

    def test_an_empty_list_cancels_everything(self, client: TestClient):
        """A real thing a person does, so it has to be expressible. This is why
        the request body has no minimum length."""
        client.put(MINE, json={"short_names": ["nfx"]})

        response = client.put(MINE, json={"short_names": []})

        assert response.status_code == 200
        assert client.get(MINE).json()["short_names"] == []

    def test_a_service_that_does_not_exist_is_a_400(self, client: TestClient):
        """The route is fine and the body is wrong, which is a 400 rather than a
        404. Stored quietly it would be invisible: it matches no offer, so the
        only symptom is never being recommended anything on it."""
        response = client.put(MINE, json={"short_names": ["nfx", "nope"]})

        assert response.status_code == 400
        assert "nope" in response.json()["detail"]

    def test_a_rejected_put_changes_nothing(self, client: TestClient):
        """All or nothing. Half-applied settings are worse than rejected ones,
        because nobody is told which half survived."""
        client.put(MINE, json={"short_names": ["nfx"]})

        client.put(MINE, json={"short_names": ["prv", "nope"]})

        assert client.get(MINE).json()["short_names"] == ["nfx"]

    def test_subscriptions_survive_a_catalogue_refresh(self, client: TestClient):
        """The whole reason the two are separate tables. A refresh rewrites
        JustWatch's data; it must not be able to reach the user's."""
        client.put(MINE, json={"short_names": ["nfx", "prv"]})

        client.post(REFRESH)

        assert client.get(MINE).json()["short_names"] == ["nfx", "prv"]

    def test_a_subscription_outlives_its_service_leaving_the_catalogue(
        self, client: TestClient, catalogue: FakeCatalogue
    ):
        """Deliberate: the listing may be wrong or temporary, and the setting is
        the user's to change, not ours to delete on their behalf."""
        client.put(MINE, json={"short_names": ["nfx", "hst"]})
        catalogue.listed = [NETFLIX, PRIME]

        client.post(REFRESH)

        assert client.get(MINE).json()["short_names"] == ["hst", "nfx"]

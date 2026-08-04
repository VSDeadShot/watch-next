"""Tests for the endpoint the whole app is built around.

Through the real app, with a real (in-memory) database and a fake catalogue.
Nothing here touches the network.

The contract is the feature. A response that could carry two titles would be a
response some client eventually renders as a feed, and the decision paralysis
this app exists to kill would be back by the end of the month -- so a good half
of these are about the *shape* of the answer rather than its content.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_catalogue
from app.config import Settings, get_settings
from app.db import get_db
from app.main import app
from app.models import Provider
from app.services.justwatch_client import CatalogueEntry, OfferEntry
from app.services.providers import set_subscriptions
from app.services.titles import store_title

RECOMMEND = "/api/recommend"

NETFLIX = "nfx"
PRIME = "prv"


def entry(node_id: str, **overrides) -> CatalogueEntry:
    values = {
        "title": f"Title {node_id}",
        "object_type": "MOVIE",
        "release_year": 2021,
        "runtime_minutes": 100,
        "genres": ("cmy",),
        "imdb_score": 8.2,
        "poster_url": "https://img/poster.jpg",
        "offers": (
            OfferEntry(
                provider=NETFLIX, monetization="FLATRATE", url="https://netflix.com/title/1"
            ),
        ),
    }
    return CatalogueEntry(node_id=node_id, **{**values, **overrides})


class FakeCatalogue:
    """A popularity listing that answers from a script."""

    country = "IN"

    def __init__(self, listed: list[CatalogueEntry] | None = None):
        self.listed = listed if listed is not None else []
        self.calls = 0

    def popular(self, *, providers=None, object_types=None, count=50, offset=0):
        self.calls += 1
        # Only the first page has anything; the rest are the end of the listing.
        return list(self.listed) if offset == 0 else []


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


@pytest.fixture
def subscribed(session: Session) -> Session:
    session.add(
        Provider(country="IN", short_name=NETFLIX, technical_name="netflix", name="Netflix")
    )
    session.add(
        Provider(country="IN", short_name=PRIME, technical_name="amazonprime", name="Prime Video")
    )
    session.flush()
    set_subscriptions(session, [NETFLIX], country="IN")
    return session


def stock(session: Session, *entries: CatalogueEntry) -> None:
    for item in entries:
        store_title(session, item, country="IN")
    session.flush()


class TestTheShapeOfTheAnswer:
    def test_one_title_comes_back_as_one_object(self, client: TestClient, subscribed: Session):
        stock(subscribed, entry("tm1"))

        body = client.post(RECOMMEND, json={}).json()

        assert isinstance(body["title"], dict)

    def test_there_is_nowhere_to_put_a_second_answer(self, client: TestClient):
        """The constraint lives in the contract rather than in the interface, so
        no client can decide to render three."""
        schema = client.app.openapi()["components"]["schemas"]["RecommendationResponse"]

        assert not [
            name
            for name, field in schema["properties"].items()
            if field.get("type") == "array" and name != "considered"
        ]

    def test_nothing_to_recommend_is_a_normal_answer(self, client: TestClient):
        """A 200 with a null title and a sentence, not a 404. The route worked
        and the answer is real -- an error status would have the client render a
        failure instead of showing what to do about it."""
        response = client.post(RECOMMEND, json={})

        assert response.status_code == 200
        assert response.json()["title"] is None
        assert response.json()["reason"]

    def test_an_empty_body_is_a_valid_request(self, client: TestClient, subscribed: Session):
        """Somebody who just wants to be told something should not have to fill
        anything in."""
        stock(subscribed, entry("tm1"))

        assert client.post(RECOMMEND, json={}).status_code == 200


class TestWhatComesBackWithIt:
    def test_everything_needed_to_render_a_card(self, client: TestClient, subscribed: Session):
        stock(subscribed, entry("tm1", title="Inception"))

        title = client.post(RECOMMEND, json={}).json()["title"]

        assert title["title"] == "Inception"
        assert title["poster_url"] == "https://img/poster.jpg"
        assert title["runtime_minutes"] == 100
        # In English, not JustWatch's private shortcodes: a client handed "cmy"
        # can only print it or keep its own copy of our genre table.
        assert title["genres"] == ["Comedy"]
        assert title["imdb_score"] == 8.2

    def test_the_reasons_are_in_plain_language(self, client: TestClient, subscribed: Session):
        stock(subscribed, entry("tm1"))

        title = client.post(RECOMMEND, json={}).json()["title"]

        assert title["reasons"]
        assert all(isinstance(reason, str) for reason in title["reasons"])

    def test_where_to_watch_it_comes_with_a_link(self, client: TestClient, subscribed: Session):
        stock(subscribed, entry("tm1"))

        [option] = client.post(RECOMMEND, json={}).json()["title"]["watch_on"]

        assert option["short_name"] == NETFLIX
        assert option["name"] == "Netflix"
        assert option["url"] == "https://netflix.com/title/1"
        assert option["requires_subscription"] is True

    def test_the_counts_say_where_it_collapsed(self, client: TestClient, subscribed: Session):
        on_prime = OfferEntry(provider=PRIME, monetization="FLATRATE")
        stock(subscribed, entry("tm1", offers=(on_prime,)))

        body = client.post(RECOMMEND, json={}).json()

        assert body["considered"] == {"pool": 1, "available": 0, "eligible": 0}


class TestAsking:
    def test_a_mood_is_passed_through(self, client: TestClient, subscribed: Session):
        stock(subscribed, entry("tm1", genres=("cmy",)), entry("tm2", genres=("trl",)))

        body = client.post(RECOMMEND, json={"mood": "thrill"}).json()

        assert body["title"]["jw_node_id"] == "tm2"

    def test_a_time_budget_is_honoured(self, client: TestClient, subscribed: Session):
        stock(subscribed, entry("tm1", runtime_minutes=190))

        body = client.post(RECOMMEND, json={"minutes_available": 45}).json()

        assert body["title"] is None
        assert "45" in body["reason"]

    def test_a_kind_preference_is_honoured(self, client: TestClient, subscribed: Session):
        stock(subscribed, entry("tm1", object_type="SHOW"), entry("tm2", object_type="MOVIE"))

        body = client.post(RECOMMEND, json={"kind": "movie"}).json()

        assert body["title"]["object_type"] == "MOVIE"

    def test_not_this_one_moves_on(self, client: TestClient, subscribed: Session):
        stock(subscribed, entry("tm1"), entry("tm2"))
        first = client.post(RECOMMEND, json={}).json()["title"]

        again = client.post(RECOMMEND, json={"exclude_ids": [first["title_id"]]}).json()

        assert again["title"]["title_id"] != first["title_id"]

    def test_an_unknown_mood_is_refused_rather_than_ignored(self, client: TestClient):
        """Silently falling back would answer a question nobody asked."""
        response = client.post(RECOMMEND, json={"mood": "hungry"})

        assert response.status_code == 422

    def test_a_negative_time_budget_is_refused(self, client: TestClient):
        assert client.post(RECOMMEND, json={"minutes_available": -30}).status_code == 422

    def test_an_absurd_time_budget_is_refused(self, client: TestClient):
        """A day and a half is a typo, not a plan."""
        assert client.post(RECOMMEND, json={"minutes_available": 99999}).status_code == 422

    def test_an_unbounded_exclusion_list_is_refused(self, client: TestClient):
        """It becomes an IN clause, and a client should not be able to make that
        arbitrarily large."""
        response = client.post(RECOMMEND, json={"exclude_ids": list(range(500))})

        assert response.status_code == 422


class TestTheDiscoveryPool:
    def test_an_empty_pool_is_filled_from_justwatch(
        self, client: TestClient, subscribed: Session, catalogue: FakeCatalogue
    ):
        catalogue.listed = [entry("tm1")]

        body = client.post(RECOMMEND, json={}).json()

        assert catalogue.calls
        assert body["title"]["jw_node_id"] == "tm1"

    def test_asking_again_costs_no_requests(
        self, client: TestClient, subscribed: Session, catalogue: FakeCatalogue
    ):
        catalogue.listed = [entry("tm1"), entry("tm2")]
        client.post(RECOMMEND, json={})
        spent = catalogue.calls

        client.post(RECOMMEND, json={})

        assert catalogue.calls == spent

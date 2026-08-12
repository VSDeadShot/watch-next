"""Tests for the availability refresh endpoint.

Availability is a hard filter rather than a ranking signal, so a stale offer
cache does not merely make the answer worse -- it makes the answer wrong while
looking entirely confident. Offers were written once at resolution and nothing
refreshed them afterwards; this endpoint is what does.

It goes through the real app with a real (in-memory) database and a fake
catalogue. Nothing here touches the network.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from simplejustwatchapi.exceptions import JustWatchHttpError
from sqlalchemy.orm import Session

from app.api.deps import get_catalogue
from app.config import Settings, get_settings
from app.core.availability import Monetization
from app.db import get_db
from app.main import app
from app.models import Title
from app.services.justwatch_client import MAX_REQUESTS_PER_PASS, CatalogueEntry, OfferEntry
from app.services.single_flight import budget

REFRESH = "/api/offers/refresh"


def offer(provider: str) -> OfferEntry:
    return OfferEntry(
        provider=provider,
        monetization=Monetization.FLATRATE,
        url=f"https://example.test/{provider}",
    )


class FakeCatalogue:
    """Answers `details` from a script, and counts being asked."""

    def __init__(self, entries: dict[str, CatalogueEntry | Exception], country: str = "IN"):
        self.country = country
        self.entries = entries
        self.looked_up: list[str] = []

    def details(self, node_id: str) -> CatalogueEntry:
        self.looked_up.append(node_id)
        outcome = self.entries[node_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def entry_for(node_id: str, *providers: str) -> CatalogueEntry:
    return CatalogueEntry(
        node_id=node_id,
        title=node_id,
        object_type="MOVIE",
        offers=tuple(offer(name) for name in providers),
    )


@pytest.fixture
def catalogue() -> FakeCatalogue:
    return FakeCatalogue({f"tm{n}": entry_for(f"tm{n}", "nfx") for n in range(1, 6)})


@pytest.fixture
def client(session: Session, catalogue: FakeCatalogue) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_catalogue] = lambda: catalogue
    app.dependency_overrides[get_settings] = lambda: Settings(jw_country="IN", jw_language="en")
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def stale_titles(session: Session, count: int) -> None:
    """Titles nobody has ever asked about, which is the stalest state there is."""
    for n in range(1, count + 1):
        session.add(
            Title(jw_node_id=f"tm{n}", object_type="MOVIE", title=f"tm{n}", offers_fetched_at=None)
        )
    session.flush()


class TestRefreshing:
    def test_an_empty_catalogue_is_finished_rather_than_an_error(self, client: TestClient):
        """The state a fresh install is in. Nothing to do is a valid answer."""
        body = client.post(REFRESH).json()

        assert body["refreshed"] == 0
        assert body["remaining"] == 0

    def test_it_refreshes_and_says_so(self, client: TestClient, session: Session):
        stale_titles(session, 2)

        body = client.post(REFRESH).json()

        assert body["refreshed"] == 2
        assert body["offers_stored"] == 2
        assert body["remaining"] == 0

    def test_a_limit_is_a_budget_of_requests(
        self, client: TestClient, session: Session, catalogue: FakeCatalogue
    ):
        stale_titles(session, 5)

        body = client.post(REFRESH, params={"limit": 2}).json()

        assert body["refreshed"] == 2
        assert body["remaining"] == 3
        assert len(catalogue.looked_up) == 2

    def test_batches_carry_on_from_each_other(self, client: TestClient, session: Session):
        stale_titles(session, 5)

        client.post(REFRESH, params={"limit": 2})
        body = client.post(REFRESH, params={"limit": 2}).json()

        assert body["refreshed"] == 2
        assert body["remaining"] == 1

    def test_a_limit_below_one_is_refused(self, client: TestClient):
        """Zero would be a pass that asks nothing and reports no progress, which
        a batching caller would read as an outage and give up on."""
        assert client.post(REFRESH, params={"limit": 0}).status_code == 422


class TestWhenJustWatchIsUnwell:
    def test_one_failure_does_not_abandon_the_rest(self, client: TestClient, session: Session):
        stale_titles(session, 2)
        app.dependency_overrides[get_catalogue] = lambda: FakeCatalogue(
            {"tm1": JustWatchHttpError("timed out"), "tm2": entry_for("tm2", "nfx")}
        )

        body = client.post(REFRESH).json()

        assert body["refreshed"] == 1
        assert body["failed"] == 1

    def test_a_failed_title_is_still_counted_as_remaining(
        self, client: TestClient, session: Session
    ):
        """Nothing was learned about it, so it is still work to do -- and this is
        why a caller cannot end a run on `remaining` alone."""
        stale_titles(session, 1)
        app.dependency_overrides[get_catalogue] = lambda: FakeCatalogue(
            {"tm1": JustWatchHttpError("timed out")}
        )

        body = client.post(REFRESH).json()

        assert body["failed"] == 1
        assert body["remaining"] == 1

    def test_a_failure_is_not_an_error_response(self, client: TestClient, session: Session):
        """A refresh walks the whole catalogue. Raising on the first dropped
        request would throw away every answer collected before it."""
        stale_titles(session, 1)
        app.dependency_overrides[get_catalogue] = lambda: FakeCatalogue(
            {"tm1": JustWatchHttpError("timed out")}
        )

        assert client.post(REFRESH).status_code == 200


class TestItDoesNotReAskWhatIsFresh:
    def test_a_freshly_refreshed_title_is_left_alone(
        self, client: TestClient, session: Session, catalogue: FakeCatalogue
    ):
        """The budget is spent on the answers most likely to have changed."""
        session.add(
            Title(
                jw_node_id="tm1",
                object_type="MOVIE",
                title="tm1",
                offers_fetched_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        session.flush()

        body = client.post(REFRESH).json()

        assert body["refreshed"] == 0
        assert catalogue.looked_up == []


class TestRefusingASecondPass:
    """Refresh and resolution spend one budget between them, so the guard is
    shared: two passes of different kinds contend exactly as two of the same
    kind do. See `app/services/single_flight.py`."""

    def test_a_refresh_is_refused_while_a_resolve_is_running(self, client: TestClient):
        with budget.claim("resolve"):
            response = client.post(REFRESH)

        assert response.status_code == 409

    def test_the_refusal_names_the_pass_in_the_way(self, client: TestClient):
        with budget.claim("resolve"):
            detail = client.post(REFRESH).json()["detail"]

        assert "resolve" in detail

    def test_it_refreshes_again_once_the_budget_is_free(self, client: TestClient):
        with budget.claim("resolve"):
            assert client.post(REFRESH).status_code == 409

        assert client.post(REFRESH).status_code == 200

    def test_an_absurd_limit_is_rejected_rather_than_quietly_clamped(self, client: TestClient):
        response = client.post(REFRESH, params={"limit": MAX_REQUESTS_PER_PASS + 1})

        assert response.status_code == 422

    def test_the_largest_allowed_limit_is_accepted(self, client: TestClient):
        response = client.post(REFRESH, params={"limit": MAX_REQUESTS_PER_PASS})

        assert response.status_code == 200

"""Tests for the resolution endpoints.

These go through the real app with a real (in-memory) database. Two things are
swapped out: which database the handler is handed, and which catalogue. Nothing
here touches the network.

The endpoints exist for one workflow -- run a pass, look at what it could not
decide, decide it yourself -- so most of these follow that shape rather than
checking each route in isolation.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from simplejustwatchapi.exceptions import JustWatchApiError, JustWatchHttpError
from sqlalchemy.orm import Session

from app.api.deps import get_catalogue
from app.db import get_db
from app.main import app
from app.services.justwatch_client import CatalogueEntry

RESOLVE = "/api/titles/resolve"
UNRESOLVED = "/api/titles/unresolved"

INCEPTION = CatalogueEntry(node_id="tm1", title="Inception", object_type="MOVIE", release_year=2010)
DUNE_1984 = CatalogueEntry(node_id="tm84", title="Dune", object_type="MOVIE", release_year=1984)
DUNE_2021 = CatalogueEntry(
    node_id="tm21",
    title="Dune",
    object_type="MOVIE",
    release_year=2021,
    runtime_minutes=155,
    genres=("scf",),
)


class FakeCatalogue:
    """Search and lookup together, because one dependency serves both routes."""

    def __init__(self, results: dict | None = None, entries: dict | None = None):
        self.results = results or {}
        self.entries = entries or {}
        self.searched: list[str] = []

    def search(self, title: str, *, object_types=None) -> list[CatalogueEntry]:
        self.searched.append(title)
        outcome = self.results.get(title, [])
        if isinstance(outcome, Exception):
            raise outcome
        return list(outcome)

    def details(self, node_id: str) -> CatalogueEntry:
        outcome = self.entries.get(node_id)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            raise JustWatchApiError([{"message": "no such node", "code": "NOT_FOUND"}])
        return outcome


@pytest.fixture
def catalogue() -> FakeCatalogue:
    return FakeCatalogue(
        results={"Inception": [INCEPTION], "Dune": [DUNE_1984, DUNE_2021]},
        entries={"tm21": DUNE_2021, "tm84": DUNE_1984},
    )


@pytest.fixture
def client(session: Session, catalogue: FakeCatalogue) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_catalogue] = lambda: catalogue
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def resolution_id(client: TestClient, query_title: str) -> int:
    [match] = [row for row in client.get(UNRESOLVED).json() if row["query_title"] == query_title]
    return match["resolution_id"]


class TestRunningAPass:
    def test_it_reports_what_it_did(self, client: TestClient, watched):
        watched("Inception")
        watched("Dune")

        body = client.post(RESOLVE).json()

        assert body["searched"] == 2
        assert body["resolved"] == 1
        assert body["unresolved"] == 1
        assert body["linked_events"] == 1

    def test_a_second_pass_asks_for_nothing(
        self, client: TestClient, catalogue: FakeCatalogue, watched
    ):
        watched("Inception")
        client.post(RESOLVE)
        catalogue.searched.clear()

        assert client.post(RESOLVE).json()["searched"] == 0
        assert catalogue.searched == []

    def test_re_asking_about_refusals_has_to_be_asked_for(
        self, client: TestClient, catalogue: FakeCatalogue, watched
    ):
        """Every run re-asking about every refusal would spend a request per
        unresolved title per pass, to be told the same thing each time."""
        watched("Dune")
        client.post(RESOLVE)
        catalogue.searched.clear()

        client.post(RESOLVE, params={"retry_unresolved": True})

        assert catalogue.searched == ["Dune"]

    def test_a_catalogue_outage_does_not_fail_the_request(self, client: TestClient, watched):
        """One dropped request must not throw away the rest of the pass, so the
        failure is a number in the summary rather than a 500."""
        watched("Nothing Answers For This")
        client.app.dependency_overrides[get_catalogue] = lambda: FakeCatalogue(
            results={"Nothing Answers For This": JustWatchHttpError("timed out")}
        )

        response = client.post(RESOLVE)

        assert response.status_code == 200
        assert response.json()["failed"] == 1


class TestListingWhatNeedsAPerson:
    def test_a_refusal_is_listed_with_its_candidates(self, client: TestClient, watched):
        watched("Dune")
        client.post(RESOLVE)

        [unresolved] = client.get(UNRESOLVED).json()

        assert unresolved["query_title"] == "Dune"
        assert unresolved["event_count"] == 1
        assert unresolved["reason"]
        assert {c["node_id"] for c in unresolved["candidates"]} == {"tm84", "tm21"}

    def test_the_candidates_carry_what_a_person_needs_to_tell_them_apart(
        self, client: TestClient, watched
    ):
        """Two films called Dune. Without the year the buttons are identical."""
        watched("Dune")
        client.post(RESOLVE)

        [unresolved] = client.get(UNRESOLVED).json()

        assert {c["release_year"] for c in unresolved["candidates"]} == {1984, 2021}

    def test_a_resolved_library_lists_nothing(self, client: TestClient, watched):
        watched("Inception")
        client.post(RESOLVE)

        assert client.get(UNRESOLVED).json() == []


class TestFixingATitleByHand:
    def _refuse(self, client: TestClient, watched, title: str = "Dune") -> int:
        watched(title)
        client.post(RESOLVE)
        return resolution_id(client, title)

    def test_choosing_a_candidate_returns_the_title_it_settled_on(
        self, client: TestClient, watched
    ):
        stored = self._refuse(client, watched)

        body = client.put(f"/api/titles/resolutions/{stored}", json={"node_id": "tm21"}).json()

        assert body["title"] == "Dune"
        assert body["release_year"] == 2021
        assert body["linked_events"] == 1

    def test_the_title_stops_being_listed_as_unresolved(self, client: TestClient, watched):
        stored = self._refuse(client, watched)

        client.put(f"/api/titles/resolutions/{stored}", json={"node_id": "tm21"})

        assert client.get(UNRESOLVED).json() == []

    def test_a_later_pass_does_not_undo_it(
        self, client: TestClient, catalogue: FakeCatalogue, watched
    ):
        stored = self._refuse(client, watched)
        client.put(f"/api/titles/resolutions/{stored}", json={"node_id": "tm21"})
        catalogue.searched.clear()

        client.post(RESOLVE, params={"retry_unresolved": True})

        assert catalogue.searched == []
        assert client.get(UNRESOLVED).json() == []

    def test_an_unknown_resolution_is_a_404(self, client: TestClient):
        response = client.put("/api/titles/resolutions/999", json={"node_id": "tm21"})

        assert response.status_code == 404

    def test_an_id_justwatch_does_not_know_is_a_404(self, client: TestClient, watched):
        """A bad id is the caller's mistake, not an outage, and saying so is the
        difference between "try again" and "pick something else"."""
        stored = self._refuse(client, watched)

        response = client.put(f"/api/titles/resolutions/{stored}", json={"node_id": "tm-nonsense"})

        assert response.status_code == 404

    def test_a_catalogue_outage_is_a_bad_gateway(self, client: TestClient, watched):
        stored = self._refuse(client, watched)
        client.app.dependency_overrides[get_catalogue] = lambda: FakeCatalogue(
            entries={"tm21": JustWatchHttpError("timed out")}
        )

        response = client.put(f"/api/titles/resolutions/{stored}", json={"node_id": "tm21"})

        assert response.status_code == 502

    def test_a_failed_fix_leaves_the_title_unresolved(self, client: TestClient, watched):
        """Not half-applied: a title marked decided by hand and pointing nowhere
        is worse than one still openly waiting for a decision."""
        stored = self._refuse(client, watched)

        client.put(f"/api/titles/resolutions/{stored}", json={"node_id": "tm-nonsense"})

        assert [row["resolution_id"] for row in client.get(UNRESOLVED).json()] == [stored]

    def test_a_request_with_no_node_id_is_rejected(self, client: TestClient, watched):
        stored = self._refuse(client, watched)

        assert client.put(f"/api/titles/resolutions/{stored}", json={}).status_code == 422

    def test_a_blank_node_id_is_rejected_before_it_reaches_justwatch(
        self, client: TestClient, watched
    ):
        stored = self._refuse(client, watched)

        assert (
            client.put(f"/api/titles/resolutions/{stored}", json={"node_id": "  "}).status_code
            == 422
        )

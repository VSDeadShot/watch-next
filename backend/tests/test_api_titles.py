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
from app.services.justwatch_client import MAX_REQUESTS_PER_PASS, CatalogueEntry
from app.services.single_flight import budget

RESOLVE = "/api/titles/resolve"
UNRESOLVED = "/api/titles/unresolved"
SEARCH = "/api/titles/search"
RESOLUTIONS = "/api/titles/resolutions"


def queue(client: TestClient) -> list[dict]:
    """The rows of one page of the fixer's queue.

    The endpoint answers with an envelope rather than a bare list, because the
    length of the whole queue is the part a caller cannot work out for itself.
    Most of these tests are about a single refusal and only want the rows.
    """
    return client.get(UNRESOLVED).json()["items"]


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

    def __init__(
        self, results: dict | None = None, entries: dict | None = None, country: str = "IN"
    ):
        # Both routes cache offers as they arrive, and an offer without the
        # country it was fetched for is not an answer to anything.
        self.country = country
        self.results = results or {}
        self.entries = entries or {}
        self.searched: list[str] = []
        # What each search was narrowed to, so the kind filter can be asserted
        # on rather than assumed.
        self.search_types: list[tuple[str, ...] | None] = []

    def search(self, title: str, *, object_types=None) -> list[CatalogueEntry]:
        self.searched.append(title)
        self.search_types.append(tuple(object_types) if object_types else None)
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
    [match] = [row for row in queue(client) if row["query_title"] == query_title]
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

        [unresolved] = queue(client)

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

        [unresolved] = queue(client)

        assert {c["release_year"] for c in unresolved["candidates"]} == {1984, 2021}

    def test_a_resolved_library_lists_nothing(self, client: TestClient, watched):
        watched("Inception")
        client.post(RESOLVE)

        assert queue(client) == []


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

        assert queue(client) == []

    def test_a_later_pass_does_not_undo_it(
        self, client: TestClient, catalogue: FakeCatalogue, watched
    ):
        stored = self._refuse(client, watched)
        client.put(f"/api/titles/resolutions/{stored}", json={"node_id": "tm21"})
        catalogue.searched.clear()

        client.post(RESOLVE, params={"retry_unresolved": True})

        assert catalogue.searched == []
        assert queue(client) == []

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

        assert [row["resolution_id"] for row in queue(client)] == [stored]

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


class TestRunningAPassInBatches:
    """A whole library in one request is minutes long at a request a second, so
    a caller with a person watching drives it in pieces."""

    def test_a_limit_caps_the_work_one_request_does(self, client: TestClient, watched):
        watched("Inception")
        watched("Dune")

        body = client.post(RESOLVE, params={"limit": 1}).json()

        assert body["searched"] == 1

    def test_the_body_says_how_much_is_left(self, client: TestClient, watched):
        watched("Inception")
        watched("Dune")

        body = client.post(RESOLVE, params={"limit": 1}).json()

        assert body["remaining"] == 1

    def test_nothing_is_left_once_the_pass_has_been_round_everything(
        self, client: TestClient, watched
    ):
        watched("Inception")

        assert client.post(RESOLVE).json()["remaining"] == 0

    def test_repeating_until_nothing_remains_finishes_the_library(
        self, client: TestClient, watched
    ):
        watched("Inception")
        watched("Dune")

        requests = 0
        while client.post(RESOLVE, params={"limit": 1}).json()["remaining"]:
            requests += 1
            assert requests < 5, "batching is not making progress"

        assert queue(client)[0]["query_title"] == "Dune"

    def test_a_limit_of_nothing_is_rejected(self, client: TestClient):
        """Zero is a request to do no work, which is a caller mistake rather
        than an instruction worth honouring."""
        assert client.post(RESOLVE, params={"limit": 0}).status_code == 422


class TestPagingTheQueue:
    def refuse(self, client: TestClient, watched, count: int) -> None:
        for index in range(count):
            watched(f"Dune {index}")
        client.post(RESOLVE)

    def test_reports_the_whole_length_beside_the_page(self, client: TestClient, watched):
        self.refuse(client, watched, 4)

        body = client.get(UNRESOLVED, params={"limit": 2}).json()

        assert body["total"] == 4
        assert len(body["items"]) == 2

    def test_an_offset_moves_the_window(self, client: TestClient, watched):
        self.refuse(client, watched, 4)

        first = client.get(UNRESOLVED, params={"limit": 2}).json()["items"]
        second = client.get(UNRESOLVED, params={"limit": 2, "offset": 2}).json()["items"]

        assert {row["query_title"] for row in first}.isdisjoint(
            row["query_title"] for row in second
        )

    def test_an_empty_queue_still_answers_with_an_envelope(self, client: TestClient):
        body = client.get(UNRESOLVED).json()

        assert body == {"total": 0, "items": []}

    def test_a_page_of_nothing_is_rejected(self, client: TestClient):
        assert client.get(UNRESOLVED, params={"limit": 0}).status_code == 422

    def test_a_negative_offset_is_rejected(self, client: TestClient):
        assert client.get(UNRESOLVED, params={"offset": -1}).status_code == 422

    def test_an_absurd_page_size_is_rejected(self, client: TestClient):
        """A cap, because the whole point of paging this is that the untrimmed
        answer was too big to want."""
        assert client.get(UNRESOLVED, params={"limit": 5000}).status_code == 422


class TestSearchingTheCatalogue:
    def test_finds_what_was_typed(self, client: TestClient):
        body = client.get(SEARCH, params={"q": "Dune"}).json()

        assert [row["node_id"] for row in body] == ["tm84", "tm21"]
        assert body[0]["release_year"] == 1984

    def test_asks_the_catalogue_for_the_typed_words_rather_than_the_stored_ones(
        self, client: TestClient, catalogue: FakeCatalogue, watched
    ):
        """The whole point: the exported spelling is what failed, so the search
        has to run on what the person typed instead."""
        client.get(SEARCH, params={"q": "Dune"})

        assert catalogue.searched == ["Dune"]

    def test_narrows_to_films_when_asked(self, client: TestClient, catalogue: FakeCatalogue):
        client.get(SEARCH, params={"q": "Dune", "kind": "movie"})

        assert catalogue.search_types == [("MOVIE",)]

    def test_narrows_to_series_for_an_episode(self, client: TestClient, catalogue: FakeCatalogue):
        client.get(SEARCH, params={"q": "Dune", "kind": "episode"})

        assert catalogue.search_types == [("SHOW",)]

    def test_narrows_nothing_by_default(self, client: TestClient, catalogue: FakeCatalogue):
        client.get(SEARCH, params={"q": "Dune"})

        assert catalogue.search_types == [None]

    def test_a_kind_that_is_not_one_of_ours_is_rejected(self, client: TestClient):
        assert client.get(SEARCH, params={"q": "Dune", "kind": "show"}).status_code == 422

    def test_a_single_letter_is_rejected_before_it_costs_a_request(
        self, client: TestClient, catalogue: FakeCatalogue
    ):
        response = client.get(SEARCH, params={"q": "D"})

        assert response.status_code == 422
        assert catalogue.searched == []

    def test_a_box_holding_only_spaces_is_rejected_before_it_costs_a_request(
        self, client: TestClient, catalogue: FakeCatalogue
    ):
        """Long enough to pass a length check and still not a search. The rate
        limit is a second a request whatever is in the box."""
        response = client.get(SEARCH, params={"q": "   "})

        assert response.status_code == 422
        assert catalogue.searched == []

    def test_the_words_are_trimmed_before_being_asked_about(
        self, client: TestClient, catalogue: FakeCatalogue
    ):
        client.get(SEARCH, params={"q": "  Dune  "})

        assert catalogue.searched == ["Dune"]

    def test_a_catalogue_outage_is_a_bad_gateway(
        self, client: TestClient, catalogue: FakeCatalogue
    ):
        catalogue.results["Dune"] = JustWatchHttpError(503, "unavailable")

        response = client.get(SEARCH, params={"q": "Dune"})

        assert response.status_code == 502
        assert "try again" in response.json()["detail"]

    def test_finding_nothing_is_an_empty_list_rather_than_a_404(self, client: TestClient):
        """A search that matched nothing is a real answer, and the fix is to
        type something else rather than to treat the route as broken."""
        response = client.get(SEARCH, params={"q": "Nothing By This Name"})

        assert response.status_code == 200
        assert response.json() == []


class TestWhatWasDecidedByHand:
    def decide(self, client: TestClient, watched, title: str = "Dune") -> int:
        watched(title)
        client.post(RESOLVE)
        stored = resolution_id(client, title)
        client.put(f"/api/titles/resolutions/{stored}", json={"node_id": "tm21"})
        return stored

    def test_nothing_decided_yet_is_an_empty_list(self, client: TestClient):
        assert client.get(RESOLUTIONS).json() == []

    def test_lists_a_decision_somebody_made(self, client: TestClient, watched):
        stored = self.decide(client, watched)

        [decision] = client.get(RESOLUTIONS).json()

        assert decision["resolution_id"] == stored
        assert decision["query_title"] == "Dune"
        assert decision["title"] == "Dune"
        assert decision["release_year"] == 2021
        # The candidates are keyed by this, so it is what lets a client mark
        # which button is the one currently in force.
        assert decision["jw_node_id"] == "tm21"
        assert decision["jw_node_id"] in {c["node_id"] for c in decision["candidates"]}

    def test_carries_the_rejected_candidates_so_the_choice_can_be_made_again(
        self, client: TestClient, watched
    ):
        self.decide(client, watched)

        [decision] = client.get(RESOLUTIONS).json()

        assert {row["node_id"] for row in decision["candidates"]} == {"tm84", "tm21"}

    def test_leaves_out_what_the_matcher_decided_on_its_own(self, client: TestClient, watched):
        watched("Inception")
        client.post(RESOLVE)

        assert client.get(RESOLUTIONS).json() == []

    def test_a_decision_can_be_changed_and_the_list_follows(self, client: TestClient, watched):
        stored = self.decide(client, watched)

        client.put(f"/api/titles/resolutions/{stored}", json={"node_id": "tm84"})

        [decision] = client.get(RESOLUTIONS).json()
        assert decision["release_year"] == 1984

    def test_the_limit_is_honoured_rather_than_only_validated(self, client: TestClient, watched):
        """A route that validated the limit and then dropped it would pass every
        other test here."""
        self.decide(client, watched, "Dune")
        self.decide(client, watched, "Arrakis")

        assert len(client.get(RESOLUTIONS, params={"limit": 1}).json()) == 1
        assert len(client.get(RESOLUTIONS).json()) == 2

    def test_an_absurd_limit_is_rejected(self, client: TestClient):
        assert client.get(RESOLUTIONS, params={"limit": 0}).status_code == 422


class TestRefusingASecondPass:
    """Two passes at once were never faster, only twice as expensive.

    The JustWatch client paces every request at one a second behind a
    process-wide lock, so concurrent passes take turns rather than running in
    parallel. What each one does hold is a worker thread, out of the forty
    shared by every route in this app -- including `/health`. See
    `app/services/single_flight.py` for the measurements.
    """

    def test_a_second_pass_is_refused_while_one_is_running(self, client: TestClient):
        with budget.claim("resolve"):
            response = client.post(RESOLVE)

        assert response.status_code == 409

    def test_the_refusal_says_what_to_wait_for(self, client: TestClient):
        with budget.claim("refresh"):
            detail = client.post(RESOLVE).json()["detail"]

        assert "refresh" in detail

    def test_the_pass_runs_again_once_the_budget_is_free(self, client: TestClient):
        """The refusal must be about what is running now, not a latch that
        stays shut."""
        with budget.claim("resolve"):
            assert client.post(RESOLVE).status_code == 409

        assert client.post(RESOLVE).status_code == 200

    def test_the_budget_is_released_even_when_a_pass_blows_up(
        self, client: TestClient, catalogue: FakeCatalogue, watched
    ):
        """A pass that raised something it does not handle must not leave the
        endpoint refusing every caller until the process restarts. `RuntimeError`
        rather than a `JustWatchError`, which the pass contains and counts --
        the point here is the failure nothing was expecting."""
        watched("Inception")
        catalogue.results["Inception"] = RuntimeError("something nobody planned for")

        with pytest.raises(RuntimeError):
            client.post(RESOLVE)

        catalogue.results["Inception"] = [INCEPTION]
        assert client.post(RESOLVE).status_code == 200

    def test_an_absurd_limit_is_rejected_rather_than_quietly_clamped(self, client: TestClient):
        response = client.post(RESOLVE, params={"limit": MAX_REQUESTS_PER_PASS + 1})

        assert response.status_code == 422

    def test_the_largest_allowed_limit_is_accepted(self, client: TestClient):
        """The boundary from the side that must not be refused."""
        response = client.post(RESOLVE, params={"limit": MAX_REQUESTS_PER_PASS})

        assert response.status_code == 200

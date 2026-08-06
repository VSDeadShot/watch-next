"""Tests for the watchlist endpoints.

Through the real app with a real (in-memory) database. Nothing here touches the
network -- the watchlist points at titles the catalogue has already given us, so
none of these routes has any reason to make a request.

Most of what is worth checking at this layer is which failures are which. A
title that does not exist, an entry that is not on the list and a note that is
too long are three different mistakes with three different fixes, and collapsing
them into one status is how a frontend ends up showing "something went wrong".
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.main import app
from app.models import Title
from app.schemas import NOTE_LIMIT

WATCHLIST = "/api/watchlist"


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_settings] = lambda: Settings(jw_country="IN", jw_language="en")
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def titles(session: Session):
    counter = iter(range(1_000_000))

    def add_title(name: str = "Arrival", **extra) -> Title:
        title = Title(
            jw_node_id=f"tm{next(counter)}",
            object_type="MOVIE",
            title=name,
            genres=["scf"],
            release_year=2016,
            runtime_minutes=116,
            poster_url="https://img/poster.jpg",
            imdb_score=7.9,
            **extra,
        )
        session.add(title)
        session.flush()
        return title

    return add_title


class TestAdding:
    def test_puts_a_title_on_the_list(self, client: TestClient, titles):
        film = titles("Arrival")

        response = client.post(WATCHLIST, json={"title_id": film.id})

        assert response.status_code == 200
        assert response.json()["title"] == "Arrival"
        assert response.json()["watched_at"] is None

    def test_the_row_carries_enough_to_draw_it(self, client: TestClient, titles):
        """A watchlist page shows a poster and a year, and should not need a
        second request per row to get them."""
        film = titles("Arrival")

        body = client.post(WATCHLIST, json={"title_id": film.id}).json()

        assert body["poster_url"] == "https://img/poster.jpg"
        assert body["release_year"] == 2016
        assert body["runtime_minutes"] == 116
        assert body["genres"] == ["Science-Fiction"]

    def test_keeps_the_note(self, client: TestClient, titles):
        film = titles()

        body = client.post(WATCHLIST, json={"title_id": film.id, "note": "Ravi says so"}).json()

        assert body["note"] == "Ravi says so"

    def test_adding_twice_is_not_an_error(self, client: TestClient, titles):
        """The button is on the recommendation card, which somebody may well
        press again after a refresh."""
        film = titles()
        client.post(WATCHLIST, json={"title_id": film.id})

        response = client.post(WATCHLIST, json={"title_id": film.id})

        assert response.status_code == 200
        assert len(client.get(WATCHLIST).json()) == 1

    def test_a_title_we_do_not_have_is_a_404(self, client: TestClient):
        response = client.post(WATCHLIST, json={"title_id": 999})

        assert response.status_code == 404

    def test_a_note_longer_than_the_limit_is_refused(self, client: TestClient, titles):
        """Refused at the edge rather than stored: the column is Text and would
        take a novel without complaining."""
        film = titles()

        response = client.post(
            WATCHLIST, json={"title_id": film.id, "note": "x" * (NOTE_LIMIT + 1)}
        )

        assert response.status_code == 422


class TestReading:
    def test_an_empty_list_is_not_an_error(self, client: TestClient):
        response = client.get(WATCHLIST)

        assert response.status_code == 200
        assert response.json() == []

    def test_newest_first(self, client: TestClient, titles):
        client.post(WATCHLIST, json={"title_id": titles("Arrival").id})
        client.post(WATCHLIST, json={"title_id": titles("Heat").id})

        assert [item["title"] for item in client.get(WATCHLIST).json()] == ["Heat", "Arrival"]

    def test_ticked_off_entries_are_left_out(self, client: TestClient, titles):
        film = titles("Arrival")
        client.post(WATCHLIST, json={"title_id": film.id})
        client.patch(f"{WATCHLIST}/{film.id}", json={"watched": True})

        assert client.get(WATCHLIST).json() == []

    def test_ticked_off_entries_can_be_asked_for(self, client: TestClient, titles):
        film = titles("Arrival")
        client.post(WATCHLIST, json={"title_id": film.id})
        client.patch(f"{WATCHLIST}/{film.id}", json={"watched": True})

        listed = client.get(WATCHLIST, params={"include_watched": True}).json()

        assert [item["title"] for item in listed] == ["Arrival"]
        assert listed[0]["watched_at"] is not None


class TestChanging:
    def test_ticks_something_off(self, client: TestClient, titles):
        film = titles()
        client.post(WATCHLIST, json={"title_id": film.id})

        response = client.patch(f"{WATCHLIST}/{film.id}", json={"watched": True})

        assert response.status_code == 200
        assert response.json()["watched_at"] is not None

    def test_un_ticks_something(self, client: TestClient, titles):
        film = titles()
        client.post(WATCHLIST, json={"title_id": film.id})
        client.patch(f"{WATCHLIST}/{film.id}", json={"watched": True})

        response = client.patch(f"{WATCHLIST}/{film.id}", json={"watched": False})

        assert response.json()["watched_at"] is None

    def test_writes_a_note(self, client: TestClient, titles):
        film = titles()
        client.post(WATCHLIST, json={"title_id": film.id})

        response = client.patch(f"{WATCHLIST}/{film.id}", json={"note": "for the flight"})

        assert response.json()["note"] == "for the flight"

    def test_a_null_note_clears_it(self, client: TestClient, titles):
        film = titles()
        client.post(WATCHLIST, json={"title_id": film.id, "note": "for the flight"})

        response = client.patch(f"{WATCHLIST}/{film.id}", json={"note": None})

        assert response.json()["note"] is None

    def test_a_note_left_out_is_left_alone(self, client: TestClient, titles):
        """The difference between "clear this" and "I am not talking about it"
        is the whole reason this route reads what was sent rather than the
        value it arrived as."""
        film = titles()
        client.post(WATCHLIST, json={"title_id": film.id, "note": "for the flight"})

        response = client.patch(f"{WATCHLIST}/{film.id}", json={"watched": True})

        assert response.json()["note"] == "for the flight"

    def test_both_at_once(self, client: TestClient, titles):
        film = titles()
        client.post(WATCHLIST, json={"title_id": film.id})

        response = client.patch(
            f"{WATCHLIST}/{film.id}", json={"watched": True, "note": "on the plane"}
        )

        assert response.json()["note"] == "on the plane"
        assert response.json()["watched_at"] is not None

    def test_changing_nothing_returns_the_entry_unchanged(self, client: TestClient, titles):
        film = titles()
        client.post(WATCHLIST, json={"title_id": film.id, "note": "for the flight"})

        response = client.patch(f"{WATCHLIST}/{film.id}", json={})

        assert response.status_code == 200
        assert response.json()["note"] == "for the flight"

    def test_something_not_on_the_list_is_a_404(self, client: TestClient, titles):
        film = titles()

        response = client.patch(f"{WATCHLIST}/{film.id}", json={"watched": True})

        assert response.status_code == 404

    def test_a_note_longer_than_the_limit_is_refused(self, client: TestClient, titles):
        film = titles()
        client.post(WATCHLIST, json={"title_id": film.id})

        response = client.patch(f"{WATCHLIST}/{film.id}", json={"note": "x" * (NOTE_LIMIT + 1)})

        assert response.status_code == 422


class TestRemoving:
    def test_takes_it_off_the_list(self, client: TestClient, titles):
        film = titles()
        client.post(WATCHLIST, json={"title_id": film.id})

        response = client.delete(f"{WATCHLIST}/{film.id}")

        assert response.status_code == 204
        assert client.get(WATCHLIST).json() == []

    def test_removing_what_is_not_there_is_a_404(self, client: TestClient, titles):
        """The client thought it had this. Saying so is what stops a stale page
        quietly disagreeing with the list."""
        response = client.delete(f"{WATCHLIST}/{titles().id}")

        assert response.status_code == 404

    def test_the_title_itself_survives(self, client: TestClient, titles, session: Session):
        film = titles()
        client.post(WATCHLIST, json={"title_id": film.id})

        client.delete(f"{WATCHLIST}/{film.id}")

        assert session.get(Title, film.id) is not None


class TestWhereToWatchIt:
    """The point of the list. A row that does not say whether it can be watched
    is a row somebody has to go and check, which is the errand this app exists
    to run for them."""

    def test_a_row_says_where_to_press_play(
        self, client: TestClient, titles, offers, providers, subscribes
    ):
        providers("nfx", "Netflix")
        subscribes("nfx")
        film = titles("Arrival")
        offers(film, "nfx", url="https://netflix/watch/1")
        client.post(WATCHLIST, json={"title_id": film.id})

        [row] = client.get(WATCHLIST).json()

        assert row["watch_on"] == [
            {
                "short_name": "nfx",
                "name": "Netflix",
                "monetization": "FLATRATE",
                "url": "https://netflix/watch/1",
                "requires_subscription": True,
            }
        ]

    def test_something_on_no_service_of_theirs_says_so_by_saying_nothing(
        self, client: TestClient, titles, offers, subscribes
    ):
        """Empty is the honest answer and the one the page draws as "not on your
        services" -- the same meaning it has on a recommendation."""
        subscribes("nfx")
        film = titles("Arrival")
        offers(film, "prv")
        client.post(WATCHLIST, json={"title_id": film.id})

        [row] = client.get(WATCHLIST).json()

        assert row["watch_on"] == []

    def test_free_to_everybody_counts(self, client: TestClient, titles, offers, providers):
        providers("yt", "YouTube")
        film = titles("Arrival")
        offers(film, "yt", "ADS")
        client.post(WATCHLIST, json={"title_id": film.id})

        [row] = client.get(WATCHLIST).json()

        assert row["watch_on"][0]["requires_subscription"] is False

    def test_the_answer_arrives_with_the_entry_that_was_just_added(
        self, client: TestClient, titles, offers, providers, subscribes
    ):
        """The recommendation card saves a title and draws the reply. Leaving
        availability off that one response would blank a row that had it a
        moment earlier."""
        providers("nfx", "Netflix")
        subscribes("nfx")
        film = titles("Arrival")
        offers(film, "nfx")

        body = client.post(WATCHLIST, json={"title_id": film.id}).json()

        assert [option["name"] for option in body["watch_on"]] == ["Netflix"]

    def test_the_answer_survives_a_change(
        self, client: TestClient, titles, offers, providers, subscribes
    ):
        providers("nfx", "Netflix")
        subscribes("nfx")
        film = titles("Arrival")
        offers(film, "nfx")
        client.post(WATCHLIST, json={"title_id": film.id})

        body = client.patch(f"{WATCHLIST}/{film.id}", json={"note": "for Sunday"}).json()

        assert [option["name"] for option in body["watch_on"]] == ["Netflix"]

    def test_a_long_list_costs_no_more_queries_than_a_short_one(
        self, client: TestClient, counting, titles, offers, providers, subscribes
    ):
        """The reason the lookup is batched. This is the page that gets slower
        the more use it gets, if anything is going to -- so what is asserted is
        that the cost does not move with the length, rather than some number
        that happens to be true today."""
        providers("nfx", "Netflix")
        subscribes("nfx")

        def add(count: int) -> None:
            for _ in range(count):
                film = titles("Film")
                offers(film, "nfx")
                client.post(WATCHLIST, json={"title_id": film.id})

        add(3)
        with counting() as few:
            assert len(client.get(WATCHLIST).json()) == 3

        add(22)
        with counting() as many:
            assert len(client.get(WATCHLIST).json()) == 25

        assert len(many) == len(few)

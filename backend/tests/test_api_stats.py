"""Tests for the stats endpoint.

Through the real app with a real (in-memory) database, and nothing here touches
the network -- this route is the whole app read back, and reading it back should
never cost a request against an unofficial API.

The counting is proved in ``test_stats.py`` and the fetching in
``test_stats_service.py``. What is left for this layer is the contract: that a
history nobody has imported yet is still a 200, that genres arrive in English,
and that the one number the page must not omit -- what could not be counted --
is actually in the body.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.taste import MOVIE, SHOW
from app.db import get_db
from app.main import app
from app.models import DEFAULT_USER_ID, ImportRun, Title, YouTubeView

STATS = "/api/stats"


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
        extra.setdefault("object_type", MOVIE)
        extra.setdefault("genres", ["scf"])
        extra.setdefault("release_year", 2016)
        title = Title(
            jw_node_id=f"tm{next(counter)}",
            title=name,
            runtime_minutes=116,
            **extra,
        )
        session.add(title)
        session.flush()
        return title

    return add_title


@pytest.fixture
def viewed(session: Session):
    run = ImportRun(user_id=DEFAULT_USER_ID, source="youtube", export_format="json")
    session.add(run)
    session.flush()
    counter = iter(range(1_000_000))

    def add(channel: str | None = "Tom Scott", *, video_id: str = "abc") -> YouTubeView:
        view = YouTubeView(
            user_id=DEFAULT_USER_ID,
            import_id=run.id,
            fingerprint=f"yt{next(counter)}",
            video_id=video_id,
            title="A video",
            channel_name=channel,
            watched_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
        session.add(view)
        session.flush()
        return view

    return add


class TestBeforeAnythingIsImported:
    """The likeliest first visit, and not an error."""

    def test_answers_rather_than_failing(self, client: TestClient):
        response = client.get(STATS)

        assert response.status_code == 200

    def test_every_section_is_present_and_empty(self, client: TestClient):
        body = client.get(STATS).json()

        assert body["history"]["sessions"] == 0
        assert body["history"]["by_month"] == []
        assert body["youtube"]["views"] == 0
        assert body["unresolved_sessions"] == 0

    def test_time_watched_is_null_rather_than_zero(self, client: TestClient):
        assert client.get(STATS).json()["history"]["minutes_watched"] is None


class TestTheHistory:
    def test_counts_titles_and_sessions_separately(self, client: TestClient, titles, watched):
        show = titles("Fargo", object_type=SHOW)
        for _ in range(5):
            watched("Fargo", title_id=show.id)

        history = client.get(STATS).json()["history"]

        assert history["titles"] == 1
        assert history["sessions"] == 5
        assert history["series"] == 1
        assert history["movies"] == 0

    def test_genres_arrive_in_english(self, client: TestClient, titles, watched):
        """A client given "scf" can only print it or keep a stale copy of our
        table. See api/recommend.py."""
        film = titles("Arrival", genres=["scf"])
        watched("Arrival", title_id=film.id)

        history = client.get(STATS).json()["history"]

        assert [entry["label"] for entry in history["top_genres"]] == ["Science-Fiction"]

    def test_reports_the_time_that_was_measured(self, client: TestClient, titles, watched):
        film = titles("Arrival")
        watched("Arrival", title_id=film.id, duration_seconds=6000)

        history = client.get(STATS).json()["history"]

        assert history["minutes_watched"] == 100
        assert history["sessions_timed"] == 1

    def test_says_which_kind_each_ranked_title_is(self, client: TestClient, titles, watched):
        show = titles("Fargo", object_type=SHOW)
        watched("Fargo", title_id=show.id)

        top = client.get(STATS).json()["history"]["top_titles"][0]

        assert top == {
            "title_id": show.id,
            "title": "Fargo",
            "object_type": SHOW,
            "sessions": 1,
        }

    def test_months_are_dated_and_keep_their_gaps(self, client: TestClient, titles, watched):
        film = titles("Arrival")
        watched("Arrival", title_id=film.id, watched_at=datetime(2026, 1, 5, tzinfo=UTC))
        watched("Arrival", title_id=film.id, watched_at=datetime(2026, 3, 5, tzinfo=UTC))

        by_month = client.get(STATS).json()["history"]["by_month"]

        assert [entry["month"] for entry in by_month] == ["2026-01-01", "2026-02-01", "2026-03-01"]
        assert [entry["count"] for entry in by_month] == [1, 0, 1]

    def test_decades_are_labelled_and_chronological(self, client: TestClient, titles, watched):
        old = titles("Alien", release_year=1979)
        new = titles("Arrival", release_year=2016)
        watched("Alien", title_id=old.id)
        watched("Arrival", title_id=new.id)

        decades = client.get(STATS).json()["history"]["decades"]

        assert [entry["label"] for entry in decades] == ["1970", "2010"]


class TestWhatCouldNotBeCounted:
    def test_unresolved_rows_are_in_the_body(self, client: TestClient, titles, watched):
        film = titles("Arrival")
        watched("Arrival", title_id=film.id)
        watched("Something The Matcher Refused")

        body = client.get(STATS).json()

        assert body["history"]["sessions"] == 1
        assert body["unresolved_sessions"] == 1


class TestYouTube:
    def test_is_reported_beside_the_rest_rather_than_inside_it(
        self, client: TestClient, titles, watched, viewed
    ):
        film = titles("Arrival")
        watched("Arrival", title_id=film.id)
        viewed("Tom Scott")

        body = client.get(STATS).json()

        assert body["history"]["sessions"] == 1
        assert body["youtube"]["views"] == 1

    def test_names_the_channels(self, client: TestClient, viewed):
        viewed("Tom Scott", video_id="a")
        viewed("Tom Scott", video_id="b")
        viewed("Veritasium", video_id="c")

        youtube = client.get(STATS).json()["youtube"]

        assert youtube["top_channels"] == [
            {"label": "Tom Scott", "count": 2},
            {"label": "Veritasium", "count": 1},
        ]
        assert youtube["videos"] == 3

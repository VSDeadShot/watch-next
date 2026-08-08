"""Tests for reading both histories out of the database to be counted.

The counting itself is tested in ``test_stats.py`` without a database anywhere
near it. What is worth proving here is the fetching: that the join does not
quietly drop a history, that one person's viewing is not counted into another's,
that unresolved rows are reported rather than silently missing, and that a
longer history is not more queries.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.core.taste import MOVIE, SHOW
from app.models import DEFAULT_USER_ID, ImportRun, Title, YouTubeView
from app.services.stats import overview


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
    """Store one YouTube view, the way the importer would have."""
    run = ImportRun(user_id=DEFAULT_USER_ID, source="youtube", export_format="json")
    session.add(run)
    session.flush()
    counter = iter(range(1_000_000))

    def add(
        channel: str | None = "Tom Scott",
        *,
        video_id: str = "abc",
        when: datetime | None = None,
        user_id: str = DEFAULT_USER_ID,
    ) -> YouTubeView:
        view = YouTubeView(
            user_id=user_id,
            import_id=run.id,
            fingerprint=f"yt{next(counter)}",
            video_id=video_id,
            title="A video",
            channel_name=channel,
            watched_at=when or datetime(2026, 1, 5, tzinfo=UTC),
        )
        session.add(view)
        session.flush()
        return view

    return add


class TestNothingImportedYet:
    """The page loads before the first import, so this is the ordinary case."""

    def test_is_not_an_error(self, session: Session):
        found = overview(session)

        assert found.history.sessions == 0
        assert found.youtube.views == 0
        assert found.unresolved_sessions == 0

    def test_reports_no_time_rather_than_no_minutes(self, session: Session):
        assert overview(session).history.minutes_watched is None


class TestReadingTheHistory:
    def test_counts_what_was_watched(self, session: Session, titles, watched):
        film = titles("Arrival")
        watched("Arrival", title_id=film.id)
        watched("Arrival", title_id=film.id)

        found = overview(session)

        assert found.history.titles == 1
        assert found.history.sessions == 2

    def test_brings_the_catalogue_metadata_with_it(self, session: Session, titles, watched):
        show = titles("Fargo", object_type=SHOW, genres=["crm", "drm"], release_year=2014)
        watched("Fargo", title_id=show.id)

        found = overview(session)

        assert found.history.series == 1
        assert {entry.label for entry in found.history.top_genres} == {"crm", "drm"}
        assert [entry.label for entry in found.history.decades] == ["2010"]

    def test_carries_the_measured_duration(self, session: Session, titles, watched):
        film = titles("Arrival")
        watched("Arrival", title_id=film.id, duration_seconds=6000)

        found = overview(session)

        assert found.history.minutes_watched == 100
        assert found.history.sessions_timed == 1

    def test_uses_the_time_it_was_watched_not_the_time_it_was_imported(
        self, session: Session, titles, watched
    ):
        film = titles("Arrival")
        watched("Arrival", title_id=film.id, watched_at=datetime(2021, 7, 4, tzinfo=UTC))

        found = overview(session)

        assert found.history.first_watched == datetime(2021, 7, 4, tzinfo=UTC)

    def test_names_the_titles_it_ranks(self, session: Session, titles, watched):
        film = titles("Arrival")
        watched("Arrival", title_id=film.id)

        top = overview(session).history.top_titles[0]

        assert top.title == "Arrival"
        assert top.title_id == film.id
        assert top.object_type == MOVIE

    def test_passes_the_limit_through(self, session: Session, titles, watched):
        for index in range(6):
            film = titles(f"Film {index}", genres=[f"g{index}"])
            watched(f"Film {index}", title_id=film.id)

        found = overview(session, top=2)

        assert len(found.history.top_titles) == 2
        assert len(found.history.top_genres) == 2


class TestWhatCouldNotBeCounted:
    def test_an_unresolved_event_is_reported_rather_than_left_out_quietly(
        self, session: Session, titles, watched
    ):
        film = titles("Arrival")
        watched("Arrival", title_id=film.id)
        watched("Something The Matcher Refused")

        found = overview(session)

        assert found.history.sessions == 1
        assert found.unresolved_sessions == 1

    def test_a_fully_resolved_library_has_nothing_outstanding(
        self, session: Session, titles, watched
    ):
        film = titles("Arrival")
        watched("Arrival", title_id=film.id)

        assert overview(session).unresolved_sessions == 0


class TestOnePersonAtATime:
    def test_another_persons_viewing_is_not_counted_in(self, session: Session, titles, watched):
        film = titles("Arrival")
        watched("Arrival", title_id=film.id)
        watched("Arrival", title_id=film.id, user_id="somebody-else")

        assert overview(session).history.sessions == 1

    def test_and_can_be_asked_for_on_their_own_behalf(self, session: Session, titles, watched):
        film = titles("Arrival")
        watched("Arrival", title_id=film.id)
        watched("Arrival", title_id=film.id, user_id="somebody-else")

        assert overview(session, user_id="somebody-else").history.sessions == 1

    def test_another_persons_unresolved_rows_are_not_counted_in(self, session: Session, watched):
        watched("Unmatched")
        watched("Unmatched", user_id="somebody-else")

        assert overview(session).unresolved_sessions == 1

    def test_another_persons_youtube_is_not_counted_in(self, session: Session, viewed):
        viewed("Tom Scott")
        viewed("Tom Scott", user_id="somebody-else")

        assert overview(session).youtube.views == 1


class TestReadingYouTube:
    def test_counts_views_and_channels(self, session: Session, viewed):
        viewed("Tom Scott", video_id="a")
        viewed("Tom Scott", video_id="a")
        viewed("Veritasium", video_id="b")

        found = overview(session).youtube

        assert found.views == 3
        assert found.videos == 2
        assert found.channels == 2

    def test_ranks_the_channels(self, session: Session, viewed):
        viewed("Tom Scott", video_id="a")
        viewed("Tom Scott", video_id="b")
        viewed("Veritasium", video_id="c")

        assert overview(session).youtube.top_channels[0].label == "Tom Scott"

    def test_youtube_is_not_mixed_into_the_watch_history(
        self, session: Session, titles, watched, viewed
    ):
        """The tables are separate on purpose; so are the numbers."""
        film = titles("Arrival")
        watched("Arrival", title_id=film.id)
        viewed("Tom Scott")

        found = overview(session)

        assert found.history.sessions == 1
        assert found.youtube.views == 1


class TestTheCostOfAsking:
    def test_a_longer_history_is_not_more_queries(
        self, session: Session, titles, watched, viewed, counting
    ):
        film = titles("Arrival")
        watched("Arrival", title_id=film.id)
        viewed("Tom Scott")

        with counting() as few:
            overview(session)

        for index in range(40):
            other = titles(f"Film {index}")
            watched(f"Film {index}", title_id=other.id)
            viewed(f"Channel {index}", video_id=f"v{index}")

        with counting() as many:
            overview(session)

        # Non-empty first. The rows are read through generators, and a refactor
        # that let one escape the call would be counted as nothing at all --
        # which would make the real assertion below pass for the wrong reason.
        assert few
        assert len(many) == len(few)

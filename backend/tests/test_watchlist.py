"""Tests for the one place the app is told something rather than guessing it.

Everything else about a person here is inferred from what they did. A watchlist
entry is stated outright, so the rules around it are about not losing it and not
quietly reinterpreting it: an add that happens twice must not make two entries or
reorder the list, a note must survive an add that did not mention it, and saying
"I have seen this" must not be confused with "I no longer want this".
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import DEFAULT_USER_ID, Title, WatchlistItem
from app.services.watchlist import (
    TitleNotInCatalogue,
    WatchlistItemNotFound,
    add,
    entries,
    pending_ids,
    remove,
    set_note,
    set_watched,
)

NOW = datetime(2026, 8, 4, 20, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=3)

OTHER_USER = "someone-else"


@pytest.fixture
def titles(session: Session):
    """Catalogue rows to point a watchlist entry at."""
    counter = iter(range(1_000_000))

    def add_title(name: str = "A Film", **extra) -> Title:
        title = Title(
            jw_node_id=f"tm{next(counter)}",
            object_type="MOVIE",
            title=name,
            genres=["cmy"],
            **extra,
        )
        session.add(title)
        session.flush()
        return title

    return add_title


def count_queries(session: Session) -> list[str]:
    """Record every statement the session sends from here on."""
    statements: list[str] = []

    @event.listens_for(session.get_bind(), "after_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    return statements


class TestAdding:
    def test_puts_a_title_on_the_list(self, session: Session, titles):
        film = titles("Arrival")

        item = add(session, film.id, now=NOW)

        assert item.title_id == film.id
        assert item.added_at == NOW
        assert item.watched_at is None

    def test_keeps_the_note(self, session: Session, titles):
        film = titles()

        item = add(session, film.id, note="Ravi keeps going on about this", now=NOW)

        assert item.note == "Ravi keeps going on about this"

    def test_a_blank_note_is_no_note(self, session: Session, titles):
        """Whitespace from an empty text box is not a reason somebody wrote."""
        film = titles()

        item = add(session, film.id, note="   ", now=NOW)

        assert item.note is None

    def test_adding_twice_does_not_make_two_entries(self, session: Session, titles):
        film = titles()

        add(session, film.id, now=NOW)
        add(session, film.id, now=LATER)

        assert len(session.scalars(select(WatchlistItem)).all()) == 1

    def test_adding_again_does_not_move_it_up_the_list(self, session: Session, titles):
        """Pressing the button twice is not a new decision, and the list is
        ordered by when the decision was made."""
        film = titles()

        add(session, film.id, now=NOW)
        again = add(session, film.id, now=LATER)

        assert again.added_at == NOW

    def test_adding_again_without_a_note_keeps_the_one_already_there(
        self, session: Session, titles
    ):
        film = titles()
        add(session, film.id, note="for the flight", now=NOW)

        again = add(session, film.id, now=LATER)

        assert again.note == "for the flight"

    def test_adding_again_with_a_note_replaces_it(self, session: Session, titles):
        film = titles()
        add(session, film.id, note="for the flight", now=NOW)

        again = add(session, film.id, note="Ravi says watch it sober", now=LATER)

        assert again.note == "Ravi says watch it sober"

    def test_adding_something_ticked_off_puts_it_back(self, session: Session, titles):
        """Wanting to see something again is a new decision, not the old one."""
        film = titles()
        add(session, film.id, now=NOW)
        set_watched(session, film.id, watched=True, now=NOW)

        again = add(session, film.id, now=LATER)

        assert again.watched_at is None
        assert again.added_at == LATER

    def test_refuses_a_title_the_catalogue_has_never_heard_of(self, session: Session):
        with pytest.raises(TitleNotInCatalogue):
            add(session, 404, now=NOW)

    def test_nothing_is_written_when_the_title_is_unknown(self, session: Session):
        with pytest.raises(TitleNotInCatalogue):
            add(session, 404, now=NOW)

        assert session.scalars(select(WatchlistItem)).all() == []

    def test_one_persons_list_is_not_anothers(self, session: Session, titles):
        film = titles()

        add(session, film.id, now=NOW)
        add(session, film.id, now=NOW, user_id=OTHER_USER)

        assert len(entries(session)) == 1
        assert len(entries(session, user_id=OTHER_USER)) == 1


class TestReading:
    def test_lists_what_is_waiting(self, session: Session, titles):
        add(session, titles("Arrival").id, now=NOW)
        add(session, titles("Heat").id, now=NOW)

        assert {item.title.title for item in entries(session)} == {"Arrival", "Heat"}

    def test_newest_first(self, session: Session, titles):
        """A list somebody keeps adding to is read from the top."""
        add(session, titles("Arrival").id, now=NOW)
        add(session, titles("Heat").id, now=LATER)

        assert [item.title.title for item in entries(session)] == ["Heat", "Arrival"]

    def test_two_added_at_once_still_have_an_order(self, session: Session, titles):
        """Identical timestamps must not leave the order to the database."""
        first = add(session, titles("Arrival").id, now=NOW)
        second = add(session, titles("Heat").id, now=NOW)

        assert [item.id for item in entries(session)] == [second.id, first.id]

    def test_the_order_does_not_depend_on_the_database(self, session: Session, titles):
        """The test above cannot see this on its own.

        SQLite happens to return tied rows in the order we want, so dropping the
        tiebreak passes every behavioural test here and then reorders the list on
        Postgres, where an unstable sort is free to return them either way. The
        only place the invariant is visible from here is the statement itself.
        """
        add(session, titles("Arrival").id, now=NOW)

        statements = count_queries(session)
        entries(session)

        ordering = statements[0].split("ORDER BY")[-1]
        assert "watchlist.id" in ordering

    def test_ticked_off_items_are_left_out_by_default(self, session: Session, titles):
        film = titles("Arrival")
        add(session, film.id, now=NOW)
        add(session, titles("Heat").id, now=NOW)
        set_watched(session, film.id, watched=True, now=LATER)

        assert [item.title.title for item in entries(session)] == ["Heat"]

    def test_ticked_off_items_can_be_asked_for(self, session: Session, titles):
        film = titles("Arrival")
        add(session, film.id, now=NOW)
        set_watched(session, film.id, watched=True, now=LATER)

        assert [item.title.title for item in entries(session, include_watched=True)] == ["Arrival"]

    def test_an_empty_list_is_not_an_error(self, session: Session):
        assert entries(session) == []

    def test_the_titles_come_back_in_one_query(self, session: Session, titles):
        """A watchlist page shows the poster and the year of every row. Reading
        those one title at a time is fine at three entries and absurd at three
        hundred, and the difference is invisible until somebody has a long list.
        """
        for name in ("Arrival", "Heat", "Dune", "Fargo", "Alien"):
            add(session, titles(name).id, now=NOW)
        session.expunge_all()

        statements = count_queries(session)
        # Reading every title is what a page does, and what would give a lazy
        # relationship five more round trips.
        assert len({item.title.title for item in entries(session)}) == 5

        assert len(statements) <= 2


class TestTickingOff:
    def test_marks_it_watched(self, session: Session, titles):
        film = titles()
        add(session, film.id, now=NOW)

        item = set_watched(session, film.id, watched=True, now=LATER)

        assert item.watched_at == LATER

    def test_it_can_be_un_ticked(self, session: Session, titles):
        film = titles()
        add(session, film.id, now=NOW)
        set_watched(session, film.id, watched=True, now=LATER)

        item = set_watched(session, film.id, watched=False, now=LATER)

        assert item.watched_at is None

    def test_ticking_it_off_twice_does_not_move_the_date(self, session: Session, titles):
        film = titles()
        add(session, film.id, now=NOW)
        set_watched(session, film.id, watched=True, now=LATER)

        item = set_watched(session, film.id, watched=True, now=LATER + timedelta(days=1))

        assert item.watched_at == LATER

    def test_ticking_off_something_not_on_the_list_says_so(self, session: Session, titles):
        film = titles()

        with pytest.raises(WatchlistItemNotFound):
            set_watched(session, film.id, watched=True, now=NOW)


class TestNotes:
    def test_sets_a_note(self, session: Session, titles):
        film = titles()
        add(session, film.id, now=NOW)

        item = set_note(session, film.id, note="for the flight")

        assert item.note == "for the flight"

    def test_clears_a_note(self, session: Session, titles):
        film = titles()
        add(session, film.id, note="for the flight", now=NOW)

        item = set_note(session, film.id, note=None)

        assert item.note is None

    def test_noting_something_not_on_the_list_says_so(self, session: Session, titles):
        film = titles()

        with pytest.raises(WatchlistItemNotFound):
            set_note(session, film.id, note="hello")


class TestRemoving:
    def test_takes_it_off_the_list(self, session: Session, titles):
        film = titles()
        add(session, film.id, now=NOW)

        assert remove(session, film.id) is True
        assert entries(session) == []

    def test_removing_what_is_not_there_says_so(self, session: Session, titles):
        assert remove(session, titles().id) is False

    def test_removing_leaves_the_catalogue_alone(self, session: Session, titles):
        """The list is the user's; the title is JustWatch's and is shared."""
        film = titles()
        add(session, film.id, now=NOW)

        remove(session, film.id)

        assert session.get(Title, film.id) is not None

    def test_removing_cannot_reach_somebody_elses_list(self, session: Session, titles):
        """The only destructive operation here, so the scoping is worth pinning
        separately from the reads."""
        film = titles()
        add(session, film.id, now=NOW, user_id=OTHER_USER)

        assert remove(session, film.id) is False
        assert len(entries(session, user_id=OTHER_USER)) == 1

    def test_removing_takes_a_ticked_off_entry_too(self, session: Session, titles):
        film = titles()
        add(session, film.id, now=NOW)
        set_watched(session, film.id, watched=True, now=LATER)

        assert remove(session, film.id) is True
        assert entries(session, include_watched=True) == []


class TestWhatTheRecommenderAsks:
    def test_says_which_titles_are_waiting(self, session: Session, titles):
        wanted = titles("Arrival")
        add(session, wanted.id, now=NOW)
        titles("Heat")

        assert pending_ids(session) == {wanted.id}

    def test_a_ticked_off_title_is_no_longer_waiting(self, session: Session, titles):
        """It has been seen. Wanting it and having watched it are opposites, and
        a bonus for something already watched would be exactly wrong."""
        film = titles()
        add(session, film.id, now=NOW)
        set_watched(session, film.id, watched=True, now=LATER)

        assert pending_ids(session) == set()

    def test_nothing_on_the_list_is_an_empty_answer(self, session: Session):
        assert pending_ids(session) == set()

    def test_it_is_one_query(self, session: Session, titles):
        for name in ("Arrival", "Heat", "Dune"):
            add(session, titles(name).id, now=NOW)
        session.expunge_all()

        statements = count_queries(session)
        pending_ids(session)

        assert len(statements) == 1

    def test_somebody_elses_list_does_not_count(self, session: Session, titles):
        add(session, titles().id, now=NOW, user_id=OTHER_USER)

        assert pending_ids(session) == set()
        assert pending_ids(session, user_id=OTHER_USER) != set()


class TestDefaultUser:
    def test_the_list_belongs_to_the_default_user_unless_told_otherwise(
        self, session: Session, titles
    ):
        item = add(session, titles().id, now=NOW)

        assert item.user_id == DEFAULT_USER_ID

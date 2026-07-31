"""Tests for turning stored watch events into catalogue links.

No test here touches the network. The resolver takes a catalogue client as an
argument and these pass in a fake, which is what makes it possible to describe
things a live API could never be asked to reproduce on demand -- a search that
times out, a title that comes back ambiguous, a hundred episodes of one show.

The recurring theme is restraint. Resolution costs someone else's requests, so
asking twice for the same answer is a defect, and so is asking again for an
answer a person already gave by hand.
"""

import pytest
from simplejustwatchapi.exceptions import JustWatchApiError, JustWatchHttpError
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.matching import MatchMethod
from app.models import Title, TitleResolution, WatchEvent
from app.services.justwatch_client import CatalogueEntry
from app.services.resolver import (
    ResolutionNotFound,
    resolve_library,
    resolve_manually,
    unresolved_titles,
)

INCEPTION = CatalogueEntry(
    node_id="tm1",
    title="Inception",
    object_type="MOVIE",
    release_year=2010,
    runtime_minutes=148,
    genres=("act", "scf"),
    imdb_id="tt1375666",
    imdb_score=8.8,
)
THE_OFFICE = CatalogueEntry(
    node_id="ts1",
    title="The Office",
    object_type="SHOW",
    release_year=2005,
    genres=("cmy",),
)
DUNE_1984 = CatalogueEntry(node_id="tm84", title="Dune", object_type="MOVIE", release_year=1984)
DUNE_2021 = CatalogueEntry(node_id="tm21", title="Dune", object_type="MOVIE", release_year=2021)


class FakeCatalogue:
    """A catalogue that answers from a script instead of from the internet.

    ``results`` maps a search term to what comes back; an exception value is
    raised instead. Every search is recorded, because "how many times did we
    ask" is the behaviour several of these tests are actually about.
    """

    def __init__(self, results: dict | None = None, default: list | None = None):
        self.results = results or {}
        self.default = default if default is not None else []
        self.searched: list[str] = []

    def search(self, title: str, *, object_types=None) -> list[CatalogueEntry]:
        self.searched.append(title)
        outcome = self.results.get(title, self.default)
        if isinstance(outcome, Exception):
            raise outcome
        return list(outcome)


class EchoCatalogue:
    """Answers every search with an entry that matches it exactly.

    For tests about how much work resolution does rather than what it decides:
    every title resolves, so the whole path runs for every question.
    """

    def __init__(self):
        self.searched: list[str] = []

    def search(self, title: str, *, object_types=None) -> list[CatalogueEntry]:
        self.searched.append(title)
        return [CatalogueEntry(node_id=f"node-{title}", title=title, object_type="SHOW")]


class FakeLookup:
    """A by-id catalogue lookup, scripted the same way.

    Separate from :class:`FakeCatalogue` because the code under test is
    separate: an automatic pass only ever searches, and a manual fix only ever
    looks one id up.
    """

    def __init__(self, entries: dict | None = None):
        self.entries = entries or {}
        self.looked_up: list[str] = []

    def details(self, node_id: str) -> CatalogueEntry:
        self.looked_up.append(node_id)
        outcome = self.entries.get(node_id)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            raise JustWatchApiError([{"message": "no such node", "code": "NOT_FOUND"}])
        return outcome


class TestLinkingTitles:
    def test_a_confident_match_links_the_watch_event(self, session: Session, watched):
        event = watched("Inception")
        catalogue = FakeCatalogue({"Inception": [INCEPTION]})

        resolve_library(session, catalogue)

        session.refresh(event)
        assert event.title_id is not None
        assert event.catalogue_title.jw_node_id == "tm1"

    def test_the_catalogue_details_are_stored(self, session: Session, watched):
        watched("Inception")
        catalogue = FakeCatalogue({"Inception": [INCEPTION]})

        resolve_library(session, catalogue)

        title = session.scalars(select(Title)).one()
        assert title.release_year == 2010
        assert title.runtime_minutes == 148
        assert title.genres == ["act", "scf"]
        assert title.imdb_id == "tt1375666"
        assert title.imdb_score == 8.8

    def test_the_resolution_is_recorded_with_its_method(self, session: Session, watched):
        watched("Inception")

        resolve_library(session, FakeCatalogue({"Inception": [INCEPTION]}))

        resolution = session.scalars(select(TitleResolution)).one()
        assert resolution.method == MatchMethod.EXACT
        assert resolution.confidence > 0
        assert resolution.title_id is not None

    def test_the_summary_counts_what_happened(self, session: Session, watched):
        watched("Inception")
        watched("Nothing Like This", kind="movie")

        summary = resolve_library(session, FakeCatalogue({"Inception": [INCEPTION]}))

        assert summary.resolved == 1
        assert summary.unresolved == 1
        assert summary.searched == 2


class TestAsksOnce:
    def test_many_episodes_of_one_show_cause_one_search(self, session: Session, watched):
        """The reason resolution is keyed by title rather than by row.

        A season of television is forty rows asking the same question, and
        asking an unofficial API forty times for one answer is how a client
        earns a block.
        """
        for episode in range(40):
            watched("The Office", kind="episode", episode_number=episode)
        catalogue = FakeCatalogue({"The Office": [THE_OFFICE]})

        resolve_library(session, catalogue)

        assert catalogue.searched == ["The Office"]

    def test_every_episode_still_gets_linked(self, session: Session, watched):
        for episode in range(40):
            watched("The Office", kind="episode", episode_number=episode)

        resolve_library(session, FakeCatalogue({"The Office": [THE_OFFICE]}))

        linked = session.scalars(select(WatchEvent).where(WatchEvent.title_id.is_not(None)))
        assert len(list(linked)) == 40

    def test_a_second_run_searches_nothing(self, session: Session, watched):
        watched("Inception")
        catalogue = FakeCatalogue({"Inception": [INCEPTION]})

        resolve_library(session, catalogue)
        resolve_library(session, catalogue)

        assert catalogue.searched == ["Inception"]

    def test_a_cached_answer_links_newly_imported_events(self, session: Session, watched):
        """The cache has to serve rows that did not exist when it was filled --
        otherwise every fresh import re-asks for titles already known."""
        watched("Inception")
        catalogue = FakeCatalogue({"Inception": [INCEPTION]})
        resolve_library(session, catalogue)

        later = watched("Inception")
        resolve_library(session, catalogue)

        session.refresh(later)
        assert later.title_id is not None
        assert catalogue.searched == ["Inception"]

    def test_titles_differing_only_cosmetically_share_one_lookup(self, session: Session, watched):
        """ "The Office (U.S.)" and "The Office" are one question, so the cache
        key is the normalised title rather than the exported string."""
        watched("The Office (U.S.)", kind="episode")
        watched("The Office", kind="episode")
        catalogue = FakeCatalogue(default=[THE_OFFICE])

        resolve_library(session, catalogue)

        assert len(catalogue.searched) == 1

    def test_one_catalogue_row_serves_every_event_that_matched_it(self, session: Session, watched):
        watched("The Office (U.S.)", kind="episode")
        watched("The Office", kind="episode")

        resolve_library(session, FakeCatalogue(default=[THE_OFFICE]))

        assert len(list(session.scalars(select(Title)))) == 1

    def test_the_same_name_as_a_film_and_a_show_are_different_questions(
        self, session: Session, watched
    ):
        """ "Fargo" the film and "Fargo" the series share a string and have
        different answers, so the kind is part of the cache key."""
        watched("Fargo", kind="movie")
        watched("Fargo", kind="episode")

        resolve_library(session, FakeCatalogue(default=[]))

        assert len(session.scalars(select(TitleResolution)).all()) == 2


class TestCostOfALargeLibrary:
    """Resolution has to walk everything, so it must not walk it repeatedly.

    The obvious implementation asks "which events wanted this answer?" once per
    title, which reads the whole library once per title. On a real history --
    a few hundred titles over a few thousand rows -- that is quadratic, and it
    showed up as half a minute of database work before a single request went
    out. These pin the shape rather than a timing, which is what actually
    regresses.
    """

    def _library(self, watched, titles: int, *, first: int = 0) -> None:
        for index in range(first, first + titles):
            for episode in range(5):
                watched(f"Show {index}", kind="episode", episode_number=episode)

    def _select_count(self, session: Session, catalogue) -> int:
        statements: list[str] = []

        @event.listens_for(session.get_bind(), "before_cursor_execute")
        def record(conn, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        try:
            resolve_library(session, catalogue)
        finally:
            event.remove(session.get_bind(), "before_cursor_execute", record)
        return len(statements)

    def test_reading_the_library_does_not_repeat_per_title(self, session: Session, watched):
        """Ten times the titles must not mean ten times the queries.

        The catalogue answers every search, because it is resolving a title --
        not refusing one -- that triggers the work of finding the events waiting
        on the answer.
        """
        self._library(watched, titles=3)
        small = self._select_count(session, EchoCatalogue())

        self._library(watched, titles=30, first=100)
        session.commit()
        large = self._select_count(session, EchoCatalogue())

        assert large <= small + 1

    def test_every_event_is_still_linked(self, session: Session, watched):
        """The cheap version must not become the wrong version."""
        self._library(watched, titles=3)

        resolve_library(session, EchoCatalogue())

        linked = session.scalars(select(WatchEvent).where(WatchEvent.title_id.is_not(None))).all()
        assert len(linked) == 15

    def test_each_title_gets_its_own_catalogue_row(self, session: Session, watched):
        """The bulk update must not point every event at one title."""
        self._library(watched, titles=3)

        resolve_library(session, EchoCatalogue())

        by_title = {event.catalogue_title.title for event in session.scalars(select(WatchEvent))}
        assert by_title == {"Show 0", "Show 1", "Show 2"}


class TestRefusals:
    def test_an_ambiguous_search_leaves_the_event_unlinked(self, session: Session, watched):
        event = watched("Dune")

        resolve_library(session, FakeCatalogue({"Dune": [DUNE_1984, DUNE_2021]}))

        session.refresh(event)
        assert event.title_id is None

    def test_the_rejected_candidates_are_kept_for_the_fixer(self, session: Session, watched):
        """A refusal that discards what it was choosing between makes the manual
        fix a search from scratch. Keeping them makes it one click."""
        watched("Dune")

        resolve_library(session, FakeCatalogue({"Dune": [DUNE_1984, DUNE_2021]}))

        resolution = session.scalars(select(TitleResolution)).one()
        assert resolution.method == MatchMethod.UNRESOLVED
        assert {c["node_id"] for c in resolution.candidates} == {"tm84", "tm21"}

    def test_a_refusal_explains_itself(self, session: Session, watched):
        watched("Dune")

        resolve_library(session, FakeCatalogue({"Dune": [DUNE_1984, DUNE_2021]}))

        resolution = session.scalars(select(TitleResolution)).one()
        assert "Dune" in resolution.reason

    def test_the_exported_title_is_kept_alongside_the_key(self, session: Session, watched):
        """The key is normalised past readability, and the fixer UI has to show
        a person something they recognise."""
        watched("The Office (U.S.)", kind="episode")

        resolve_library(session, FakeCatalogue(default=[]))

        resolution = session.scalars(select(TitleResolution)).one()
        assert resolution.query_title == "The Office (U.S.)"

    def test_an_unresolved_title_is_not_searched_again(self, session: Session, watched):
        """A refusal is an answer. Re-asking on every run spends requests to be
        told the same thing, and the way out is a person, not another search."""
        watched("Dune")
        catalogue = FakeCatalogue({"Dune": [DUNE_1984, DUNE_2021]})

        resolve_library(session, catalogue)
        resolve_library(session, catalogue)

        assert catalogue.searched == ["Dune"]

    def test_retrying_unresolved_titles_can_be_asked_for(self, session: Session, watched):
        """The catalogue does change, so there has to be a way to ask again --
        just not one that happens by default on every run."""
        watched("Dune")
        catalogue = FakeCatalogue({"Dune": [DUNE_1984, DUNE_2021]})
        resolve_library(session, catalogue)

        catalogue.results["Dune"] = [DUNE_2021]
        resolve_library(session, catalogue, retry_unresolved=True)

        assert len(catalogue.searched) == 2
        assert session.scalars(select(TitleResolution)).one().title_id is not None


class TestManualResolutionsWin:
    def test_an_automatic_pass_does_not_overwrite_a_manual_answer(self, session: Session, watched):
        """Someone corrected this by hand. Silently undoing that on the next run
        is the single fastest way to make a tool untrustworthy."""
        watched("Dune")
        catalogue = FakeCatalogue({"Dune": [DUNE_1984, DUNE_2021]})
        resolve_library(session, catalogue)

        chosen = Title(jw_node_id="tm21", object_type="MOVIE", title="Dune", release_year=2021)
        session.add(chosen)
        session.flush()
        resolution = session.scalars(select(TitleResolution)).one()
        resolution.method = MatchMethod.MANUAL
        resolution.title_id = chosen.id
        session.commit()

        resolve_library(session, catalogue, retry_unresolved=True)

        assert session.scalars(select(TitleResolution)).one().title_id == chosen.id
        assert session.scalars(select(TitleResolution)).one().method == MatchMethod.MANUAL

    def test_a_manual_answer_is_not_searched_again(self, session: Session, watched):
        watched("Dune")
        catalogue = FakeCatalogue({"Dune": [DUNE_2021]})
        resolve_library(session, catalogue)
        session.scalars(select(TitleResolution)).one().method = MatchMethod.MANUAL
        session.commit()
        catalogue.searched.clear()

        resolve_library(session, catalogue, retry_unresolved=True)

        assert catalogue.searched == []


class TestSurfacingWhatNeedsAPerson:
    """The refusals are only useful if something asks for them.

    A title the matcher declined is invisible everywhere else in the app -- the
    events just have no link -- so this is the one place that turns "we could
    not decide" into a list somebody can act on.
    """

    def test_a_refusal_is_listed_with_what_it_was_choosing_between(self, session: Session, watched):
        watched("Dune")
        resolve_library(session, FakeCatalogue({"Dune": [DUNE_1984, DUNE_2021]}))

        [unresolved] = unresolved_titles(session)

        assert unresolved.query_title == "Dune"
        assert {c["node_id"] for c in unresolved.candidates} == {"tm84", "tm21"}

    def test_a_refusal_carries_the_reason_it_was_refused(self, session: Session, watched):
        watched("Dune")
        resolve_library(session, FakeCatalogue({"Dune": [DUNE_1984, DUNE_2021]}))

        [unresolved] = unresolved_titles(session)

        assert unresolved.reason

    def test_a_resolved_title_is_not_listed(self, session: Session, watched):
        watched("Inception")
        resolve_library(session, FakeCatalogue({"Inception": [INCEPTION]}))

        assert unresolved_titles(session) == []

    def test_a_title_already_fixed_by_hand_is_not_listed_again(self, session: Session, watched):
        """Otherwise the list never shrinks and stops meaning anything."""
        watched("Dune")
        resolve_library(session, FakeCatalogue({"Dune": [DUNE_1984, DUNE_2021]}))
        resolution = session.scalars(select(TitleResolution)).one()

        resolve_manually(
            session,
            FakeLookup({"tm21": DUNE_2021}),
            resolution_id=resolution.id,
            node_id="tm21",
        )

        assert unresolved_titles(session) == []

    def test_it_says_how_many_rows_are_waiting_on_the_answer(self, session: Session, watched):
        for episode in range(12):
            watched("Unknown Show", kind="episode", episode_number=episode)
        resolve_library(session, FakeCatalogue(default=[]))

        [unresolved] = unresolved_titles(session)

        assert unresolved.event_count == 12

    def test_the_titles_holding_up_the_most_rows_come_first(self, session: Session, watched):
        """A fixer list is a queue of chores, and one click that fixes eighty
        episodes is worth more than one that fixes a single film."""
        watched("A Film Watched Once")
        for episode in range(20):
            watched("A Show Watched Often", kind="episode", episode_number=episode)
        resolve_library(session, FakeCatalogue(default=[]))

        titles = [unresolved.query_title for unresolved in unresolved_titles(session)]

        assert titles == ["A Show Watched Often", "A Film Watched Once"]


class TestFixingATitleByHand:
    def _refuse(self, session: Session) -> TitleResolution:
        resolve_library(session, FakeCatalogue({"Dune": [DUNE_1984, DUNE_2021]}))
        return session.scalars(select(TitleResolution)).one()

    def test_choosing_a_candidate_links_every_waiting_event(self, session: Session, watched):
        first = watched("Dune")
        second = watched("Dune")
        resolution = self._refuse(session)

        resolve_manually(
            session,
            FakeLookup({"tm21": DUNE_2021}),
            resolution_id=resolution.id,
            node_id="tm21",
        )

        session.refresh(first)
        session.refresh(second)
        assert first.catalogue_title.jw_node_id == "tm21"
        assert second.catalogue_title.jw_node_id == "tm21"

    def test_the_full_catalogue_row_is_fetched_rather_than_invented(
        self, session: Session, watched
    ):
        """The stored candidate holds four fields -- enough to draw a button.
        Building the catalogue row from it would leave the recommender with no
        genres and no runtime for everything a person fixed by hand.
        """
        watched("Dune")
        resolution = self._refuse(session)
        full = CatalogueEntry(
            node_id="tm21",
            title="Dune",
            object_type="MOVIE",
            release_year=2021,
            runtime_minutes=155,
            genres=("scf", "act"),
            imdb_score=8.0,
        )

        resolve_manually(
            session, FakeLookup({"tm21": full}), resolution_id=resolution.id, node_id="tm21"
        )

        title = session.scalars(select(Title).where(Title.jw_node_id == "tm21")).one()
        assert title.runtime_minutes == 155
        assert title.genres == ["scf", "act"]
        assert title.imdb_score == 8.0

    def test_a_title_already_in_the_catalogue_is_not_fetched_again(self, session: Session, watched):
        """Two people's refusals can point at the same film, and the second fix
        should not spend a request re-reading a row we already hold."""
        watched("Dune")
        watched("Dune Part Two")
        resolve_library(session, FakeCatalogue(default=[DUNE_1984, DUNE_2021]))
        first, second = session.scalars(select(TitleResolution).order_by(TitleResolution.id)).all()
        lookup = FakeLookup({"tm21": DUNE_2021})

        resolve_manually(session, lookup, resolution_id=first.id, node_id="tm21")
        resolve_manually(session, lookup, resolution_id=second.id, node_id="tm21")

        assert lookup.looked_up == ["tm21"]

    def test_the_answer_is_marked_manual_so_a_later_pass_leaves_it_alone(
        self, session: Session, watched
    ):
        watched("Dune")
        resolution = self._refuse(session)

        resolve_manually(
            session,
            FakeLookup({"tm21": DUNE_2021}),
            resolution_id=resolution.id,
            node_id="tm21",
        )

        stored = session.scalars(select(TitleResolution)).one()
        assert stored.method == MatchMethod.MANUAL
        assert stored.confidence == 1.0

    def test_a_later_automatic_pass_really_does_leave_it_alone(self, session: Session, watched):
        """The stored method is only worth setting if it is honoured; this is
        the end-to-end version of that promise."""
        watched("Dune")
        resolution = self._refuse(session)
        catalogue = FakeCatalogue({"Dune": [DUNE_1984]})
        resolve_manually(
            session,
            FakeLookup({"tm21": DUNE_2021}),
            resolution_id=resolution.id,
            node_id="tm21",
        )
        catalogue.searched.clear()

        resolve_library(session, catalogue, retry_unresolved=True)

        assert catalogue.searched == []
        assert session.scalars(select(TitleResolution)).one().title.jw_node_id == "tm21"

    def test_correcting_a_correction_moves_the_events_across(self, session: Session, watched):
        """People get it wrong the first time too, so the fix has to be a fix
        rather than an append."""
        event = watched("Dune")
        resolution = self._refuse(session)
        lookup = FakeLookup({"tm21": DUNE_2021, "tm84": DUNE_1984})
        resolve_manually(session, lookup, resolution_id=resolution.id, node_id="tm21")

        resolve_manually(session, lookup, resolution_id=resolution.id, node_id="tm84")

        session.refresh(event)
        assert event.catalogue_title.jw_node_id == "tm84"

    def test_it_reports_what_it_linked(self, session: Session, watched):
        for episode in range(7):
            watched("Dune", episode_number=episode)
        resolution = self._refuse(session)

        fixed = resolve_manually(
            session,
            FakeLookup({"tm21": DUNE_2021}),
            resolution_id=resolution.id,
            node_id="tm21",
        )

        assert fixed.linked_events == 7
        assert fixed.title.jw_node_id == "tm21"

    def test_the_rejected_candidates_survive_the_fix(self, session: Session, watched):
        """What the matcher was choosing between is the record of why a person
        had to be asked, and re-picking should not mean re-searching."""
        watched("Dune")
        resolution = self._refuse(session)

        resolve_manually(
            session,
            FakeLookup({"tm21": DUNE_2021}),
            resolution_id=resolution.id,
            node_id="tm21",
        )

        assert len(session.scalars(select(TitleResolution)).one().candidates) == 2

    def test_an_unknown_resolution_is_rejected_rather_than_created(self, session: Session):
        with pytest.raises(ResolutionNotFound):
            resolve_manually(
                session, FakeLookup({"tm21": DUNE_2021}), resolution_id=999, node_id="tm21"
            )

    def test_a_catalogue_failure_leaves_the_stored_answer_untouched(
        self, session: Session, watched
    ):
        """A half-applied fix -- the method changed, the link not written --
        would be a title marked as decided by hand that points nowhere."""
        event = watched("Dune")
        resolution = self._refuse(session)

        with pytest.raises(JustWatchApiError):
            resolve_manually(
                session, FakeLookup(), resolution_id=resolution.id, node_id="tm-nonsense"
            )

        session.refresh(event)
        assert event.title_id is None
        assert session.scalars(select(TitleResolution)).one().method == MatchMethod.UNRESOLVED


class TestFailuresAreContained:
    def test_one_failed_search_does_not_abandon_the_rest(self, session: Session, watched):
        """Resolution walks a whole library. Losing all of it because the
        network dropped one request in the middle would mean starting over."""
        watched("Broken")
        watched("Inception")
        catalogue = FakeCatalogue(
            {"Broken": JustWatchHttpError("timed out"), "Inception": [INCEPTION]}
        )

        summary = resolve_library(session, catalogue)

        assert summary.resolved == 1
        assert summary.failed == 1

    def test_a_failed_title_is_not_recorded_as_unresolved(self, session: Session, watched):
        """ "We could not ask" is not "we asked and there was no answer". Storing
        a failure as a refusal would stop it ever being retried."""
        watched("Broken")
        catalogue = FakeCatalogue({"Broken": JustWatchHttpError("timed out")})

        resolve_library(session, catalogue)

        assert session.scalars(select(TitleResolution)).all() == []

    def test_a_failed_title_is_searched_again_next_run(self, session: Session, watched):
        watched("Broken")
        catalogue = FakeCatalogue({"Broken": JustWatchHttpError("timed out")})
        resolve_library(session, catalogue)

        catalogue.results["Broken"] = [
            CatalogueEntry(node_id="tm2", title="Broken", object_type="MOVIE")
        ]
        summary = resolve_library(session, catalogue)

        assert summary.resolved == 1

"""Tests for turning stored watch events into catalogue links.

No test here touches the network. The resolver takes a catalogue client as an
argument and these pass in a fake, which is what makes it possible to describe
things a live API could never be asked to reproduce on demand -- a search that
times out, a title that comes back ambiguous, a hundred episodes of one show.

The recurring theme is restraint. Resolution costs someone else's requests, so
asking twice for the same answer is a defect, and so is asking again for an
answer a person already gave by hand.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from simplejustwatchapi.exceptions import JustWatchApiError, JustWatchHttpError
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.matching import MatchMethod
from app.core.title_parser import TitleKind
from app.models import Offer, Title, TitleResolution, WatchEvent
from app.services.justwatch_client import CatalogueEntry, OfferEntry
from app.services.offers import cached_offers
from app.services.resolver import (
    ResolutionNotFound,
    recent_resolutions,
    resolve_library,
    resolve_manually,
    search_candidates,
    unresolved_page,
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
# Fixed moments for the tests about ordering. Passing the clock in rather than
# letting two calls race for different microseconds is what makes those tests
# say what they mean instead of usually passing.
DECIDED_FIRST = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
DECIDED_SECOND = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)
DECIDED_THIRD = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)

DUNE_1984 = CatalogueEntry(node_id="tm84", title="Dune", object_type="MOVIE", release_year=1984)
DUNE_2021 = CatalogueEntry(node_id="tm21", title="Dune", object_type="MOVIE", release_year=2021)


class FakeCatalogue:
    """A catalogue that answers from a script instead of from the internet.

    ``results`` maps a search term to what comes back; an exception value is
    raised instead. Every search is recorded, because "how many times did we
    ask" is the behaviour several of these tests are actually about.
    """

    def __init__(
        self, results: dict | None = None, default: list | None = None, country: str = "IN"
    ):
        # Offers arrive with a search, so whatever stores them has to know which
        # country they describe, and the client that asked is the only honest
        # source for that.
        self.country = country
        self.results = results or {}
        self.default = default if default is not None else []
        self.searched: list[str] = []
        # What each search was narrowed to, recorded because "did we filter by
        # the right kind" is the behaviour some of these tests are about.
        self.search_types: list[tuple[str, ...] | None] = []

    def search(self, title: str, *, object_types=None) -> list[CatalogueEntry]:
        self.searched.append(title)
        self.search_types.append(tuple(object_types) if object_types else None)
        outcome = self.results.get(title, self.default)
        if isinstance(outcome, Exception):
            raise outcome
        return list(outcome)


class EchoCatalogue:
    """Answers every search with an entry that matches it exactly.

    For tests about how much work resolution does rather than what it decides:
    every title resolves, so the whole path runs for every question.
    """

    def __init__(self, country: str = "IN"):
        self.country = country
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

    def __init__(self, entries: dict | None = None, country: str = "IN"):
        self.country = country
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

    def test_the_title_stored_is_the_one_the_matcher_chose(self, session: Session, watched):
        """JustWatch orders results by its own idea of relevance, and the
        matcher weighs title, kind and year and does not have to agree. Storing
        the first result rather than the chosen one would produce a confident,
        audited, entirely wrong match -- the exact failure this app is built to
        avoid, arrived at through the back door.
        """
        watched("Inception")
        catalogue = FakeCatalogue({"Inception": [THE_OFFICE, INCEPTION]})

        resolve_library(session, catalogue)

        assert session.scalars(select(Title)).one().jw_node_id == "tm1"

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


class TestAvailabilityIsCachedForFree:
    """The reason resolution and availability are one step rather than two.

    JustWatch returns where a title plays as part of the search that resolution
    already has to make. Fetching that again afterwards would double the number
    of requests a library costs to learn nothing new, so the answer is kept as
    it arrives.
    """

    def test_resolving_a_title_stores_where_it_plays(self, session: Session, watched):
        watched("Inception")
        catalogue = FakeCatalogue(
            {"Inception": [replace(INCEPTION, offers=(OfferEntry("nfx", "FLATRATE"),))]}
        )

        resolve_library(session, catalogue)

        title = session.scalars(select(Title)).one()
        assert [offer.provider for offer in cached_offers(session, title.id, country="IN")] == [
            "nfx"
        ]

    def test_it_costs_no_extra_requests(self, session: Session, watched):
        """The whole point. One search per distinct title, availability
        included -- not one search and then a lookup."""
        watched("Inception")
        catalogue = FakeCatalogue(
            {"Inception": [replace(INCEPTION, offers=(OfferEntry("nfx", "FLATRATE"),))]}
        )

        resolve_library(session, catalogue)

        assert catalogue.searched == ["Inception"]

    def test_the_country_is_the_one_the_client_asked_about(self, session: Session, watched):
        """Read off the client rather than from settings, so the offers and the
        request that produced them cannot end up describing different places."""
        watched("Inception")
        catalogue = FakeCatalogue(
            {"Inception": [replace(INCEPTION, offers=(OfferEntry("nfx", "FLATRATE"),))]},
            country="US",
        )

        resolve_library(session, catalogue)

        title = session.scalars(select(Title)).one()
        assert cached_offers(session, title.id, country="US") != []
        assert cached_offers(session, title.id, country="IN") == []

    def test_a_title_streaming_nowhere_still_records_that_we_asked(self, session: Session, watched):
        """Otherwise the refresh pass would treat it as never-asked and spend a
        request on it every single time it ran."""
        watched("Inception")
        catalogue = FakeCatalogue({"Inception": [INCEPTION]})

        resolve_library(session, catalogue)

        assert session.scalars(select(Title)).one().offers_fetched_at is not None

    def test_a_refusal_caches_nothing(self, session: Session, watched):
        """There is no title to hang an offer on, and the offers of a candidate
        we declined describe something we have not agreed we watched."""
        watched("Ambiguous")
        catalogue = FakeCatalogue({"Ambiguous": [DUNE_1984, DUNE_2021]})

        resolve_library(session, catalogue)

        assert session.scalars(select(Offer)).all() == []

    def test_a_cached_resolution_does_not_clear_what_it_knows(self, session: Session, watched):
        """A second pass answers from the resolution cache without searching, so
        it has no offers to store -- and must not read that as "nowhere"."""
        watched("Inception")
        catalogue = FakeCatalogue(
            {"Inception": [replace(INCEPTION, offers=(OfferEntry("nfx", "FLATRATE"),))]}
        )
        resolve_library(session, catalogue)

        resolve_library(session, catalogue)

        title = session.scalars(select(Title)).one()
        assert [offer.provider for offer in cached_offers(session, title.id, country="IN")] == [
            "nfx"
        ]

    def test_the_offers_stored_are_the_chosen_entry_s(self, session: Session, watched):
        """Same trap as the title itself, one step further on: caching the first
        result's offers against the chosen result's title would say a film is on
        a service that is carrying something else entirely."""
        watched("Inception")
        catalogue = FakeCatalogue(
            {
                "Inception": [
                    replace(THE_OFFICE, offers=(OfferEntry("wrong", "FLATRATE"),)),
                    replace(INCEPTION, offers=(OfferEntry("nfx", "FLATRATE"),)),
                ]
            }
        )

        resolve_library(session, catalogue)

        title = session.scalars(select(Title)).one()
        assert [offer.provider for offer in cached_offers(session, title.id, country="IN")] == [
            "nfx"
        ]

    def test_a_manual_fix_caches_the_offers_it_looked_up(self, session: Session, watched):
        """The lookup a manual fix makes returns offers too, and there is no
        reason to throw them away and re-ask for them later."""
        watched("Ambiguous")
        resolve_library(session, FakeCatalogue({"Ambiguous": [DUNE_1984, DUNE_2021]}))
        resolution = session.scalars(select(TitleResolution)).one()
        lookup = FakeLookup({"tm21": replace(DUNE_2021, offers=(OfferEntry("prv", "FLATRATE"),))})

        fixed = resolve_manually(session, lookup, resolution_id=resolution.id, node_id="tm21")

        assert [
            offer.provider for offer in cached_offers(session, fixed.title.id, country="IN")
        ] == ["prv"]


class TestResolvingInBatches:
    """A pass paces itself at a request a second against somebody else's API,
    so a library of four hundred titles is seven minutes inside one HTTP
    request. The limit is what lets a caller do it in pieces and show progress.

    The limit counts *searches*, not questions looked at. That is what makes
    repeated batches make progress without anyone having to guarantee the order
    the questions come back in: every search stores an answer, and a stored
    answer is skipped for free next time.
    """

    def test_a_limit_caps_how_many_searches_one_pass_makes(self, session: Session, watched):
        for index in range(10):
            watched(f"Film {index}")
        catalogue = EchoCatalogue()

        resolve_library(session, catalogue, limit=4)

        assert len(catalogue.searched) == 4

    def test_no_limit_still_asks_about_everything(self, session: Session, watched):
        for index in range(10):
            watched(f"Film {index}")
        catalogue = EchoCatalogue()

        resolve_library(session, catalogue)

        assert len(catalogue.searched) == 10

    def test_the_next_batch_carries_on_rather_than_starting_over(self, session: Session, watched):
        for index in range(10):
            watched(f"Film {index}")
        catalogue = EchoCatalogue()

        resolve_library(session, catalogue, limit=4)
        resolve_library(session, catalogue, limit=4)

        assert len(catalogue.searched) == 8
        # Nothing asked twice. A batch that re-asked would burn its allowance on
        # answers we already had and never reach the end of the library.
        assert len(set(catalogue.searched)) == 8

    def test_enough_batches_finish_the_library(self, session: Session, watched):
        for index in range(10):
            watched(f"Film {index}")
        catalogue = EchoCatalogue()

        passes = 0
        while resolve_library(session, catalogue, limit=3).remaining:
            passes += 1
            assert passes < 10, "batching is not making progress"

        assert sorted(catalogue.searched) == sorted(f"Film {index}" for index in range(10))

    def test_remaining_says_how_much_a_further_pass_would_ask_about(
        self, session: Session, watched
    ):
        for index in range(10):
            watched(f"Film {index}")
        catalogue = EchoCatalogue()

        summary = resolve_library(session, catalogue, limit=4)

        assert summary.searched == 4
        assert summary.remaining == 6

    def test_remaining_is_nothing_once_every_question_has_an_answer(
        self, session: Session, watched
    ):
        watched("Inception")
        catalogue = FakeCatalogue({"Inception": [INCEPTION]})

        assert resolve_library(session, catalogue).remaining == 0

    def test_a_refusal_is_not_something_a_further_pass_would_ask_about(
        self, session: Session, watched
    ):
        """It is waiting on a person, not on another request. Counting it as
        remaining would leave a progress bar that never finishes."""
        watched("Dune")
        catalogue = FakeCatalogue({"Dune": [DUNE_1984, DUNE_2021]})

        summary = resolve_library(session, catalogue)

        assert summary.unresolved == 1
        assert summary.remaining == 0

    def test_an_answer_we_already_have_does_not_consume_the_limit(self, session: Session, watched):
        """Otherwise a batch could spend its whole allowance walking past
        questions it was never going to ask about."""
        for index in range(5):
            watched(f"Film {index}")
        catalogue = EchoCatalogue()
        resolve_library(session, catalogue)

        watched("Something New")
        resolve_library(session, catalogue, limit=1)

        assert catalogue.searched[-1] == "Something New"
        assert len(catalogue.searched) == 6

    def test_a_batch_that_searches_nothing_still_links_rows_imported_later(
        self, session: Session, watched
    ):
        """The reason a spent allowance skips the search rather than abandoning
        the walk.

        The order matters, and it is the whole point of the test: an unanswered
        question comes first and an answered one after it. A batch that stopped
        at the first question it could not afford would never reach the second,
        and the row imported against it would wait for whichever future batch
        happened to get that far.
        """
        watched("Zebra")
        late_answer = watched("Inception")
        catalogue = FakeCatalogue({"Inception": [INCEPTION]})
        resolve_library(session, catalogue)
        assert late_answer.title_id is not None

        late = watched("Inception")
        # retry_unresolved is what puts the refusal back in front of the
        # allowance; without it Zebra is skipped for free and never blocks.
        resolve_library(session, catalogue, limit=0, retry_unresolved=True)

        session.refresh(late)
        assert late.title_id is not None
        assert len(catalogue.searched) == 2


class TestOnePageOfTheQueue:
    """The queue can be hundreds long and every row carries its own list of
    rejected candidates, so it is served a page at a time."""

    def refuse(self, session: Session, watched, count: int) -> None:
        for index in range(count):
            watched(f"Puzzle {index:02d}")
        resolve_library(session, FakeCatalogue(default=[DUNE_1984, DUNE_2021]))

    def test_reports_the_length_of_the_whole_queue_not_of_the_page(self, session: Session, watched):
        """A page that only knew its own size could not say "25 of 214", and a
        caller could not tell whether to ask for another."""
        self.refuse(session, watched, 7)

        page = unresolved_page(session, limit=3)

        assert page.total == 7
        assert len(page.items) == 3

    def test_an_offset_moves_the_window(self, session: Session, watched):
        self.refuse(session, watched, 7)

        first = unresolved_page(session, limit=3)
        second = unresolved_page(session, limit=3, offset=3)

        assert [item.query_title for item in first.items] != [
            item.query_title for item in second.items
        ]
        assert second.total == 7

    def test_the_pages_together_are_the_whole_list_in_the_same_order(
        self, session: Session, watched
    ):
        self.refuse(session, watched, 7)
        whole = [title.query_title for title in unresolved_titles(session)]

        paged: list[str] = []
        for offset in range(0, 9, 3):
            paged += [
                item.query_title for item in unresolved_page(session, limit=3, offset=offset).items
            ]

        assert paged == whole

    def test_no_limit_is_the_whole_queue(self, session: Session, watched):
        self.refuse(session, watched, 7)

        assert len(unresolved_page(session).items) == 7

    def test_an_offset_past_the_end_is_an_empty_page_rather_than_an_error(
        self, session: Session, watched
    ):
        self.refuse(session, watched, 3)

        page = unresolved_page(session, limit=3, offset=99)

        assert page.items == []
        assert page.total == 3

    def test_an_empty_queue_is_a_page_of_nothing(self, session: Session):
        page = unresolved_page(session, limit=3)

        assert page.total == 0
        assert page.items == []


class TestWhatWasAlreadyDecided:
    """Once a person answers a question it leaves the queue, so without this
    there is no way back to a decision they got wrong."""

    def decide(
        self,
        session: Session,
        watched,
        title: str,
        node_id: str,
        *,
        when: datetime | None = None,
    ) -> int:
        watched(title)
        resolve_library(session, FakeCatalogue(default=[DUNE_1984, DUNE_2021]))
        resolution = session.scalars(
            select(TitleResolution).where(TitleResolution.query_title == title)
        ).one()
        resolve_manually(
            session,
            FakeLookup({"tm84": DUNE_1984, "tm21": DUNE_2021}),
            resolution_id=resolution.id,
            node_id=node_id,
            now=when,
        )
        return resolution.id

    def test_lists_what_a_person_chose(self, session: Session, watched):
        self.decide(session, watched, "Dune", "tm21")

        decided = recent_resolutions(session)

        assert [entry.query_title for entry in decided] == ["Dune"]
        assert decided[0].title == "Dune"
        assert decided[0].release_year == 2021

    def test_leaves_out_what_the_matcher_decided_on_its_own(self, session: Session, watched):
        """Only a choice somebody made is a choice somebody might want back."""
        watched("Inception")
        resolve_library(session, FakeCatalogue({"Inception": [INCEPTION]}))

        assert recent_resolutions(session) == []

    def test_most_recently_decided_first(self, session: Session, watched):
        self.decide(session, watched, "Dune", "tm21", when=DECIDED_FIRST)
        self.decide(session, watched, "Arrakis", "tm84", when=DECIDED_SECOND)

        assert [entry.query_title for entry in recent_resolutions(session)] == [
            "Arrakis",
            "Dune",
        ]

    def test_changing_a_decision_moves_it_back_to_the_top(self, session: Session, watched):
        """Otherwise the list is ordered by when the matcher gave up rather than
        by when anybody decided, and the fix somebody just made is nowhere near
        the top of the list they are looking at."""
        first = self.decide(session, watched, "Dune", "tm21", when=DECIDED_FIRST)
        self.decide(session, watched, "Arrakis", "tm84", when=DECIDED_SECOND)

        resolve_manually(
            session,
            FakeLookup({"tm84": DUNE_1984}),
            resolution_id=first,
            node_id="tm84",
            now=DECIDED_THIRD,
        )

        assert recent_resolutions(session)[0].query_title == "Dune"

    def test_the_decision_is_dated_when_it_was_made(self, session: Session, watched):
        """Pinned directly rather than through the ordering it produces. Real
        time happens to sort these correctly even when the clock argument is
        ignored, so the order alone proves nothing about the stamp."""
        self.decide(session, watched, "Dune", "tm21", when=DECIDED_FIRST)

        assert recent_resolutions(session)[0].resolved_at == DECIDED_FIRST

    def test_keeps_only_the_most_recent_few(self, session: Session, watched):
        for index in range(5):
            self.decide(session, watched, f"Puzzle {index}", "tm21")

        assert len(recent_resolutions(session, limit=2)) == 2

    def test_carries_enough_to_show_it_and_to_change_it(self, session: Session, watched):
        resolution_id = self.decide(session, watched, "Dune", "tm21")

        entry = recent_resolutions(session)[0]

        assert entry.resolution_id == resolution_id
        assert entry.title_id is not None
        assert entry.object_type == "MOVIE"
        # What the matcher was choosing between, so a change of mind re-picks
        # from the same list rather than from a blank search box.
        assert [candidate["node_id"] for candidate in entry.candidates] == ["tm84", "tm21"]

    def decide_on_its_own_title(self, session: Session, watched, index: int) -> None:
        """One decision pointing at a title of its own.

        Distinct on purpose. Six decisions that all chose the same film cost one
        lazy load between them however many rows there are, because the second
        one finds the title already in the identity map -- so a list of repeats
        cannot tell a join from a query per row.
        """
        entry = CatalogueEntry(node_id=f"tm{index}", title=f"Puzzle {index}", object_type="MOVIE")
        watched(f"Puzzle {index}")
        resolve_library(session, FakeCatalogue(default=[DUNE_1984, DUNE_2021]))
        resolution = session.scalars(
            select(TitleResolution).where(TitleResolution.query_title == f"Puzzle {index}")
        ).one()
        resolve_manually(
            session,
            FakeLookup({entry.node_id: entry}),
            resolution_id=resolution.id,
            node_id=entry.node_id,
        )

    def test_a_longer_list_is_not_more_queries(self, session: Session, watched, counting):
        self.decide_on_its_own_title(session, watched, 0)
        # Expired first, deliberately. These rows were written by this very
        # session, so the titles sit loaded in its identity map and following
        # the relationship would cost nothing -- which is exactly the state a
        # real request is never in. Without this the test passes whether the
        # titles are loaded with the resolutions or one query at a time.
        session.expire_all()
        with counting() as few:
            recent_resolutions(session)

        for index in range(1, 6):
            self.decide_on_its_own_title(session, watched, index)
        session.expire_all()
        with counting() as many:
            recent_resolutions(session)

        assert few
        assert len(many) == len(few)


class TestSearchingByHand:
    """The stored candidates are what the matcher already weighed, so when the
    right answer was never among them -- a typo in the export, a title known by
    another name here -- there has to be a way to go and look."""

    def test_asks_the_catalogue_for_what_was_typed(self):
        catalogue = FakeCatalogue({"Arrival": [INCEPTION]})

        search_candidates(catalogue, "Arrival")

        assert catalogue.searched == ["Arrival"]

    def test_returns_what_came_back_as_candidates(self):
        catalogue = FakeCatalogue({"Dune": [DUNE_1984, DUNE_2021]})

        found = search_candidates(catalogue, "Dune")

        assert [candidate.node_id for candidate in found] == ["tm84", "tm21"]
        assert found[0].release_year == 1984

    def test_narrows_to_films_when_that_is_the_kind_asked_for(self):
        catalogue = FakeCatalogue(default=[INCEPTION])

        search_candidates(catalogue, "Dune", kind=TitleKind.MOVIE)

        assert catalogue.search_types == [("MOVIE",)]

    def test_narrows_to_shows_for_an_episode(self):
        """A history row is an episode because that is what was watched; the
        catalogue entry is a show because that is what exists."""
        catalogue = FakeCatalogue(default=[THE_OFFICE])

        search_candidates(catalogue, "The Office", kind=TitleKind.EPISODE)

        assert catalogue.search_types == [("SHOW",)]

    def test_narrows_nothing_when_no_kind_is_given(self):
        """The parser's reading of a title is itself a common reason a row
        needed fixing, so a filter derived from it is exactly what would hide
        the right answer."""
        catalogue = FakeCatalogue(default=[INCEPTION])

        search_candidates(catalogue, "Dune")

        assert catalogue.search_types == [None]

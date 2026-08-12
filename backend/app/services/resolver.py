"""Work out what each watched title actually is, and remember the answer.

Importing gives strings. Everything after it -- availability, statistics,
recommendations -- needs catalogue entries, and this is the step between. It runs
separately from the upload so that an import stays fast and so that a JustWatch
outage delays resolution rather than rejecting a file.

Three decisions shape the whole module:

**Ask once.** The unit of work is a distinct normalised title, not a row. A
season of television is forty rows asking one question, and this is an unofficial
API we are guests of.

**Remember refusals.** A title the matcher declined gets a stored row, not a
missing one. That row carries the candidates it rejected, which is what turns the
manual fix into one click, and it stops the next run spending a request to be
told the same thing.

**Never overwrite a person.** A resolution someone corrected by hand is final.
An automatic pass that quietly undoes manual work is worse than one that never
runs.

This module is impure: it owns the session and the catalogue client.
"""

import logging
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from simplejustwatchapi.exceptions import JustWatchError
from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from app.core.matching import (
    Candidate,
    MatchMethod,
    MatchQuery,
    MatchResult,
    match_title,
    object_types_for,
)
from app.core.normalize import normalize_title
from app.core.title_parser import TitleKind
from app.models import DEFAULT_USER_ID, Title, TitleResolution, WatchEvent
from app.services.justwatch_client import CatalogueEntry, CatalogueLookup, CatalogueSearch
from app.services.offers import store_offers
from app.services.single_flight import one_at_a_time
from app.services.titles import title_from_entry

_log = logging.getLogger(__name__)

# SQLite caps how many values one statement may bind, and a long-running show
# can have thousands of episodes behind a single answer, so linking is batched.
_ID_UPDATE_CHUNK = 500

# How many past decisions the fixer offers to reconsider. Enough to catch the
# one somebody just got wrong, few enough that it stays a footnote rather than
# a second queue.
RECENT_RESOLUTIONS = 10


@dataclass(frozen=True)
class ResolveSummary:
    """What one resolution pass did.

    ``failed`` is deliberately separate from ``unresolved``. "We could not ask"
    and "we asked and the answer was unclear" need different responses: the
    first is retried automatically, the second waits for a person.
    """

    searched: int = 0
    resolved: int = 0
    unresolved: int = 0
    failed: int = 0
    linked_events: int = 0
    # How many distinct titles still have no stored answer once this pass
    # finished. A batched caller needs it to draw progress and to know when to
    # stop asking; without it the only signal is "we searched fewer than we were
    # allowed", which cannot say how much is left.
    remaining: int = 0


@dataclass
class _Question:
    """One distinct thing to look up, and every row waiting on the answer."""

    key: str
    kind: TitleKind
    # The exported spelling, kept so the fixer UI can show something readable.
    display_title: str
    # Whether the parser proved the kind or guessed it, which decides how much
    # weight the matcher gives a catalogue entry of the other kind.
    ambiguous: bool
    # Collected while grouping rather than looked up afterwards. Querying for
    # them per title re-reads the library once per title, which is quadratic and
    # costs tens of seconds on a history of a few thousand rows.
    event_ids: list[int] = field(default_factory=list)


@one_at_a_time("resolve")
def resolve_library(
    session: Session,
    catalogue: CatalogueSearch,
    *,
    retry_unresolved: bool = False,
    limit: int | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> ResolveSummary:
    """Resolve every distinct title that does not already have an answer.

    Args:
        retry_unresolved: ask again about titles previously refused. Off by
            default, because the catalogue rarely changes between two runs an
            hour apart and asking costs requests. Worth turning on occasionally;
            never worth doing automatically.

    Raises:
        PassAlreadyRunning: if another pass holds the budget. See
            :mod:`app.services.single_flight` -- a second pass would not finish
            sooner and would hold a worker thread the whole time it waited.
    """
    # Each of these reads its table exactly once. Everything after works from
    # memory, so the cost of a pass is set by how many searches it makes rather
    # than by how large the library is.
    questions = _questions(session, user_id)
    known = _stored_resolutions(session, user_id)
    titles = _titles_by_node(session)
    summary = ResolveSummary()

    searches = 0
    for question in questions:
        stored = known.get((question.key, question.kind))
        if not _still_to_ask(stored, retry_unresolved):
            summary = _apply(session, question.event_ids, stored.title_id, summary)
            continue

        if limit is not None and searches >= limit:
            # Out of allowance, and the walk deliberately carries on rather than
            # breaking. A question further down may already have an answer with
            # rows waiting to be linked to it, and abandoning the loop would
            # leave those rows waiting for whichever batch happened to reach
            # them -- which is not a promise batching should be allowed to break.
            continue
        searches += 1

        try:
            entries = catalogue.search(question.display_title)
        except JustWatchError:
            # Contained on purpose: resolution walks a whole library, and losing
            # all of it to one dropped request would mean starting over. Nothing
            # is stored, so the title is asked about again next run.
            _log.warning("could not search for %r", question.display_title, exc_info=True)
            summary = _counted(summary, searched=1, failed=1)
            continue

        result = _match(question, entries)
        entry = _chosen_entry(entries, result)
        title = _title_for(session, titles, entry)
        if entry is not None and title is not None:
            # The search we just made already carried the offers, so availability
            # is cached here for nothing. Doing it any later would mean asking a
            # second time for an answer we were handed the first time.
            store_offers(session, title, entry.offers, country=catalogue.country)
        _record(session, question, result, title, stored, user_id)
        session.flush()

        summary = _counted(
            summary,
            searched=1,
            resolved=title is not None,
            unresolved=title is None,
        )
        summary = _apply(session, question.event_ids, title.id if title else None, summary)

    session.commit()
    # The links were written as bulk statements, which the identity map does not
    # see. Without this, a caller holding a session configured not to expire on
    # commit would keep reading the pre-resolution rows.
    session.expire_all()

    # Re-read rather than reasoning from `known`, which was loaded before this
    # pass wrote to it. What is left is defined as what another identical call
    # would search, so a caller can drive batches until it reaches zero.
    after = _stored_resolutions(session, user_id)
    return replace(
        summary,
        remaining=sum(
            1
            for question in questions
            if _still_to_ask(after.get((question.key, question.kind)), retry_unresolved)
        ),
    )


class ResolutionNotFound(LookupError):
    """The resolution someone tried to fix does not exist."""


@dataclass(frozen=True)
class UnresolvedTitle:
    """One title the matcher declined, and everything needed to fix it."""

    resolution_id: int
    query_title: str
    kind: TitleKind
    reason: str
    # How many rows are waiting on this answer. The fixer is a queue of chores,
    # and this is what says which chore is worth doing.
    event_count: int
    candidates: list[dict]


@dataclass(frozen=True)
class ManualResolution:
    """What one fix by hand did."""

    resolution_id: int
    title: Title
    linked_events: int


def unresolved_titles(session: Session, *, user_id: str = DEFAULT_USER_ID) -> list[UnresolvedTitle]:
    """Every title still waiting on a person, most consequential first.

    Refusals are invisible everywhere else in the app -- the events simply have
    no link -- so this is what turns "we could not decide" into a list somebody
    can act on.
    """
    rows = session.scalars(
        select(TitleResolution).where(
            TitleResolution.user_id == user_id,
            TitleResolution.method == MatchMethod.UNRESOLVED,
        )
    ).all()
    if not rows:
        # Nothing to count against, and on a fully resolved library this is the
        # normal case. Worth not walking the events to establish it.
        return []

    waiting = _unlinked_event_counts(session, user_id)
    listed = [
        UnresolvedTitle(
            resolution_id=row.id,
            query_title=row.query_title,
            kind=row.kind,
            reason=row.reason,
            event_count=waiting.get((row.query_key, row.kind), 0),
            candidates=list(row.candidates),
        )
        for row in rows
    ]
    # One click that fixes eighty episodes is worth more than one that fixes a
    # single film. Ties break on the title so the order is stable between calls.
    listed.sort(key=lambda title: (-title.event_count, title.query_title))
    return listed


@dataclass(frozen=True)
class UnresolvedPage:
    """One page of the fixer's queue, and how long the whole queue is."""

    total: int = 0
    items: list[UnresolvedTitle] = field(default_factory=list)


@dataclass(frozen=True)
class ResolvedTitle:
    """A question somebody already answered, so a wrong answer can be changed."""

    resolution_id: int
    query_title: str
    kind: TitleKind
    title_id: int
    # The catalogue's own id, which is what the stored candidates are keyed by.
    # Without it a client can only guess which button it chose by comparing the
    # title and the year -- and the queue asks about a title precisely when two
    # entries look alike on both.
    jw_node_id: str
    title: str
    object_type: str
    release_year: int | None
    poster_url: str | None
    resolved_at: datetime
    candidates: list[dict]


def unresolved_page(
    session: Session,
    *,
    limit: int | None = None,
    offset: int = 0,
    user_id: str = DEFAULT_USER_ID,
) -> UnresolvedPage:
    """A slice of the queue, with the length of the whole thing.

    ``total`` is the length of the queue rather than of the page, because a
    caller that only knew its own page could neither say "25 of 214" nor tell
    whether to ask for another.

    The slicing happens after the sort, in Python, which is where the sort
    already was: the order is by how many rows each answer is holding up, and
    that count is worked out from the events rather than stored on the
    resolution, so no ``ORDER BY`` could produce it. This trims what goes over
    the wire and nothing else -- it is not a way to avoid reading the table.
    """
    listed = unresolved_titles(session, user_id=user_id)
    start = max(offset, 0)
    window = listed[start:] if limit is None else listed[start : start + limit]
    return UnresolvedPage(total=len(listed), items=window)


def recent_resolutions(
    session: Session,
    *,
    limit: int = RECENT_RESOLUTIONS,
    user_id: str = DEFAULT_USER_ID,
) -> list[ResolvedTitle]:
    """The questions answered by hand, most recently decided first.

    Only manual answers. A choice somebody made is the only kind of choice
    somebody might want back -- an automatic match that was wrong shows up as a
    wrong recommendation, not as a decision to revisit.

    The title is loaded with the resolution rather than followed per row: this
    is a short list, but a relationship walked in a loop is a query per row
    whatever its length, and the length is the caller's to choose.
    """
    rows = session.scalars(
        select(TitleResolution)
        .where(
            TitleResolution.user_id == user_id,
            TitleResolution.method == MatchMethod.MANUAL,
            TitleResolution.title_id.is_not(None),
        )
        .options(joinedload(TitleResolution.title))
        # Ties break on the id so two decisions in the same instant still have
        # one stable order, as everywhere else that sorts for a person to read.
        .order_by(TitleResolution.resolved_at.desc(), TitleResolution.id.desc())
        .limit(limit)
    ).all()

    return [
        ResolvedTitle(
            resolution_id=row.id,
            query_title=row.query_title,
            kind=row.kind,
            title_id=row.title.id,
            jw_node_id=row.title.jw_node_id,
            title=row.title.title,
            object_type=row.title.object_type,
            release_year=row.title.release_year,
            poster_url=row.title.poster_url,
            resolved_at=row.resolved_at,
            candidates=list(row.candidates),
        )
        for row in rows
    ]


def search_candidates(
    catalogue: CatalogueSearch,
    query: str,
    *,
    kind: TitleKind | None = None,
) -> list[Candidate]:
    """Ask the catalogue what it has under a name somebody typed.

    The way back when the stored candidates are no help -- a title misspelled in
    the export, or known by a different name in this country -- which is
    precisely the case the matcher cannot be expected to have got right.

    Read-only on purpose. A search comes back carrying offers, and it is
    tempting to bank them, but there is no catalogue row to hang them on until
    somebody actually picks one. Storing a title for every result of every
    search someone typed would fill the catalogue with things nobody chose; the
    offers arrive for free with the lookup that confirms the pick.
    """
    entries = catalogue.search(query, object_types=object_types_for(kind))
    return [entry.as_candidate() for entry in entries]


def resolve_manually(
    session: Session,
    catalogue: CatalogueLookup,
    *,
    resolution_id: int,
    node_id: str,
    now: datetime | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> ManualResolution:
    """Record the answer a person gave, and link every row that was waiting.

    Raises:
        ResolutionNotFound: if no such resolution is stored.
        JustWatchError: if the chosen id cannot be looked up. Nothing is written
            in that case -- a half-applied fix would leave a title marked as
            decided by hand and pointing at nothing.
    """
    resolution = session.scalars(
        select(TitleResolution).where(
            TitleResolution.id == resolution_id,
            TitleResolution.user_id == user_id,
        )
    ).one_or_none()
    if resolution is None:
        raise ResolutionNotFound(f"no stored resolution with id {resolution_id}")

    title = _title_for_node(session, catalogue, node_id)

    resolution.title_id = title.id
    resolution.method = MatchMethod.MANUAL
    resolution.confidence = 1.0
    resolution.reason = f"chosen by hand: {title.title}"
    # Stamped with when the decision was made, not left at when the matcher gave
    # up. The column defaults on insert only, so without this a list of recent
    # decisions would be ordered by when each refusal was recorded -- and the
    # fix somebody just made would appear wherever the original refusal fell.
    resolution.resolved_at = now or datetime.now(UTC)
    # candidates is deliberately left as it was. It is the record of what the
    # matcher was choosing between when it gave up, which is both the audit of
    # why a person was asked and what a change of mind re-picks from.

    event_ids = _event_ids_for(session, user_id, resolution.query_key, resolution.kind)
    linked = _apply(session, event_ids, title.id, ResolveSummary()).linked_events

    session.commit()
    session.expire_all()
    return ManualResolution(resolution_id=resolution.id, title=title, linked_events=linked)


def _unlinked_event_counts(session: Session, user_id: str) -> Counter[tuple[str, TitleKind]]:
    """How many unlinked rows each distinct question is holding up.

    Only the unlinked ones are read: they are precisely the rows a fix would
    change, and on a library that has been resolved there are almost none.
    """
    rows = session.execute(
        select(WatchEvent.title, WatchEvent.kind).where(
            WatchEvent.user_id == user_id,
            WatchEvent.title_id.is_(None),
        )
    ).all()
    return Counter((normalize_title(title), kind) for title, kind in rows)


def _event_ids_for(session: Session, user_id: str, key: str, kind: TitleKind) -> list[int]:
    """Every event asking one question.

    The key is a normalised form of the title, which the database cannot
    compute, so the match happens in Python over a single query rather than in
    a WHERE clause. That is fine here: a manual fix is one title at a time.
    """
    rows = session.execute(
        select(WatchEvent.id, WatchEvent.title).where(
            WatchEvent.user_id == user_id,
            WatchEvent.kind == kind,
        )
    ).all()
    return [event_id for event_id, title in rows if normalize_title(title) == key]


def _title_for_node(session: Session, catalogue: CatalogueLookup, node_id: str) -> Title:
    """The stored catalogue row for an id, fetched if we do not have it.

    Two refusals can legitimately point at the same film, and the second fix
    should not spend a request re-reading a row we already hold.
    """
    existing = session.scalars(select(Title).where(Title.jw_node_id == node_id)).one_or_none()
    if existing is not None:
        return existing

    entry = catalogue.details(node_id)
    title = title_from_entry(entry)
    session.add(title)
    session.flush()
    # Free, exactly as in the automatic pass: the lookup returned the offers
    # alongside everything else.
    store_offers(session, title, entry.offers, country=catalogue.country)
    return title


def _questions(session: Session, user_id: str) -> list[_Question]:
    """Group the whole library into the distinct questions it asks.

    Two rows spelled differently but normalising alike -- "The Office (U.S.)"
    and "The Office" -- are one question, so the grouping happens on the
    normalised key rather than in SQL on the raw string. That is also why this
    reads columns rather than ORM objects: it touches every row in the library,
    and hydrating thousands of mapped instances to read four fields is waste.
    """
    rows = session.execute(
        select(
            WatchEvent.id,
            WatchEvent.title,
            WatchEvent.kind,
            WatchEvent.title_ambiguous,
        ).where(WatchEvent.user_id == user_id)
    ).all()

    questions: dict[tuple[str, TitleKind], _Question] = {}
    for event_id, title, kind, ambiguous in rows:
        key = normalize_title(title)
        # First spelling seen wins as the display form. Any of them is a fair
        # thing to show a person, and picking deterministically keeps a re-run
        # from rewriting the row for no reason.
        question = questions.setdefault(
            (key, kind),
            _Question(key=key, kind=kind, display_title=title, ambiguous=ambiguous),
        )
        question.event_ids.append(event_id)
    return list(questions.values())


def _still_to_ask(stored: TitleResolution | None, retry_unresolved: bool) -> bool:
    """Whether another pass would spend a request on this question.

    One definition, used both to decide what to search and to count what is
    left, so the two can never drift into disagreeing about what "done" means.
    """
    return stored is None or _should_ask_again(stored, retry_unresolved)


def _stored_resolutions(
    session: Session, user_id: str
) -> dict[tuple[str, TitleKind], TitleResolution]:
    resolutions = session.scalars(select(TitleResolution).where(TitleResolution.user_id == user_id))
    return {(row.query_key, row.kind): row for row in resolutions}


def _should_ask_again(stored: TitleResolution, retry_unresolved: bool) -> bool:
    """Whether a stored answer should be replaced by a fresh search."""
    if stored.method == MatchMethod.MANUAL:
        # Never. Someone decided this, and an automatic pass does not get to
        # disagree with them.
        return False
    if stored.method == MatchMethod.UNRESOLVED:
        return retry_unresolved
    # An accepted automatic match is left alone; re-asking would spend a request
    # to confirm what we already believe.
    return False


def _match(question: _Question, entries: list[CatalogueEntry]) -> MatchResult:
    """Choose among the search results, or decline to.

    The search is deliberately not filtered by object type. Handing the matcher
    only shows when we expect a show would hide "Fargo" the film -- and a
    plausible entry of the wrong kind is exactly the evidence that should make
    the matcher hesitate rather than something to conceal from it.
    """
    return match_title(
        MatchQuery(
            title=question.display_title,
            kind=question.kind,
            ambiguous=question.ambiguous,
        ),
        [entry.as_candidate() for entry in entries],
    )


def _titles_by_node(session: Session) -> dict[str, Title]:
    """Every catalogue row already stored, keyed by its JustWatch id.

    Read once for the same reason as everything else here: several titles in a
    library legitimately resolve to one catalogue entry, and looking each up
    separately turns "do we have this?" into a query per search.
    """
    return {title.jw_node_id: title for title in session.scalars(select(Title))}


def _chosen_entry(entries: list[CatalogueEntry], result: MatchResult) -> CatalogueEntry | None:
    """The full search result the matcher settled on, or None if it declined.

    The matcher works on a reduced candidate, so what it returns is not enough
    to store a title or its offers -- this finds its way back to the entry the
    candidate came from.
    """
    if result.chosen is None:
        return None
    return next(entry for entry in entries if entry.node_id == result.chosen.node_id)


def _title_for(
    session: Session,
    titles: dict[str, Title],
    entry: CatalogueEntry | None,
) -> Title | None:
    """The catalogue row for the chosen entry, stored if it is new."""
    if entry is None:
        return None

    existing = titles.get(entry.node_id)
    if existing is not None:
        return existing

    title = title_from_entry(entry)
    session.add(title)
    session.flush()
    titles[entry.node_id] = title
    return title


def _record(
    session: Session,
    question: _Question,
    result: MatchResult,
    title: Title | None,
    stored: TitleResolution | None,
    user_id: str,
) -> None:
    """Write down the answer, including when the answer is "I don't know"."""
    resolution = stored or TitleResolution(
        user_id=user_id, query_key=question.key, kind=question.kind
    )
    resolution.query_title = question.display_title
    resolution.title_id = title.id if title else None
    resolution.method = result.method
    resolution.confidence = result.confidence
    resolution.reason = result.reason
    # Everything weighed, best first, including what was rejected. This list is
    # what the fixer UI turns into a set of buttons.
    resolution.candidates = [_as_json(scored.candidate) for scored in result.ranked]
    session.add(resolution)


def _as_json(candidate: Candidate) -> dict:
    return {
        "node_id": candidate.node_id,
        "title": candidate.title,
        "object_type": candidate.object_type,
        "release_year": candidate.release_year,
    }


def _apply(
    session: Session,
    event_ids: list[int],
    title_id: int | None,
    summary: ResolveSummary,
) -> ResolveSummary:
    """Point every event asking this question at the answer.

    Runs for cached answers too, not only fresh ones: rows imported after a
    resolution was stored still need linking, and re-searching for them would
    waste a request on a question already answered. That makes the "already
    correct" case the common one, so rows that would not change are excluded --
    otherwise a second pass reports thousands of links it did not make.
    """
    if title_id is None:
        return summary

    linked = 0
    for start in range(0, len(event_ids), _ID_UPDATE_CHUNK):
        batch = event_ids[start : start + _ID_UPDATE_CHUNK]
        linked += session.execute(
            update(WatchEvent)
            .where(
                WatchEvent.id.in_(batch),
                WatchEvent.title_id.is_distinct_from(title_id),
            )
            .values(title_id=title_id)
            .execution_options(synchronize_session=False)
        ).rowcount
    return _counted(summary, linked_events=linked)


def _counted(
    summary: ResolveSummary,
    *,
    searched: int = 0,
    resolved: int = 0,
    unresolved: int = 0,
    failed: int = 0,
    linked_events: int = 0,
) -> ResolveSummary:
    return ResolveSummary(
        searched=summary.searched + searched,
        resolved=summary.resolved + int(resolved),
        unresolved=summary.unresolved + int(unresolved),
        failed=summary.failed + failed,
        linked_events=summary.linked_events + linked_events,
    )

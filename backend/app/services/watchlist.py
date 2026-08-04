"""The list of things somebody has decided they want to watch.

This is the only place in the app that is *told* something. Everything else
about a person is inferred -- their taste from what they watched, their mood
from a button, their evening from a slider -- and inference is always a guess.
A watchlist entry is not a guess, and the rules here exist to keep it that way.

Two of them are worth stating outright.

**Adding twice is one decision, not two.** A button gets pressed twice, a page
gets submitted again, a title gets added from two different screens. None of
those is a new intention, so an add that finds the title already waiting leaves
its date alone -- reordering somebody's list because they clicked again is a
small betrayal of the only thing on it they actually chose.

**Ticking off and removing are different.** "I have seen this" and "I do not
want this any more" happen to have the same effect on the list, and completely
different effects on everything else: the first is a reason never to recommend
the title again, the second is not. Merging them would either lose watches or
suppress titles somebody merely lost interest in.

This module is impure: it owns the session.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import DEFAULT_USER_ID, Title, WatchlistItem


class TitleNotInCatalogue(ValueError):
    """A watchlist entry was asked for against a title we do not have."""


class WatchlistItemNotFound(LookupError):
    """A change was asked for against a title that is not on the list."""


def add(
    session: Session,
    title_id: int,
    *,
    note: str | None = None,
    now: datetime | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> WatchlistItem:
    """Put a title on the list, or leave it where it already is.

    Adding something already waiting keeps the date it was first added, so the
    list does not reshuffle under somebody who clicked twice. Adding something
    already ticked off does move it, because wanting to see a film again is a
    genuinely new decision rather than a repeat of the old one.

    A note is only written when one is passed. ``add`` is called from places that
    know nothing about notes -- the recommendation card's "save for later" -- and
    those must not wipe a reason somebody typed on the watchlist page.

    Raises:
        TitleNotInCatalogue: if there is no such title. Checked rather than left
            to the foreign key, because SQLite does not enforce foreign keys
            unless asked to, and a dangling row would only surface much later as
            a watchlist page that cannot render one of its entries.
    """
    when = now or datetime.now(UTC)
    if session.get(Title, title_id) is None:
        raise TitleNotInCatalogue(f"no title with id {title_id}")

    item = _find(session, title_id, user_id=user_id)
    if item is None:
        item = WatchlistItem(user_id=user_id, title_id=title_id, added_at=when)
        session.add(item)
    elif item.watched_at is not None:
        item.watched_at = None
        item.added_at = when

    if note is not None:
        item.note = _clean(note)

    session.commit()
    return item


def entries(
    session: Session,
    *,
    include_watched: bool = False,
    user_id: str = DEFAULT_USER_ID,
) -> list[WatchlistItem]:
    """The list, newest decision first.

    Ticked-off entries are left out unless asked for: the ordinary question is
    "what have I still got waiting", and a list that keeps growing with things
    already seen stops being a list somebody reads.

    The titles come back with them in one further query rather than one per row.
    A watchlist page shows a poster, a year and a runtime for every entry, and
    lazily loading those is invisible at three entries and absurd at three
    hundred.
    """
    query = (
        select(WatchlistItem)
        .options(selectinload(WatchlistItem.title))
        .where(WatchlistItem.user_id == user_id)
    )
    if not include_watched:
        query = query.where(WatchlistItem.watched_at.is_(None))

    # The id breaks ties, so two titles added in the same request keep a stable
    # order instead of whichever one the database happens to hand back first.
    query = query.order_by(WatchlistItem.added_at.desc(), WatchlistItem.id.desc())
    return list(session.scalars(query))


def set_watched(
    session: Session,
    title_id: int,
    *,
    watched: bool,
    now: datetime | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> WatchlistItem:
    """Say whether something on the list has been seen.

    This exists because not everything gets watched where we can see it. A film
    seen at a friend's house appears in no export, so without a way to say so by
    hand it stays on the list and stays recommendable for ever.

    Ticking off something already ticked off does not move the date. The date
    answers "when did they say they had seen it", and a second click is not a
    second viewing.

    Raises:
        WatchlistItemNotFound: if the title is not on the list.
    """
    when = now or datetime.now(UTC)
    item = entry(session, title_id, user_id=user_id)

    if not watched:
        item.watched_at = None
    elif item.watched_at is None:
        item.watched_at = when

    session.commit()
    return item


def set_note(
    session: Session,
    title_id: int,
    *,
    note: str | None,
    user_id: str = DEFAULT_USER_ID,
) -> WatchlistItem:
    """Replace the reason attached to an entry. ``None`` clears it.

    Unlike :func:`add`, this always writes what it is given: it is only ever
    called by somebody looking at the note, so an empty one means "delete this"
    rather than "I had nothing to say about it".

    Raises:
        WatchlistItemNotFound: if the title is not on the list.
    """
    item = entry(session, title_id, user_id=user_id)
    item.note = _clean(note)
    session.commit()
    return item


def remove(session: Session, title_id: int, *, user_id: str = DEFAULT_USER_ID) -> bool:
    """Take a title off the list entirely. Returns whether it was there.

    Deliberately a real delete, and deliberately not the same thing as ticking
    it off: this says "I no longer want this", which is no reason at all to stop
    recommending it later. Somebody who removes a film they went cold on should
    still be told about it in a year.

    The catalogue row is untouched -- it is JustWatch's and is shared with the
    history and the offer cache.
    """
    item = _find(session, title_id, user_id=user_id)
    if item is None:
        return False

    session.delete(item)
    session.commit()
    return True


def pending_ids(session: Session, *, user_id: str = DEFAULT_USER_ID) -> set[int]:
    """The titles still waiting, as the scorer's ``on_watchlist`` input.

    Ids rather than rows, and one query rather than one per candidate: the
    recommender asks this once and then tests a few hundred candidates against
    the answer.

    Ticked-off entries are excluded. Having watched something and wanting to
    watch it are opposites, and a bonus for something already seen would be
    precisely backwards.
    """
    rows = session.scalars(
        select(WatchlistItem.title_id).where(
            WatchlistItem.user_id == user_id,
            WatchlistItem.watched_at.is_(None),
        )
    )
    return set(rows)


def entry(session: Session, title_id: int, *, user_id: str = DEFAULT_USER_ID) -> WatchlistItem:
    """One entry, by the title it points at.

    Keyed by the title rather than by the row's own id, as every route here is:
    the title id is what the caller already has -- off a recommendation, out of
    the library -- and making them look up a watchlist id first would be a round
    trip to learn a number that exists only because rows need one.

    Raises:
        WatchlistItemNotFound: if the title is not on the list.
    """
    item = _find(session, title_id, user_id=user_id)
    if item is None:
        raise WatchlistItemNotFound(f"title {title_id} is not on the watchlist")
    return item


def _find(session: Session, title_id: int, *, user_id: str) -> WatchlistItem | None:
    return session.scalars(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user_id,
            WatchlistItem.title_id == title_id,
        )
    ).one_or_none()


def _clean(note: str | None) -> str | None:
    """Whitespace out of an empty text box is not a reason somebody wrote."""
    if note is None:
        return None
    stripped = note.strip()
    return stripped or None

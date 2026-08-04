"""Endpoints for the list of things somebody has decided they want to watch.

Every route is keyed by the title id rather than by the watchlist row's own id.
The title id is what a caller already has -- off a recommendation card, out of
the library -- and there is at most one entry per title, so asking them to look
up a second identifier first would be a round trip to learn a number that exists
only because rows need one.

Nothing here touches the network: the watchlist points at titles the catalogue
has already given us.
"""

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import SessionDep
from app.core.genres import genre_name
from app.models import WatchlistItem
from app.schemas import WatchlistAddRequest, WatchlistItemResponse, WatchlistUpdateRequest
from app.services.watchlist import (
    TitleNotInCatalogue,
    WatchlistItemNotFound,
    add,
    entries,
    entry,
    remove,
    set_note,
    set_watched,
)

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistItemResponse])
def mine(session: SessionDep, include_watched: bool = False) -> list[WatchlistItemResponse]:
    """The list, newest decision first.

    Args:
        include_watched: also return entries already ticked off. Off by default:
            the ordinary question is "what have I still got waiting", and a list
            that keeps growing with things already seen stops being read.
    """
    return [_as_response(item) for item in entries(session, include_watched=include_watched)]


@router.post("", response_model=WatchlistItemResponse)
def add_title(body: WatchlistAddRequest, session: SessionDep) -> WatchlistItemResponse:
    """Put a title on the list.

    Adding something already there is not an error and does not make a second
    entry -- the button lives on the recommendation card, which somebody may
    well press again after a refresh. 200 rather than 201 for that reason: what
    was asked for is "make sure this is on my list", and whether a row had to be
    created to satisfy that is our bookkeeping rather than the client's business.
    """
    try:
        item = add(session, body.title_id, note=body.note)
    except TitleNotInCatalogue as error:
        # The title is not in the catalogue at all, which is a different problem
        # from an entry that is not on the list, and has a different fix:
        # resolve the library rather than add the entry again.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return _as_response(item)


@router.patch("/{title_id}", response_model=WatchlistItemResponse)
def change(
    title_id: int, body: WatchlistUpdateRequest, session: SessionDep
) -> WatchlistItemResponse:
    """Tick an entry off, un-tick it, or change its note.

    What was *sent* decides what changes, not the values that arrived: a null
    note clears it, and a note left out entirely leaves it alone. Those have to
    mean different things, because the client ticking something off knows
    nothing about whatever reason somebody typed months ago.
    """
    sent = body.model_fields_set
    try:
        if "watched" in sent and body.watched is not None:
            set_watched(session, title_id, watched=body.watched)
        if "note" in sent:
            set_note(session, title_id, note=body.note)
        return _as_response(entry(session, title_id))
    except WatchlistItemNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.delete("/{title_id}", status_code=status.HTTP_204_NO_CONTENT)
def drop(title_id: int, session: SessionDep) -> Response:
    """Take a title off the list entirely.

    Deliberately not the same as ticking it off. This says "I no longer want
    this", which is no reason to stop recommending it later -- somebody who went
    cold on a film should still be told about it in a year.

    Removing something that is not there is a 404 rather than a quiet success.
    The effect is the same either way, but a client that believed it had the
    entry is looking at a stale list and should find out.
    """
    if not remove(session, title_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"title {title_id} is not on the watchlist",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _as_response(item: WatchlistItem) -> WatchlistItemResponse:
    return WatchlistItemResponse(
        title_id=item.title_id,
        jw_node_id=item.title.jw_node_id,
        title=item.title.title,
        object_type=item.title.object_type,
        release_year=item.title.release_year,
        runtime_minutes=item.title.runtime_minutes,
        # In English, as everywhere a client reads genres. See api/recommend.py.
        genres=[genre_name(code) for code in item.title.genres or ()],
        poster_url=item.title.poster_url,
        imdb_score=item.title.imdb_score,
        added_at=item.added_at,
        watched_at=item.watched_at,
        note=item.note,
    )

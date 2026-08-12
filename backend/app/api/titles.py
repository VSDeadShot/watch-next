"""Endpoints for turning watch history into catalogue titles.

One workflow in three routes: run a pass, look at what it could not decide,
decide it yourself. Resolution is a separate step from importing so that an
upload stays fast and a JustWatch outage delays resolution rather than rejecting
a file.
"""

from fastapi import APIRouter, HTTPException, Query, status
from simplejustwatchapi.exceptions import JustWatchApiError, JustWatchError

from app.api.deps import CatalogueDep, SessionDep
from app.core.title_parser import TitleKind
from app.schemas import (
    ManualResolutionRequest,
    ManualResolutionResponse,
    ResolvedTitleResponse,
    ResolveSummaryResponse,
    TitleCandidate,
    UnresolvedPageResponse,
    UnresolvedTitleResponse,
)
from app.services.justwatch_client import MAX_REQUESTS_PER_PASS, UnusableCatalogueEntry
from app.services.resolver import (
    RECENT_RESOLUTIONS,
    ResolutionNotFound,
    recent_resolutions,
    resolve_library,
    resolve_manually,
    search_candidates,
    unresolved_page,
)
from app.services.single_flight import PassAlreadyRunning

router = APIRouter(prefix="/api/titles", tags=["titles"])

# The shortest search worth spending a request on. Anything below this cannot
# narrow the catalogue and still costs the same second of the rate limit.
MINIMUM_SEARCH_LENGTH = 2


@router.post("/resolve", response_model=ResolveSummaryResponse)
def resolve(
    session: SessionDep,
    catalogue: CatalogueDep,
    retry_unresolved: bool = False,
    limit: int | None = Query(None, ge=1, le=MAX_REQUESTS_PER_PASS),
) -> ResolveSummaryResponse:
    """Look up distinct titles that do not already have an answer.

    Args:
        retry_unresolved: ask again about titles previously refused. Off by
            default: the catalogue rarely changes between two runs an hour
            apart, and asking costs requests against an unofficial API.
        limit: make at most this many searches. Omitted means the whole
            library, which is the right default for a script and the wrong one
            for a browser -- the pass paces itself at a request a second, so a
            real history is minutes inside a single request. A caller with a
            person watching sends a small limit and repeats until ``remaining``
            reaches zero, which is also what makes the work stoppable.

    A search that fails is counted, not raised. Resolution walks a whole
    library, and abandoning the rest of it because one request timed out would
    mean starting over.
    """
    try:
        summary = resolve_library(
            session, catalogue, retry_unresolved=retry_unresolved, limit=limit
        )
    except PassAlreadyRunning as error:
        # 409 rather than 429: nothing here is rate limiting the caller, and
        # waiting a moment will not help. The state of the process conflicts
        # with the request, and the fix is to let the other pass finish.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return ResolveSummaryResponse(
        searched=summary.searched,
        resolved=summary.resolved,
        unresolved=summary.unresolved,
        failed=summary.failed,
        linked_events=summary.linked_events,
        remaining=summary.remaining,
    )


@router.get("/unresolved", response_model=UnresolvedPageResponse)
def unresolved(
    session: SessionDep,
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> UnresolvedPageResponse:
    """Titles the matcher declined, most consequential first.

    Nothing else in the app makes a refusal visible -- the events just have no
    link -- so this is what a person works through.

    Paged, because the queue is as long as the import went badly and every row
    carries its own list of rejected candidates. The worst case for one large
    response is exactly the case that produces one.
    """
    page = unresolved_page(session, limit=limit, offset=offset)
    return UnresolvedPageResponse(
        total=page.total,
        items=[
            UnresolvedTitleResponse(
                resolution_id=title.resolution_id,
                query_title=title.query_title,
                kind=title.kind,
                reason=title.reason,
                event_count=title.event_count,
                candidates=[TitleCandidate(**candidate) for candidate in title.candidates],
            )
            for title in page.items
        ],
    )


@router.get("/search", response_model=list[TitleCandidate])
def search(
    catalogue: CatalogueDep,
    q: str = Query(min_length=MINIMUM_SEARCH_LENGTH),
    kind: TitleKind | None = None,
) -> list[TitleCandidate]:
    """Look the catalogue up by a name somebody typed.

    The way out when the stored candidates are no help, which is the case the
    matcher could never have got right on its own: a title misspelled in the
    export, or one known by a different name in this country.

    Args:
        q: at least two characters once trimmed. A single letter costs a request
            against a rate-limited API and cannot narrow anything -- and neither
            can a box someone left holding spaces, which is why the length is
            checked after stripping rather than by the validator alone.
        kind: narrow to films or to series. Omitted means neither, and that is
            the honest default -- the parser's reading of a title is itself a
            common reason a row needed fixing, so a filter taken from that same
            reading is exactly what would hide the right answer.
    """
    query = q.strip()
    if len(query) < MINIMUM_SEARCH_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"search for at least {MINIMUM_SEARCH_LENGTH} characters",
        )

    try:
        found = search_candidates(catalogue, query, kind=kind)
    except JustWatchApiError as error:
        # The API answered, and the answer was unusable. Nothing to retry.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="JustWatch could not answer that search",
        ) from error
    except JustWatchError as error:
        # Timed out, refused the connection, or answered 5xx three times over.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="could not reach JustWatch to search; try again shortly",
        ) from error

    return [
        TitleCandidate(
            node_id=candidate.node_id,
            title=candidate.title,
            object_type=candidate.object_type,
            release_year=candidate.release_year,
        )
        for candidate in found
    ]


@router.get("/resolutions", response_model=list[ResolvedTitleResponse])
def decided(
    session: SessionDep,
    limit: int = Query(RECENT_RESOLUTIONS, ge=1, le=100),
) -> list[ResolvedTitleResponse]:
    """What somebody decided by hand, most recently decided first.

    A manual answer leaves the unresolved queue the moment it is given, so this
    is the only way back to one that was given wrongly. Automatic matches are
    deliberately absent: a choice somebody made is the only kind of choice
    somebody might want back.
    """
    return [
        ResolvedTitleResponse(
            resolution_id=decision.resolution_id,
            query_title=decision.query_title,
            kind=decision.kind,
            title_id=decision.title_id,
            jw_node_id=decision.jw_node_id,
            title=decision.title,
            object_type=decision.object_type,
            release_year=decision.release_year,
            poster_url=decision.poster_url,
            resolved_at=decision.resolved_at,
            candidates=[TitleCandidate(**candidate) for candidate in decision.candidates],
        )
        for decision in recent_resolutions(session, limit=limit)
    ]


@router.put("/resolutions/{resolution_id}", response_model=ManualResolutionResponse)
def choose(
    resolution_id: int,
    body: ManualResolutionRequest,
    session: SessionDep,
    catalogue: CatalogueDep,
) -> ManualResolutionResponse:
    """Record the answer a person gave, and link every row waiting on it.

    The choice is final: later automatic passes leave it alone. Sending it again
    with a different id is how a mistaken fix gets corrected.
    """
    try:
        fixed = resolve_manually(
            session, catalogue, resolution_id=resolution_id, node_id=body.node_id
        )
    except ResolutionNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (JustWatchApiError, UnusableCatalogueEntry) as error:
        # JustWatch answered and had nothing usable under that id. That is the
        # caller naming something that does not exist, not an outage, and the
        # difference is "pick something else" rather than "try again".
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"JustWatch has no usable title with id {body.node_id!r}",
        ) from error
    except JustWatchError as error:
        # Timed out, refused the connection, or answered 5xx three times over.
        # Nothing was written, so retrying the same request is safe.
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="could not reach JustWatch to confirm that title; try again shortly",
        ) from error

    return ManualResolutionResponse(
        resolution_id=fixed.resolution_id,
        title_id=fixed.title.id,
        jw_node_id=fixed.title.jw_node_id,
        title=fixed.title.title,
        object_type=fixed.title.object_type,
        release_year=fixed.title.release_year,
        poster_url=fixed.title.poster_url,
        linked_events=fixed.linked_events,
    )

"""Endpoints for turning watch history into catalogue titles.

One workflow in three routes: run a pass, look at what it could not decide,
decide it yourself. Resolution is a separate step from importing so that an
upload stays fast and a JustWatch outage delays resolution rather than rejecting
a file.
"""

from fastapi import APIRouter, HTTPException, status
from simplejustwatchapi.exceptions import JustWatchApiError, JustWatchError

from app.api.deps import CatalogueDep, SessionDep
from app.schemas import (
    ManualResolutionRequest,
    ManualResolutionResponse,
    ResolveSummaryResponse,
    TitleCandidate,
    UnresolvedTitleResponse,
)
from app.services.justwatch_client import UnusableCatalogueEntry
from app.services.resolver import (
    ResolutionNotFound,
    resolve_library,
    resolve_manually,
    unresolved_titles,
)

router = APIRouter(prefix="/api/titles", tags=["titles"])


@router.post("/resolve", response_model=ResolveSummaryResponse)
def resolve(
    session: SessionDep,
    catalogue: CatalogueDep,
    retry_unresolved: bool = False,
) -> ResolveSummaryResponse:
    """Look up every distinct title that does not already have an answer.

    Args:
        retry_unresolved: ask again about titles previously refused. Off by
            default: the catalogue rarely changes between two runs an hour
            apart, and asking costs requests against an unofficial API.

    A search that fails is counted, not raised. Resolution walks a whole
    library, and abandoning the rest of it because one request timed out would
    mean starting over.
    """
    summary = resolve_library(session, catalogue, retry_unresolved=retry_unresolved)
    return ResolveSummaryResponse(
        searched=summary.searched,
        resolved=summary.resolved,
        unresolved=summary.unresolved,
        failed=summary.failed,
        linked_events=summary.linked_events,
    )


@router.get("/unresolved", response_model=list[UnresolvedTitleResponse])
def unresolved(session: SessionDep) -> list[UnresolvedTitleResponse]:
    """Titles the matcher declined, most consequential first.

    Nothing else in the app makes a refusal visible -- the events just have no
    link -- so this is what a person works through.
    """
    return [
        UnresolvedTitleResponse(
            resolution_id=title.resolution_id,
            query_title=title.query_title,
            kind=title.kind,
            reason=title.reason,
            event_count=title.event_count,
            candidates=[TitleCandidate(**candidate) for candidate in title.candidates],
        )
        for title in unresolved_titles(session)
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

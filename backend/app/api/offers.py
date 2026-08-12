"""Refreshing the availability cache.

Availability is a hard filter rather than a ranking signal: a title the user
cannot press play on is not a candidate at any score. That makes a stale offer
cache worse than a slow one -- it does not degrade the answer, it makes the
answer wrong while looking entirely confident.

Offers are written once when a title is resolved or discovered, and nothing
refreshed them afterwards. This is the router that does, driven in batches for
the same reason `/api/titles/resolve` is: the pass paces itself at a request a
second against an unofficial API, so a whole catalogue inside one HTTP request
is minutes of a browser waiting with no way out.
"""

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CatalogueDep, SessionDep
from app.schemas import RefreshSummaryResponse
from app.services.justwatch_client import MAX_REQUESTS_PER_PASS
from app.services.offers import refresh_stale_offers
from app.services.single_flight import PassAlreadyRunning

router = APIRouter(prefix="/api/offers", tags=["offers"])


@router.post("/refresh", response_model=RefreshSummaryResponse)
def refresh(
    session: SessionDep,
    catalogue: CatalogueDep,
    limit: int | None = Query(None, ge=1, le=MAX_REQUESTS_PER_PASS),
) -> RefreshSummaryResponse:
    """Re-ask JustWatch about the titles whose availability has gone stale.

    Args:
        limit: spend at most this many requests. Omitted means the module's own
            default rather than the whole catalogue -- a refresh is a budget,
            and the oldest answers are asked about first, so a partial pass is
            always the most useful partial pass available. A caller with a
            person watching sends a small limit and repeats until ``remaining``
            reaches zero.

    A request that fails is counted rather than raised, and the title keeps its
    old ``offers_fetched_at``: marking it fetched would buy it another week of
    being treated as known when nothing was learned about it at all.
    """
    try:
        summary = (
            refresh_stale_offers(session, catalogue)
            if limit is None
            else refresh_stale_offers(session, catalogue, limit=limit)
        )
    except PassAlreadyRunning as error:
        # 409 rather than 429: nothing is rate limiting this caller, and trying
        # again in a second will not help. The conflict is with a pass already
        # running, and the fix is to let it finish.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return RefreshSummaryResponse(
        refreshed=summary.refreshed,
        failed=summary.failed,
        offers_stored=summary.offers_stored,
        remaining=summary.remaining,
    )

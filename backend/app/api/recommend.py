"""The endpoint the whole app is built around: one thing to watch.

A POST rather than a GET, and not because anything here is a resource being
created. The request carries a mood, a time budget and a list of titles already
turned down, which is a body rather than a query string -- and answering it
tops the discovery pool up and writes down what was suggested, so it is not a
read either.

The contract enforces the product decision. ``title`` is one object or null.
There is no field on the response that could hold a second answer, so no client
can quietly turn this into a feed.
"""

from fastapi import APIRouter

from app.api.deps import CatalogueDep, SessionDep
from app.core.genres import genre_name
from app.core.scoring import RecommendationRequest
from app.schemas import (
    ConsideredResponse,
    RecommendationRequestBody,
    RecommendationResponse,
    RecommendedTitleResponse,
    WatchOnResponse,
)
from app.services.recommender import Pick, recommend

router = APIRouter(prefix="/api", tags=["recommend"])


@router.post("/recommend", response_model=RecommendationResponse)
def recommend_one(
    body: RecommendationRequestBody, session: SessionDep, catalogue: CatalogueDep
) -> RecommendationResponse:
    """Choose one thing to watch, or say why there is nothing.

    Nothing to recommend is a 200 with a null title and a sentence explaining
    it, not a 404. The route worked and the answer is real: "nothing on your
    services fits forty minutes" is information somebody can act on, and an
    error status would have the client render a failure instead of showing it.
    """
    result = recommend(
        session,
        catalogue,
        RecommendationRequest(
            mood=body.mood,
            minutes_available=body.minutes_available,
            kind=body.kind,
        ),
        exclude_ids=body.exclude_ids,
    )

    return RecommendationResponse(
        title=_as_response(result.pick) if result.pick else None,
        reason=result.reason,
        considered=ConsideredResponse(
            pool=result.considered.pool,
            available=result.considered.available,
            eligible=result.considered.eligible,
        ),
    )


def _as_response(pick: Pick) -> RecommendedTitleResponse:
    return RecommendedTitleResponse(
        title_id=pick.title.id,
        jw_node_id=pick.title.jw_node_id,
        title=pick.title.title,
        object_type=pick.title.object_type,
        release_year=pick.title.release_year,
        runtime_minutes=pick.title.runtime_minutes,
        # In English. The codes are JustWatch's private vocabulary and mean
        # nothing outside this backend -- a client given "crm" can only either
        # print it or keep a second copy of our table, and the second copy is
        # the one that silently goes stale.
        genres=[genre_name(code) for code in pick.title.genres or ()],
        poster_url=pick.title.poster_url,
        imdb_score=pick.title.imdb_score,
        score=pick.score,
        reasons=list(pick.reasons),
        watch_on=[
            WatchOnResponse(
                short_name=option.provider,
                name=option.name,
                monetization=option.monetization,
                url=option.url,
                requires_subscription=option.requires_subscription,
            )
            for option in pick.watch_on
        ],
        on_watchlist=pick.on_watchlist,
    )

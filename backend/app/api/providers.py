"""Endpoints for the settings page: which services exist, and which you have.

The two are deliberately separate routes over separate storage. The catalogue is
JustWatch's and is refreshed on request; the subscriptions are the user's and are
the only input the availability filter has. Reading the catalogue never touches
the network, so the settings page loads instantly and works offline once it has
been filled in at least once.
"""

from fastapi import APIRouter, HTTPException, status
from simplejustwatchapi.exceptions import JustWatchError

from app.api.deps import CatalogueDep, SessionDep, SettingsDep, UserDep
from app.schemas import (
    ProviderCatalogueResponse,
    ProviderRefreshResponse,
    ProviderResponse,
    SubscriptionsRequest,
    SubscriptionsResponse,
)
from app.services.providers import (
    UnknownProvider,
    available_providers,
    refresh_providers,
    set_subscriptions,
    subscriptions,
)

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("", response_model=ProviderCatalogueResponse)
def catalogue(session: SessionDep, settings: SettingsDep) -> ProviderCatalogueResponse:
    """The services the picker can offer, alphabetically.

    Reads only what has been stored: a settings page that made a live request
    every time it opened would spend the request budget on a list that changes
    a few times a year. Empty until a refresh has run, which is the frontend's
    cue to offer one.
    """
    return ProviderCatalogueResponse(
        country=settings.jw_country,
        providers=[
            ProviderResponse(
                short_name=provider.short_name,
                name=provider.name,
                technical_name=provider.technical_name,
                icon_url=provider.icon_url,
                monetization_types=list(provider.monetization_types),
            )
            for provider in available_providers(session, country=settings.jw_country)
        ],
    )


@router.post("/refresh", response_model=ProviderRefreshResponse)
def refresh(session: SessionDep, catalogue: CatalogueDep) -> ProviderRefreshResponse:
    """Re-ask JustWatch which services exist here.

    Explicit rather than automatic on read, because it is the one thing here
    that costs a request, and a GET that quietly makes network calls is a GET
    that behaves differently depending on how recently someone else called it.
    """
    try:
        summary = refresh_providers(session, catalogue)
    except JustWatchError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="could not reach JustWatch for the provider list; try again shortly",
        ) from error

    return ProviderRefreshResponse(
        country=catalogue.country,
        fetched=summary.fetched,
        added=summary.added,
        updated=summary.updated,
        removed=summary.removed,
    )


@router.get("/mine", response_model=SubscriptionsResponse)
def mine(session: SessionDep, settings: SettingsDep, user: UserDep) -> SubscriptionsResponse:
    """The services the user says they have."""
    return SubscriptionsResponse(
        country=settings.jw_country,
        short_names=subscriptions(session, country=settings.jw_country, user_id=user),
    )


@router.put("/mine", response_model=SubscriptionsResponse)
def set_mine(
    body: SubscriptionsRequest, session: SessionDep, settings: SettingsDep, user: UserDep
) -> SubscriptionsResponse:
    """Replace the whole set. Sending an empty list cancels everything.

    A PUT rather than a pair of add/remove routes because a picker is a set: the
    client always knows the complete answer, and sending it whole means the
    stored settings cannot drift out of step with what the page is showing.
    """
    try:
        stored = set_subscriptions(
            session, body.short_names, country=settings.jw_country, user_id=user
        )
    except UnknownProvider as error:
        # The client's fault, not an outage: it named a service the catalogue for
        # this country does not list. 400 rather than 404 -- the route exists and
        # it is the body that is wrong.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    return SubscriptionsResponse(country=settings.jw_country, short_names=stored)

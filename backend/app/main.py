"""The FastAPI application.

The schema is owned by Alembic -- run ``alembic upgrade head`` before starting.
The app deliberately does not create tables itself: doing so would build a
schema Alembic's history knows nothing about, and the two would drift apart
silently from then on.

Built by a factory rather than assembled at module scope, because two decisions
made here depend on configuration and both are worth testing: whether an absent
secret is grounds for refusing to start, and whether the reference docs exist at
all. A module that can only be built one way can only be tested one way, and the
interesting case would be the untestable one.
"""

import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import imports, offers, providers, recommend, stats, titles, watchlist
from app.api.security import GateStatus, gate_status, require_api_key
from app.config import Settings, get_settings

_log = logging.getLogger(__name__)

#: Every router in the app, in the order they are mounted. Named as a sequence
#: so that adding one is a single edit rather than two, and so that nothing can
#: be mounted past the gate by writing the include line slightly differently.
_ROUTERS = (imports, titles, providers, recommend, watchlist, stats, offers)


def create_app(settings: Settings) -> FastAPI:
    """Build the application for a given configuration.

    Raises:
        RuntimeError: if the configuration is one that must not serve. Raised
            here rather than reported, because at import time this is what makes
            uvicorn exit non-zero and the platform report a failed deploy with
            the previous version still running.
    """
    gate = gate_status(
        api_secret=settings.api_secret,
        database_url=settings.database_url,
        allow_unauthenticated=settings.allow_unauthenticated,
    )
    if gate is GateStatus.MISCONFIGURED:
        raise RuntimeError(
            "Refusing to start: API_SECRET is empty and DATABASE_URL is not SQLite, "
            "so this is a deployment rather than a laptop. Without the secret this "
            "API answers anyone holding the URL -- the whole viewing history, the "
            "subscriptions, and the JustWatch request budget. Set API_SECRET. If you "
            "genuinely mean to run ungated, a local Postgres being the usual reason, "
            "set ALLOW_UNAUTHENTICATED=true and this becomes a warning."
        )
    if gate is GateStatus.WAIVED_EXPLICIT:
        _log.warning(
            "Starting with no API key required: ALLOW_UNAUTHENTICATED is set. Every "
            "route except /health answers anyone who can reach this process."
        )

    app = FastAPI(
        title="watch-next",
        description="Watch history aggregator and availability-aware recommender.",
        version="0.1.0",
        **_reference_urls(gate),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # The gate goes on the routers rather than on the app, so `/health` stays
    # reachable by construction: Render polls it to decide whether the service is
    # up, and a healthy deployment behind a 401 looks permanently broken.
    # `tests/test_api_security.py` walks the real routing table -- not the
    # OpenAPI schema -- and fails if anything else answers without a key.
    guarded = [Depends(require_api_key)]
    for router in _ROUTERS:
        app.include_router(router.router, dependencies=guarded)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _reference_urls(gate: GateStatus) -> dict[str, str | None]:
    """Where ``/docs``, ``/redoc`` and ``/openapi.json`` live, or nowhere.

    FastAPI mounts these outside any router, so the dependency that guards
    everything else cannot reach them -- and they were consequently answering
    200 on the deployed backend while every real route answered 401, handing
    anyone who found the URL a complete description of all sixteen endpoints.

    The rule is tied to the gate rather than to an environment: documentation is
    an *unauthenticated* surface, so it matters exactly when there is an
    authenticated one to walk around. Where the API is already open -- a laptop,
    or a deployment somebody deliberately waived -- removing the docs would
    protect nothing and cost the one place they are genuinely useful.
    """
    if gate is GateStatus.ENFORCED:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {}


app = create_app(get_settings())

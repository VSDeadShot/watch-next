"""The FastAPI application.

The schema is owned by Alembic -- run ``alembic upgrade head`` before starting.
The app deliberately does not create tables itself: doing so would build a
schema Alembic's history knows nothing about, and the two would drift apart
silently from then on.
"""

import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import imports, offers, providers, recommend, stats, titles, watchlist
from app.api.security import GateStatus, gate_status, require_api_key
from app.config import get_settings

_log = logging.getLogger(__name__)

settings = get_settings()

# Checked at import, which is what makes it a refusal to boot rather than a
# refusal to answer: uvicorn imports this module, so the process exits non-zero
# and the platform reports a failed deploy with the previous version still
# serving. An outage is recoverable. A public viewing history is not, and it is
# the outcome that looks identical to success from outside.
_gate = gate_status(
    api_secret=settings.api_secret,
    database_url=settings.database_url,
    allow_unauthenticated=settings.allow_unauthenticated,
)
if _gate is GateStatus.MISCONFIGURED:
    raise RuntimeError(
        "Refusing to start: API_SECRET is empty and DATABASE_URL is not SQLite, "
        "so this is a deployment rather than a laptop. Without the secret this "
        "API answers anyone holding the URL -- the whole viewing history, the "
        "subscriptions, and the JustWatch request budget. Set API_SECRET. If you "
        "genuinely mean to run ungated, a local Postgres being the usual reason, "
        "set ALLOW_UNAUTHENTICATED=true and this becomes a warning."
    )
if _gate is GateStatus.WAIVED_EXPLICIT:
    _log.warning(
        "Starting with no API key required: ALLOW_UNAUTHENTICATED is set. Every "
        "route except /health answers anyone who can reach this process."
    )

app = FastAPI(
    title="watch-next",
    description="Watch history aggregator and availability-aware recommender.",
    version="0.1.0",
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
# `tests/test_api_security.py` walks the OpenAPI paths and fails if a router is
# ever added without this, which is the mistake the arrangement invites.
guarded = [Depends(require_api_key)]

app.include_router(imports.router, dependencies=guarded)
app.include_router(titles.router, dependencies=guarded)
app.include_router(providers.router, dependencies=guarded)
app.include_router(recommend.router, dependencies=guarded)
app.include_router(watchlist.router, dependencies=guarded)
app.include_router(stats.router, dependencies=guarded)
app.include_router(offers.router, dependencies=guarded)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

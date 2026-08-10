"""The FastAPI application.

The schema is owned by Alembic -- run ``alembic upgrade head`` before starting.
The app deliberately does not create tables itself: doing so would build a
schema Alembic's history knows nothing about, and the two would drift apart
silently from then on.
"""

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import imports, offers, providers, recommend, stats, titles, watchlist
from app.api.security import require_api_key
from app.config import get_settings

settings = get_settings()

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

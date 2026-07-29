"""The FastAPI application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import imports
from app.config import get_settings
from app.db import Base, engine

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create any missing tables on startup.

    Enough while the schema is still moving and the app is single-user and
    local. It creates but never alters, so a migration tool is needed before the
    first deployment that has data worth keeping.
    """
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    lifespan=lifespan,
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

app.include_router(imports.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

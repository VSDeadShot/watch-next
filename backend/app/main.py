"""The FastAPI application.

The schema is owned by Alembic -- run ``alembic upgrade head`` before starting.
The app deliberately does not create tables itself: doing so would build a
schema Alembic's history knows nothing about, and the two would drift apart
silently from then on.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import imports, titles
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

app.include_router(imports.router)
app.include_router(titles.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

"""Turning what JustWatch said about a title into the row we keep.

Two very different paths arrive at the same job. Resolution starts from
something in somebody's history and asks "what is this?"; discovery starts from
nothing and asks "what is worth watching?". Both end up holding a
:class:`CatalogueEntry` that has to become a ``titles`` row, and both would
otherwise write their own version of that conversion -- which is how two paths
end up storing subtly different rows for the same film.

This module is impure: it owns the session.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Title
from app.services.justwatch_client import CatalogueEntry
from app.services.offers import store_offers


def title_from_entry(entry: CatalogueEntry) -> Title:
    """A catalogue row, built from everything JustWatch told us about it."""
    return Title(
        jw_node_id=entry.node_id,
        object_type=entry.object_type,
        title=entry.title,
        release_year=entry.release_year,
        runtime_minutes=entry.runtime_minutes,
        genres=list(entry.genres),
        imdb_id=entry.imdb_id,
        tmdb_id=entry.tmdb_id,
        poster_url=entry.poster_url,
        imdb_score=entry.imdb_score,
        tmdb_score=entry.tmdb_score,
        tomatometer=entry.tomatometer,
    )


def store_title(
    session: Session,
    entry: CatalogueEntry,
    *,
    country: str,
    now: datetime | None = None,
) -> tuple[Title, bool]:
    """Store or update one catalogue row, caching the offers that came with it.

    Returns the row and whether it was new, because a caller counting what a
    discovery pass achieved needs to tell "found something we had never heard
    of" apart from "saw the same fifty titles again".

    An existing row has its metadata replaced rather than left alone. Scores
    move, runtimes get corrected, and a poster URL expires; the entry in hand is
    newer than the row by definition, so keeping the old one would be preferring
    stale data for no reason.

    Does not commit. This runs inside a larger pass, and a pass that failed
    halfway should take its titles down with it.
    """
    when = now or datetime.now(UTC)

    title = session.scalars(select(Title).where(Title.jw_node_id == entry.node_id)).one_or_none()
    is_new = title is None
    if title is None:
        title = title_from_entry(entry)
        session.add(title)
    else:
        fresh = title_from_entry(entry)
        for column in (
            "object_type",
            "title",
            "release_year",
            "runtime_minutes",
            "genres",
            "imdb_id",
            "tmdb_id",
            "poster_url",
            "imdb_score",
            "tmdb_score",
            "tomatometer",
        ):
            setattr(title, column, getattr(fresh, column))
    title.fetched_at = when
    session.flush()

    # Free, exactly as in a resolve pass: whatever asked about this title got
    # the offers back alongside everything else.
    store_offers(session, title, entry.offers, country=country, now=when)
    return title, is_new

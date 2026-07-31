"""Database tables.

``watch_events`` is deliberately append-only and keeps the raw exported title
alongside the parsed reading of it. Parsing improves between releases; the raw
string does not, so keeping it means a better parser can be re-run over history
that has already been imported.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.title_parser import TitleKind
from app.db import Base, UtcDateTime

# The app is single-user for now, but every row is scoped from the first commit
# so that adding accounts later is a config change rather than a migration of
# every table.
DEFAULT_USER_ID = "local"


class ImportRun(Base):
    """One upload, kept so the numbers shown to the user can be re-checked."""

    __tablename__ = "imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default=DEFAULT_USER_ID, index=True)
    source: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str | None] = mapped_column(Text)
    export_format: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())

    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    imported_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    skipped_rows: Mapped[int] = mapped_column(Integer, default=0)

    # {skip reason: count} and any date-order guesses, so the summary the user
    # saw at upload time stays available afterwards.
    skipped_detail: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    assumptions: Mapped[list[str]] = mapped_column(JSON, default=list)

    events: Mapped[list["WatchEvent"]] = relationship(back_populates="import_run")


class WatchEvent(Base):
    """One viewing session, as exported and as parsed."""

    __tablename__ = "watch_events"
    __table_args__ = (
        # The idempotency guarantee: a row already imported cannot be imported
        # again, however many times its export is re-uploaded.
        UniqueConstraint("user_id", "fingerprint", name="uq_watch_events_user_fingerprint"),
        Index("ix_watch_events_user_watched_at", "user_id", "watched_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default=DEFAULT_USER_ID)
    import_id: Mapped[int] = mapped_column(ForeignKey("imports.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))

    source: Mapped[str] = mapped_column(String(32))
    raw_title: Mapped[str] = mapped_column(Text)
    watched_at: Mapped[datetime] = mapped_column(UtcDateTime)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    profile_name: Mapped[str | None] = mapped_column(Text)
    device_type: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(8))

    # The parsed reading of raw_title. `title` is the searchable name: the series
    # for an episode, the film for a film.
    kind: Mapped[TitleKind] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text, index=True)
    season_number: Mapped[int | None] = mapped_column(Integer)
    episode_title: Mapped[str | None] = mapped_column(Text)
    episode_number: Mapped[int | None] = mapped_column(Integer)

    # The kind was inferred from the string's shape rather than proven by a
    # marker, so the JustWatch lookup gets the final say.
    title_ambiguous: Mapped[bool] = mapped_column(default=False)

    # Null until resolution runs, and null afterwards for anything the matcher
    # declined to guess at. Nothing downstream may treat null as "no match" --
    # it means "not decided", which is a different thing and is fixable.
    title_id: Mapped[int | None] = mapped_column(ForeignKey("titles.id"), index=True)

    import_run: Mapped[ImportRun] = relationship(back_populates="events")
    catalogue_title: Mapped["Title | None"] = relationship(back_populates="watch_events")


class Title(Base):
    """A catalogue entry, as JustWatch describes it.

    One row per distinct thing, however many viewing sessions point at it.
    """

    __tablename__ = "titles"

    id: Mapped[int] = mapped_column(primary_key=True)
    jw_node_id: Mapped[str] = mapped_column(String(128), unique=True)

    object_type: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text, index=True)
    release_year: Mapped[int | None] = mapped_column(Integer)
    runtime_minutes: Mapped[int | None] = mapped_column(Integer)
    genres: Mapped[list[str]] = mapped_column(JSON, default=list)

    imdb_id: Mapped[str | None] = mapped_column(String(32))
    # Stored even though nothing reads it yet, so adding TMDB later needs no
    # migration and no re-resolution of the whole library.
    tmdb_id: Mapped[str | None] = mapped_column(String(32))
    poster_url: Mapped[str | None] = mapped_column(Text)

    imdb_score: Mapped[float | None] = mapped_column(Float)
    tmdb_score: Mapped[float | None] = mapped_column(Float)
    tomatometer: Mapped[int | None] = mapped_column(Integer)

    # JustWatch is an unofficial API whose answers change; this says how stale
    # this row is.
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())

    watch_events: Mapped[list[WatchEvent]] = relationship(back_populates="catalogue_title")


class TitleResolution(Base):
    """The answer to "what is this string?", cached and kept auditable.

    Keyed by the normalised title rather than by the row, because a hundred
    episodes of one show ask the same question and the API should be asked once.

    A row exists even when nothing was matched. That is the point: an unresolved
    answer records the candidates that were rejected, so the UI can offer them
    for a one-click manual fix instead of silently showing nothing.
    """

    __tablename__ = "title_resolutions"
    __table_args__ = (
        # The kind is part of the key: "Fargo" the film and "Fargo" the series
        # are the same string and different answers.
        UniqueConstraint("user_id", "query_key", "kind", name="uq_title_resolutions_query"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default=DEFAULT_USER_ID)

    query_key: Mapped[str] = mapped_column(Text)
    kind: Mapped[TitleKind] = mapped_column(String(16))
    # The title as exported, kept only so the fixer UI can show something a
    # person recognises. query_key is normalised past readability.
    query_title: Mapped[str] = mapped_column(Text)

    title_id: Mapped[int | None] = mapped_column(ForeignKey("titles.id"))
    method: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    # Everything the matcher weighed, best first, including whatever it rejected.
    # This is what a person picks from.
    candidates: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # Why nothing was chosen, in plain language, for the fixer UI to show.
    reason: Mapped[str] = mapped_column(Text, default="")

    resolved_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())

    title: Mapped[Title | None] = relationship()

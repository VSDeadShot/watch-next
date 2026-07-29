"""Database tables.

``watch_events`` is deliberately append-only and keeps the raw exported title
alongside the parsed reading of it. Parsing improves between releases; the raw
string does not, so keeping it means a better parser can be re-run over history
that has already been imported.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
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

    import_run: Mapped[ImportRun] = relationship(back_populates="events")

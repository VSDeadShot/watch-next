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

    # When availability was last looked up, which is not the same question as
    # when this row was written. It lives here rather than being inferred from
    # the offers because a title that is streaming nowhere has no offer rows at
    # all -- without this, "we asked and the answer was nothing" and "we have
    # never asked" would be indistinguishable, and the first would be refetched
    # for ever.
    offers_fetched_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    watch_events: Mapped[list[WatchEvent]] = relationship(back_populates="catalogue_title")
    offers: Mapped[list["Offer"]] = relationship(
        back_populates="title", cascade="all, delete-orphan"
    )


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


class Provider(Base):
    """A streaming service, as JustWatch names it in one country.

    Per country on purpose: the same service can carry a different short name,
    offer different things, or not exist at all somewhere else, so a global
    provider list would be wrong everywhere but one place.
    """

    __tablename__ = "providers"
    __table_args__ = (
        UniqueConstraint("country", "short_name", name="uq_providers_country_short_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    country: Mapped[str] = mapped_column(String(8), index=True)
    # The join key for everything: offers name their provider this way, and so
    # does a subscription.
    short_name: Mapped[str] = mapped_column(String(16))

    technical_name: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(Text)
    icon_url: Mapped[str | None] = mapped_column(Text)
    # FLATRATE, RENT, BUY, ADS, FREE -- what this service does at all, which is
    # not the same as what it offers for a given title.
    monetization_types: Mapped[list[str]] = mapped_column(JSON, default=list)

    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())


class UserProvider(Base):
    """A service the user actually has. The availability filter's input.

    Deliberately not a foreign key to ``providers``: a subscription means "I
    have Netflix in India", which stays true across a refresh of the provider
    catalogue. Pointing at a catalogue row would let re-fetching the catalogue
    delete somebody's settings.
    """

    __tablename__ = "user_providers"
    __table_args__ = (
        UniqueConstraint("user_id", "country", "short_name", name="uq_user_providers_subscription"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default=DEFAULT_USER_ID)
    country: Mapped[str] = mapped_column(String(8))
    short_name: Mapped[str] = mapped_column(String(16))
    added_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())


class DiscoveryRun(Base):
    """One top-up of the pool of things nobody here has watched.

    A log rather than a single upserted row, for the same reason ``imports`` is
    one: the interesting question later is not only "when did this last run" but
    "did it keep finding anything", and a row that overwrites itself cannot
    answer the second.

    Its real job is to stop the pool being refilled on every request. Discovery
    costs several requests against an unofficial API, and what is popular in a
    country does not change between two evenings.
    """

    __tablename__ = "discovery_runs"
    __table_args__ = (Index("ix_discovery_runs_user_country", "user_id", "country"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default=DEFAULT_USER_ID)
    country: Mapped[str] = mapped_column(String(8))
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())

    # How many titles came back, and how many of those we had never heard of.
    # A run that returns plenty and adds none means the pool is saturated, which
    # is a different thing from a run that returns nothing at all.
    titles_seen: Mapped[int] = mapped_column(Integer, default=0)
    titles_added: Mapped[int] = mapped_column(Integer, default=0)
    requests_made: Mapped[int] = mapped_column(Integer, default=0)


class Recommendation(Base):
    """One title actually put in front of somebody, and the question it answered.

    Kept for two reasons. The first is not repeating: being told to watch the
    same film four evenings running is how somebody stops opening the app. The
    second is auditability, the same reason ``title_resolutions`` keeps the
    candidates it rejected -- a recommendation with no record of the score and
    the reasons behind it cannot be argued with afterwards, and this app's whole
    claim is that its one answer is defensible.
    """

    __tablename__ = "recommendations"
    __table_args__ = (Index("ix_recommendations_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default=DEFAULT_USER_ID)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())

    # The question. A recommendation without it cannot be judged later: "why did
    # it tell me to watch a documentary" has a good answer only if we still know
    # that the person had asked for something to think about.
    mood: Mapped[str] = mapped_column(String(16))
    minutes_available: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))

    # The answer, and its working.
    score: Mapped[float] = mapped_column(Float, default=0.0)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)

    title: Mapped["Title"] = relationship()


class WatchlistItem(Base):
    """Something somebody has already decided they want to watch.

    The only place in this app where intent is stated rather than inferred.
    Everything else about a person here is read off what they did -- what they
    watched, how recently, how much of it -- and inference is always a guess. A
    watchlist entry is not a guess, which is why it earns a bonus in the scoring
    rather than being just another signal, and why it is stored separately from
    the history it will eventually become.

    ``watched_at`` is how something leaves the list without being deleted, and it
    exists because not everything gets watched where we can see it. A film seen
    at a friend's house never appears in any export, so without a way to say so
    by hand it would be recommended for ever. Ticked-off items are excluded from
    recommendations exactly as imported history is.

    Deleting a row and ticking it off are deliberately different actions.
    "I have seen this" and "I no longer want to" are different facts, and only
    the first should keep the title out of future suggestions on the grounds
    that it has been watched.
    """

    __tablename__ = "watchlist"
    __table_args__ = (
        # One entry per title. Adding something twice is a person pressing a
        # button twice, not a request for two copies.
        UniqueConstraint("user_id", "title_id", name="uq_watchlist_user_title"),
        Index("ix_watchlist_user_added", "user_id", "added_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default=DEFAULT_USER_ID)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id"), index=True)
    added_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())

    # Why they wanted it, in their own words -- "Ravi keeps going on about this".
    # Nothing reads it but a person, which is the point: a reason you wrote down
    # months ago is what makes an old list worth keeping.
    note: Mapped[str | None] = mapped_column(Text)

    # Null while it is still waiting. Set when somebody says they have seen it.
    watched_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    title: Mapped["Title"] = relationship()


class YouTubeView(Base):
    """One YouTube video watched.

    Kept well away from ``watch_events`` on purpose. YouTube is a taste and
    statistics signal and never a recommendation candidate -- nobody needs an app
    to tell them to watch YouTube -- and a table that cannot be resolved against
    the catalogue is a much stronger guarantee of that than a convention someone
    has to remember.

    Named for the view rather than the video because that is what a row is: the
    same video watched three times is three rows, and how often something is
    returned to is most of what this data is good for. What it was called and
    whose channel it was travel with each row rather than living in a table of
    videos, for the reason ``watch_events`` keeps its raw title: the record of a
    viewing should still read correctly after the video has been deleted.
    """

    __tablename__ = "youtube_views"
    __table_args__ = (
        # The same idempotency guarantee watch_events has. Takeout exports the
        # whole history every time, so re-uploading is the normal case.
        UniqueConstraint("user_id", "fingerprint", name="uq_youtube_views_user_fingerprint"),
        Index("ix_youtube_views_user_watched_at", "user_id", "watched_at"),
        # Everything interesting here is "per channel, over time".
        Index("ix_youtube_views_user_channel", "user_id", "channel_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default=DEFAULT_USER_ID)
    import_id: Mapped[int] = mapped_column(ForeignKey("imports.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))

    video_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(Text)
    channel_name: Mapped[str | None] = mapped_column(Text)
    watched_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Offer(Base):
    """Where one title can be watched in one country, cached with a TTL.

    Rent and buy offers are stored alongside the rest even though they never
    satisfy the availability filter: they cost nothing extra to keep, and a
    price is worth showing next to a title someone has decided they want.
    """

    __tablename__ = "offers"
    __table_args__ = (
        UniqueConstraint(
            "title_id",
            "country",
            "provider_short_name",
            "monetization_type",
            "presentation_type",
            name="uq_offers_title_country_provider_offer",
        ),
        Index("ix_offers_title_country", "title_id", "country"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id"))
    country: Mapped[str] = mapped_column(String(8))
    provider_short_name: Mapped[str] = mapped_column(String(16))

    monetization_type: Mapped[str] = mapped_column(String(16))
    # Not nullable, and blank rather than null when unknown: two rows whose
    # presentation is NULL would both satisfy the unique constraint, because
    # SQL does not consider two nulls equal, and the deduplication above would
    # quietly stop working.
    presentation_type: Mapped[str] = mapped_column(String(8), default="")

    url: Mapped[str | None] = mapped_column(Text)
    price_string: Mapped[str | None] = mapped_column(Text)
    price_value: Mapped[float | None] = mapped_column(Float)
    price_currency: Mapped[str | None] = mapped_column(String(8))
    # When this offer stops being available, when JustWatch says so. The
    # "leaving Netflix in nine days" signal.
    available_to: Mapped[datetime | None] = mapped_column(UtcDateTime)

    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())

    title: Mapped[Title] = relationship(back_populates="offers")

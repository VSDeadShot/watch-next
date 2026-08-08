"""Request and response bodies. The frontend's `lib/types.ts` mirrors these."""

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.core.moods import Mood
from app.core.scoring import KindPreference

# Long enough for a reason somebody typed, short enough that the column cannot
# be used as storage. Bounded here rather than in the database because this is
# where an arbitrarily long request body would otherwise be accepted.
NOTE_LIMIT = 500


class ImportSummaryResponse(BaseModel):
    """What an upload did.

    ``imported + duplicates + skipped`` equals ``total_rows``, so the user can
    reconcile the result against the file they uploaded rather than taking it on
    trust.
    """

    import_id: int
    source: str
    filename: str | None = None
    export_format: str

    total_rows: int
    imported: int
    duplicates: int
    skipped: int

    # Skip reason -> count, e.g. {"supplemental_video": 12}. Empty when nothing
    # was dropped.
    skipped_by_reason: dict[str, int] = Field(default_factory=dict)

    # Readings the importer had to guess at, in plain language -- currently only
    # the day/month order of the simple export's dates.
    assumptions: list[str] = Field(default_factory=list)


class ResolveSummaryResponse(BaseModel):
    """What one resolution pass did.

    ``failed`` and ``unresolved`` are separate because they need different
    responses: a failure is retried by the next run on its own, a refusal waits
    for someone to decide.
    """

    searched: int
    resolved: int
    unresolved: int
    failed: int
    linked_events: int


class TitleCandidate(BaseModel):
    """One option the matcher weighed, as a button in the fixer.

    The year is not decoration: two films called Dune are indistinguishable
    without it, and telling them apart is the whole job being handed to a person.
    """

    node_id: str
    title: str
    object_type: str
    release_year: int | None = None


class UnresolvedTitleResponse(BaseModel):
    """A title the matcher declined, and everything needed to decide it."""

    resolution_id: int
    query_title: str
    kind: str
    reason: str
    # How many watch events are waiting on this answer, so the list can be
    # worked through in order of how much each fix is worth.
    event_count: int
    candidates: list[TitleCandidate] = Field(default_factory=list)


class ManualResolutionRequest(BaseModel):
    """Which catalogue entry a person picked."""

    # Not restricted to the stored candidates: the fixer will grow a free search
    # for the case where the right answer was never among them.
    node_id: str = Field(min_length=1, pattern=r"\S")


class ManualResolutionResponse(BaseModel):
    """The title a manual fix settled on, and what it linked."""

    resolution_id: int
    title_id: int
    jw_node_id: str
    title: str
    object_type: str
    release_year: int | None = None
    poster_url: str | None = None
    linked_events: int


class ProviderResponse(BaseModel):
    """One streaming service, as a tile in the settings picker."""

    # What an offer names its provider by, and therefore what a subscription is
    # stored as. Everything else on this model is for a person to look at.
    short_name: str
    name: str
    technical_name: str
    icon_url: str | None = None
    monetization_types: list[str] = Field(default_factory=list)


class ProviderCatalogueResponse(BaseModel):
    """Everything the picker can offer, and where it applies.

    The country is on the response rather than assumed by the client because
    availability means nothing without it, and a picker rendering one country's
    services against another's subscriptions would be quietly wrong.
    """

    country: str
    providers: list[ProviderResponse] = Field(default_factory=list)


class ProviderRefreshResponse(BaseModel):
    """What a catalogue refresh changed.

    ``fetched == 0`` means JustWatch listed nothing and the stored catalogue was
    kept rather than emptied -- not that the catalogue is now empty.
    """

    country: str
    fetched: int
    added: int
    updated: int
    removed: int


class SubscriptionsRequest(BaseModel):
    """The complete set of services the user has. Replaces, never appends.

    An empty list is a valid, meaningful answer -- it is how somebody says they
    have cancelled everything -- so this deliberately has no minimum length.
    """

    short_names: list[str] = Field(default_factory=list)


class SubscriptionsResponse(BaseModel):
    """The services the user has, in the form the availability filter uses."""

    country: str
    short_names: list[str] = Field(default_factory=list)


class WatchOnResponse(BaseModel):
    """Somewhere a title can be watched at no additional cost."""

    short_name: str
    name: str
    monetization: str
    url: str | None = None
    # False for anything free to everyone, so the interface can say "free on
    # JioHotstar" instead of implying a subscription the user does not have.
    requires_subscription: bool = True


class WatchlistAddRequest(BaseModel):
    """Put a title on the list, with an optional reason.

    Omitting the note is not the same as sending an empty one: the "save for
    later" button on a recommendation card knows nothing about notes, and must
    not wipe a reason typed on the watchlist page.
    """

    title_id: int
    note: str | None = Field(default=None, max_length=NOTE_LIMIT)


class WatchlistUpdateRequest(BaseModel):
    """Change an entry. Anything left out is left alone.

    ``note: null`` clears the note, which is why omitting the field and sending
    it as null have to mean different things -- the route tells them apart by
    what the client actually sent rather than by the value.
    """

    note: str | None = Field(default=None, max_length=NOTE_LIMIT)
    watched: bool | None = None


class WatchlistItemResponse(BaseModel):
    """One entry, with enough of its title to draw a row.

    The title travels with the entry rather than being fetched per row by the
    client, for the same reason it is loaded in one query on the way out: a
    watchlist page is a list of posters, and a request per poster is a page that
    gets slower the more somebody uses it.
    """

    title_id: int
    jw_node_id: str
    title: str
    object_type: str
    release_year: int | None = None
    runtime_minutes: int | None = None
    genres: list[str] = Field(default_factory=list)
    poster_url: str | None = None
    imdb_score: float | None = None

    # Where it can be watched right now, best first. Empty means nowhere the
    # user can watch it at no additional cost -- which is the single most
    # useful thing a list of things-to-watch can tell somebody, and the reason
    # this is on the entry rather than fetched per row by the client.
    watch_on: list[WatchOnResponse] = Field(default_factory=list)

    added_at: datetime
    # Null while it is still waiting; set once somebody says they have seen it.
    watched_at: datetime | None = None
    note: str | None = None


class RecommendationRequestBody(BaseModel):
    """What somebody wants tonight. Every field has a defensible default."""

    mood: Mood = Mood.SURPRISE_ME
    # Null means "no limit", which is different from a large number: with no
    # budget, runtime stops influencing the choice at all rather than preferring
    # something enormous. Capped at a day because a value beyond that is a typo
    # rather than a plan.
    minutes_available: int | None = Field(default=None, ge=1, le=1440)
    kind: KindPreference = KindPreference.ANY
    # What "not this one" sends back: the titles already turned down in this
    # sitting. Bounded so a client cannot post an unbounded IN clause.
    exclude_ids: list[int] = Field(default_factory=list, max_length=100)


class RecommendedTitleResponse(BaseModel):
    """The one title, and everything needed to justify and act on it."""

    title_id: int
    jw_node_id: str
    title: str
    object_type: str
    release_year: int | None = None
    runtime_minutes: int | None = None
    genres: list[str] = Field(default_factory=list)
    poster_url: str | None = None
    imdb_score: float | None = None

    score: float
    # Why this one, in plain language, strongest first.
    reasons: list[str] = Field(default_factory=list)
    watch_on: list[WatchOnResponse] = Field(default_factory=list)
    # Already waiting on their list. Said outright so the interface does not
    # have to infer it from a sentence in `reasons`, which is written to be
    # read rather than parsed.
    on_watchlist: bool = False


class ConsideredResponse(BaseModel):
    """How many candidates survived each stage.

    Reading these in order says where the search collapsed, which is the
    difference between "import something", "tick a box on the settings page"
    and "ask again with more time".
    """

    pool: int
    available: int
    eligible: int


class RecommendationResponse(BaseModel):
    """One title, or none and the reason why.

    ``title`` is a single object rather than a list with one element in it, and
    that is the point: the constraint this app is built around is enforced by
    the contract, so no client can decide to render three. There is no field
    here that could hold a second answer.
    """

    title: RecommendedTitleResponse | None = None
    # Populated only when there is no title. Written for somebody to read and
    # act on -- a refusal that does not say what to change is just a dead end.
    reason: str = ""
    considered: ConsideredResponse


class CountResponse(BaseModel):
    """One labelled number in a ranked list."""

    label: str
    count: int


class MonthCountResponse(BaseModel):
    """Activity in one month, dated by its first day.

    Every month between the first and the last is present, including the ones
    with nothing in them, so a client can draw the series without having to
    reconstruct the gaps -- and without being able to omit them by accident.
    """

    month: date
    count: int


class TopTitleResponse(BaseModel):
    """A much-watched title, and how much watching went into it.

    ``object_type`` is carried because the count means different things either
    side of it: twelve sessions of a series is twelve episodes, twelve of a film
    is having watched it twelve times.
    """

    title_id: int
    title: str
    object_type: str
    sessions: int


class HistoryStatsResponse(BaseModel):
    """A watch history, counted.

    ``titles`` and ``sessions`` are both here and neither substitutes for the
    other: somebody who watched sixty episodes of one show made one decision and
    sat down sixty times, and only saying one of those would misrepresent them.
    """

    titles: int = 0
    sessions: int = 0
    movies: int = 0
    series: int = 0

    # Null when nothing in the history recorded how long it ran, which is not
    # the same as nothing having been watched. ``sessions_timed`` says how many
    # sessions the figure rests on, so a client can present it as the lower
    # bound it is rather than as a total.
    minutes_watched: int | None = None
    sessions_timed: int = 0

    first_watched: datetime | None = None
    last_watched: datetime | None = None

    # Genres in English, as everywhere a client reads them. See api/recommend.py.
    top_genres: list[CountResponse] = Field(default_factory=list)
    # Chronological rather than ranked -- it is a shape over time.
    decades: list[CountResponse] = Field(default_factory=list)
    top_titles: list[TopTitleResponse] = Field(default_factory=list)
    by_month: list[MonthCountResponse] = Field(default_factory=list)


class YouTubeStatsResponse(BaseModel):
    """A YouTube history, counted, and reported separately from the rest.

    Separate for the reason the table is: YouTube is a taste and statistics
    signal and never a recommendation candidate.
    """

    views: int = 0
    videos: int = 0
    channels: int = 0
    first_watched: datetime | None = None
    last_watched: datetime | None = None
    top_channels: list[CountResponse] = Field(default_factory=list)
    by_month: list[MonthCountResponse] = Field(default_factory=list)


class StatsResponse(BaseModel):
    """Everything the stats page is drawn from."""

    history: HistoryStatsResponse = Field(default_factory=HistoryStatsResponse)
    youtube: YouTubeStatsResponse = Field(default_factory=YouTubeStatsResponse)
    # Watch events that never reached a catalogue row, and so are in none of the
    # numbers above. Reported rather than omitted: a summary that quietly
    # understates itself is the thing this app exists not to be, and the fix --
    # a resolve pass, or a few choices on the unresolved list -- is one a person
    # can act on once they know.
    unresolved_sessions: int = 0

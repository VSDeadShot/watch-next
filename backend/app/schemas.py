"""Request and response bodies. The frontend's `lib/types.ts` mirrors these."""

from pydantic import BaseModel, Field


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

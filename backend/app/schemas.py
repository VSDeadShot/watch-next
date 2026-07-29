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

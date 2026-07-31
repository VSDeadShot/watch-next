"""Upload endpoints for watch-history exports."""

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.api.deps import SessionDep, SettingsDep
from app.core.netflix_parser import NetflixExportError
from app.schemas import ImportSummaryResponse
from app.services.importer import SOURCE, import_netflix_export

router = APIRouter(prefix="/api/imports", tags=["imports"])


@router.post("/netflix", response_model=ImportSummaryResponse)
async def import_netflix(
    file: Annotated[UploadFile, File()],
    session: SessionDep,
    settings: SettingsDep,
) -> ImportSummaryResponse:
    """Import a Netflix viewing-history export.

    Takes the personal-data zip as downloaded, or either CSV on its own. Safe to
    call repeatedly with the same file: rows already stored are counted as
    duplicates rather than inserted again.
    """
    data = await file.read()

    try:
        summary = import_netflix_export(
            session,
            data,
            filename=file.filename,
            min_watch_seconds=settings.min_watch_seconds,
        )
    except NetflixExportError as error:
        session.rollback()
        # The parser's message names what it found and where the right file
        # lives. A bare 500 would leave the user guessing which of a dozen
        # folders to try next.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    return ImportSummaryResponse(
        import_id=summary.import_id,
        source=SOURCE,
        filename=summary.filename,
        export_format=summary.export_format,
        total_rows=summary.total_rows,
        imported=summary.imported,
        duplicates=summary.duplicates,
        skipped=summary.skipped,
        skipped_by_reason=dict(summary.skipped_by_reason),
        assumptions=list(summary.assumptions),
    )

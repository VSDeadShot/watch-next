"""Upload endpoints for watch-history exports."""

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.api.deps import SessionDep, SettingsDep, UserDep
from app.core.netflix_parser import NetflixExportError, NetflixTooLargeError
from app.core.youtube_parser import YouTubeExportError
from app.schemas import ImportSummaryResponse
from app.services.importer import (
    SOURCE,
    YOUTUBE_SOURCE,
    ImportSummary,
    import_netflix_export,
    import_youtube_export,
)

router = APIRouter(prefix="/api/imports", tags=["imports"])


@router.post("/netflix", response_model=ImportSummaryResponse)
async def import_netflix(
    file: Annotated[UploadFile, File()],
    session: SessionDep,
    settings: SettingsDep,
    user: UserDep,
) -> ImportSummaryResponse:
    """Import a Netflix viewing-history export.

    Takes the personal-data zip as downloaded, or either CSV on its own. Safe to
    call repeatedly with the same file: rows already stored are counted as
    duplicates rather than inserted again.
    """
    # Checked before the read, not before the request. FastAPI has already
    # parsed the body by the time any endpoint or dependency runs, and
    # Starlette's `max_part_size` only applies to non-file parts -- so this
    # bounds what the upload costs in memory, CPU and database work, but not
    # the bytes arriving. Refusing at the door would need middleware reading
    # `Content-Length`, and past the API key there is nobody to refuse.
    if file.size is not None and file.size > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"this upload is {file.size:,} bytes, over the "
                f"{settings.max_upload_bytes:,} this endpoint accepts. The "
                "Netflix personal-data zip is a few megabytes; if you meant to "
                "send something else, /api/imports/youtube takes the Takeout "
                "watch history."
            ),
        )

    data = await file.read()

    try:
        summary = import_netflix_export(
            session,
            data,
            filename=file.filename,
            min_watch_seconds=settings.min_watch_seconds,
            max_history_bytes=settings.max_history_bytes,
            user_id=user,
        )
    # Before the base class it derives from: a compressed archive can be small
    # enough to accept and still unpack to more than the parser will read, and
    # answering that with 400 would tell the user to send a different file when
    # the file was right and only the size was wrong.
    except NetflixTooLargeError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(error)
        ) from error
    except NetflixExportError as error:
        session.rollback()
        # The parser's message names what it found and where the right file
        # lives. A bare 500 would leave the user guessing which of a dozen
        # folders to try next.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    return _response(summary, source=SOURCE)


# Deliberately sync. FastAPI runs a plain `def` endpoint in a worker thread,
# which is what makes it safe to hand the upload's own file object to a reader
# that blocks -- the alternative, awaiting `file.read()`, would pull two hundred
# megabytes into memory and undo the streaming this endpoint exists for.
@router.post("/youtube", response_model=ImportSummaryResponse)
def import_youtube(
    file: Annotated[UploadFile, File()],
    session: SessionDep,
    user: UserDep,
) -> ImportSummaryResponse:
    """Import a Google Takeout YouTube watch history.

    Takes ``watch-history.json`` as exported. YouTube views are a taste and
    statistics signal only and never become recommendations. Safe to call
    repeatedly with the same file.
    """
    try:
        summary = import_youtube_export(session, file.file, filename=file.filename, user_id=user)
    except YouTubeExportError as error:
        # Rolls back whatever the stream managed to write before it failed. A
        # truncated download is only discovered part way through, so without
        # this a broken upload would leave half a history behind and no summary
        # saying so.
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    return _response(summary, source=YOUTUBE_SOURCE)


def _response(summary: ImportSummary, *, source: str) -> ImportSummaryResponse:
    return ImportSummaryResponse(
        import_id=summary.import_id,
        source=source,
        filename=summary.filename,
        export_format=summary.export_format,
        total_rows=summary.total_rows,
        imported=summary.imported,
        duplicates=summary.duplicates,
        skipped=summary.skipped,
        skipped_by_reason=dict(summary.skipped_by_reason),
        assumptions=list(summary.assumptions),
    )

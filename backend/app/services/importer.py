"""Persist a parsed Netflix export, adding only what is genuinely new.

This is the impure half of importing: :mod:`app.core.netflix_parser` turns bytes
into events without touching anything, and this module decides what to store.

The work it does beyond writing rows is deduplication. Netflix's export contains
the whole history every time it is downloaded, and people re-download it, so the
second upload of a file is the normal case rather than the exception. Every row
is reduced to a fingerprint and compared against what is already stored, which
makes uploading the same file twice a no-op and uploading a fresh download add
exactly the rows recorded since the last one. Takeout is cumulative in the same
way, and its import works the same way for the same reason.

The two differ in one respect. A Netflix export is parsed in full before
anything is written, so a file that turns out to be unreadable leaves no trace at
all. A Takeout history is too big to hold, so it is read as it is written and a
truncated download is not discovered until rows are already pending -- which is
why that import is one transaction the caller can abandon rather than a promise
made before it starts.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import IO

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.core.fingerprint import VideoFingerprints, fingerprint_events
from app.core.netflix_parser import ParseResult, SkipReason, parse_netflix_export
from app.core.title_parser import parse_netflix_title
from app.core.youtube_parser import RawVideoEvent, open_youtube_export
from app.models import DEFAULT_USER_ID, ImportRun, WatchEvent, YouTubeView

SOURCE = "netflix"
YOUTUBE_SOURCE = "youtube"

# Takeout offers no choice of shape the way Netflix does, but the column is not
# nullable and "whatever this was" is worth recording either way.
YOUTUBE_FORMAT = "takeout"

# SQLite caps how many values a single statement may bind, and a real export runs
# to thousands of rows, so the "have I seen these already" lookup is batched.
_FINGERPRINT_QUERY_CHUNK = 500

# How many streamed views are held before being written. Small enough that a
# 200 MB history costs nothing to import, big enough that the duplicate check is
# one query per few hundred rows rather than one per row.
_IMPORT_BATCH = 500


@dataclass(frozen=True)
class ImportSummary:
    """What one upload did, in terms the user can check against their file.

    ``imported + duplicates + skipped == total_rows`` always holds. A summary
    whose numbers do not reconcile is worse than no summary: it tells the user
    something went missing without telling them what.
    """

    import_id: int
    export_format: str
    filename: str | None
    total_rows: int
    imported: int
    duplicates: int
    skipped: int
    skipped_by_reason: dict[str, int] = field(default_factory=dict)
    assumptions: tuple[str, ...] = ()


def import_netflix_export(
    session: Session,
    data: bytes,
    *,
    filename: str | None = None,
    min_watch_seconds: int = 60,
    max_history_bytes: int = 16 * 1024 * 1024,
    user_id: str = DEFAULT_USER_ID,
) -> ImportSummary:
    """Read an uploaded export and store the rows not already held.

    Raises:
        NetflixExportError: if the upload cannot be read. Parsing happens before
            anything is written, so a rejected upload leaves no trace.
    """
    parsed = parse_netflix_export(
        data, min_watch_seconds=min_watch_seconds, max_history_bytes=max_history_bytes
    )

    fingerprints = fingerprint_events(parsed.events, source=SOURCE)
    already_stored = _existing_fingerprints(session, user_id, fingerprints)

    run = ImportRun(
        user_id=user_id,
        source=SOURCE,
        filename=filename,
        export_format=parsed.export_format.value,
    )
    session.add(run)
    # Flush rather than commit: the events below need the id, but a failure part
    # way through should still take the audit row down with it.
    session.flush()

    skipped: Counter[SkipReason] = Counter(parsed.skipped)
    duplicates = 0
    imported = 0

    for event, fingerprint in zip(parsed.events, fingerprints, strict=True):
        if fingerprint in already_stored:
            duplicates += 1
            continue

        try:
            title = parse_netflix_title(event.raw_title)
        except ValueError:
            # The cell held something -- punctuation, stray separators -- but no
            # title to search for. Counted, never silently dropped.
            skipped[SkipReason.MISSING_TITLE] += 1
            continue

        session.add(
            WatchEvent(
                user_id=user_id,
                import_id=run.id,
                fingerprint=fingerprint,
                source=SOURCE,
                raw_title=event.raw_title,
                watched_at=event.watched_at,
                duration_seconds=event.duration_seconds,
                profile_name=event.profile_name,
                device_type=event.device_type,
                country=event.country,
                kind=title.kind,
                title=title.title,
                season_number=title.season_number,
                episode_title=title.episode_title,
                episode_number=title.episode_number,
                title_ambiguous=title.ambiguous,
            )
        )
        imported += 1

    _record_totals(run, parsed, imported=imported, duplicates=duplicates, skipped=skipped)
    session.commit()

    return ImportSummary(
        import_id=run.id,
        export_format=parsed.export_format.value,
        filename=filename,
        total_rows=parsed.total_rows,
        imported=imported,
        duplicates=duplicates,
        skipped=sum(skipped.values()),
        skipped_by_reason=dict(skipped),
        assumptions=parsed.assumptions,
    )


def import_youtube_export(
    session: Session,
    stream: IO[bytes],
    *,
    filename: str | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> ImportSummary:
    """Read an uploaded Takeout watch history and store the views not already held.

    The file is never held whole -- not as bytes, not as parsed entries, not as
    ORM objects. Views are read a batch at a time and written with a bulk insert
    that does not put them in the session's identity map, so importing two
    hundred megabytes costs the same memory as importing two.

    It is still one transaction. A download that ends halfway through surfaces as
    a parse error mid-stream, and the caller rolling back is what stops half a
    history being left behind with a summary that never arrived to describe it.

    Raises:
        YouTubeExportError: if the upload is not a readable Takeout history.
            Whether this arrives before or during the read is the difference
            between the wrong file and a broken one; both leave nothing stored,
            provided the caller rolls back.
    """
    export = open_youtube_export(stream)

    run = ImportRun(
        user_id=user_id,
        source=YOUTUBE_SOURCE,
        filename=filename,
        export_format=YOUTUBE_FORMAT,
    )
    session.add(run)
    session.flush()

    fingerprints = VideoFingerprints(YOUTUBE_SOURCE)
    batch: list[tuple[RawVideoEvent, str]] = []
    imported = 0
    duplicates = 0

    for event in export.events():
        batch.append((event, fingerprints.of(video_id=event.video_id, watched_at=event.watched_at)))
        if len(batch) >= _IMPORT_BATCH:
            added, held = _store_views(session, run, batch, user_id=user_id)
            imported += added
            duplicates += held
            batch.clear()

    added, held = _store_views(session, run, batch, user_id=user_id)
    imported += added
    duplicates += held

    run.total_rows = export.total_entries
    run.imported_rows = imported
    run.duplicate_rows = duplicates
    run.skipped_rows = export.skipped_total
    run.skipped_detail = {reason.value: count for reason, count in export.skipped.items()}
    run.assumptions = list(export.assumptions)
    session.commit()

    return ImportSummary(
        import_id=run.id,
        export_format=YOUTUBE_FORMAT,
        filename=filename,
        total_rows=export.total_entries,
        imported=imported,
        duplicates=duplicates,
        skipped=export.skipped_total,
        skipped_by_reason=dict(run.skipped_detail),
        assumptions=export.assumptions,
    )


def _store_views(
    session: Session,
    run: ImportRun,
    batch: Sequence[tuple[RawVideoEvent, str]],
    *,
    user_id: str,
) -> tuple[int, int]:
    """Write the views in this batch that are not already held.

    Returns ``(written, already held)``. The lookup runs inside the open
    transaction, so it sees the batches written before it and a view repeated
    across two batches is recognised rather than colliding on the way in.
    """
    if not batch:
        return 0, 0

    already = _existing_youtube_fingerprints(
        session, user_id, tuple(fingerprint for _, fingerprint in batch)
    )

    rows = []
    for event, fingerprint in batch:
        if fingerprint in already:
            continue
        # Guards against a repeat inside this one batch, which the query above
        # cannot see because neither row is written yet.
        already.add(fingerprint)
        rows.append(
            {
                "user_id": user_id,
                "import_id": run.id,
                "fingerprint": fingerprint,
                "video_id": event.video_id,
                "title": event.title,
                "channel_name": event.channel_name,
                "watched_at": event.watched_at,
            }
        )

    if rows:
        # A bulk insert rather than session.add: these rows are never read back,
        # and keeping half a million of them in the identity map for the rest of
        # the transaction would undo the streaming.
        session.execute(insert(YouTubeView), rows)

    return len(rows), len(batch) - len(rows)


def _existing_youtube_fingerprints(
    session: Session, user_id: str, fingerprints: tuple[str, ...]
) -> set[str]:
    """Which of these views are already stored, including earlier batches."""
    unique = list(dict.fromkeys(fingerprints))
    found: set[str] = set()

    for start in range(0, len(unique), _FINGERPRINT_QUERY_CHUNK):
        chunk = unique[start : start + _FINGERPRINT_QUERY_CHUNK]
        found.update(
            session.scalars(
                select(YouTubeView.fingerprint).where(
                    YouTubeView.user_id == user_id,
                    YouTubeView.fingerprint.in_(chunk),
                )
            )
        )

    return found


def _existing_fingerprints(
    session: Session, user_id: str, fingerprints: tuple[str, ...]
) -> set[str]:
    """Return which of these rows are already stored, in batched queries."""
    unique = list(dict.fromkeys(fingerprints))
    found: set[str] = set()

    for start in range(0, len(unique), _FINGERPRINT_QUERY_CHUNK):
        batch = unique[start : start + _FINGERPRINT_QUERY_CHUNK]
        found.update(
            session.scalars(
                select(WatchEvent.fingerprint).where(
                    WatchEvent.user_id == user_id,
                    WatchEvent.fingerprint.in_(batch),
                )
            )
        )

    return found


def _record_totals(
    run: ImportRun,
    parsed: ParseResult,
    *,
    imported: int,
    duplicates: int,
    skipped: Counter[SkipReason],
) -> None:
    run.total_rows = parsed.total_rows
    run.imported_rows = imported
    run.duplicate_rows = duplicates
    run.skipped_rows = sum(skipped.values())
    # Plain strings, so the stored JSON reads the same as it did in memory.
    run.skipped_detail = {reason.value: count for reason, count in skipped.items()}
    run.assumptions = list(parsed.assumptions)

"""Persist a parsed Netflix export, adding only what is genuinely new.

This is the impure half of importing: :mod:`app.core.netflix_parser` turns bytes
into events without touching anything, and this module decides what to store.

The work it does beyond writing rows is deduplication. Netflix's export contains
the whole history every time it is downloaded, and people re-download it, so the
second upload of a file is the normal case rather than the exception. Every row
is reduced to a fingerprint and compared against what is already stored, which
makes uploading the same file twice a no-op and uploading a fresh download add
exactly the rows recorded since the last one.
"""

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.fingerprint import fingerprint_events
from app.core.netflix_parser import ParseResult, SkipReason, parse_netflix_export
from app.core.title_parser import parse_netflix_title
from app.models import DEFAULT_USER_ID, ImportRun, WatchEvent

SOURCE = "netflix"

# SQLite caps how many values a single statement may bind, and a real export runs
# to thousands of rows, so the "have I seen these already" lookup is batched.
_FINGERPRINT_QUERY_CHUNK = 500


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
    user_id: str = DEFAULT_USER_ID,
) -> ImportSummary:
    """Read an uploaded export and store the rows not already held.

    Raises:
        NetflixExportError: if the upload cannot be read. Parsing happens before
            anything is written, so a rejected upload leaves no trace.
    """
    parsed = parse_netflix_export(data, min_watch_seconds=min_watch_seconds)

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

"""Read Netflix viewing-history exports into normalized watch events.

Netflix ships history in two different shapes:

* The **full** personal-data download (Account -> Security & Privacy -> Download
  your personal information) is a zip of roughly a dozen folders, with the useful
  file at ``CONTENT_INTERACTION/ViewingActivity.csv``: ten columns including UTC
  timestamps, durations, and a flag marking trailers and recaps.
* The **simple** export from the viewing-activity page is a bare
  ``NetflixViewingHistory.csv`` with only ``Title,Date``.

Both are accepted, and so is the zip itself -- requiring someone to find the
right file inside a dozen folders is exactly the friction this project exists to
remove.

Two decisions here are worth knowing about:

*Nothing is dropped silently.* Trailers, recaps, sub-minute views and malformed
rows are all excluded, but each exclusion is counted by reason and reported. A
user who knows they watched 400 things and is shown 250 needs the other 150
explained, or they stop trusting the importer.

*Ambiguous dates are resolved from evidence, not assumed.* The simple export's
``01/02/2024`` is 1 February in most of the world and 2 January in the US.
The whole file is scanned for a value that settles it; when nothing does, the
assumption is recorded and handed back to the caller.

This module is pure: no network, no database.
"""

import csv
import io
import zipfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

# Zip local-file-header magic. Sniffing the bytes is more reliable than trusting
# an uploaded filename or content type.
_ZIP_MAGIC = b"PK\x03\x04"

# Basenames to look for inside an archive, most specific first.
_HISTORY_FILENAMES = ("ViewingActivity.csv", "NetflixViewingHistory.csv")

_FULL_TIMESTAMP = "%Y-%m-%d %H:%M:%S"
_ISO_DATE = "%Y-%m-%d"

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600

# Seconds, minutes, hours -- applied right-to-left across a duration's components.
_DURATION_MULTIPLIERS = (1, _SECONDS_PER_MINUTE, _SECONDS_PER_HOUR)

# Above this, a slash-separated date component cannot be a month.
_MAX_MONTH = 12


class NetflixExportError(ValueError):
    """The upload is not a Netflix viewing-history export we can read."""


class ExportFormat(StrEnum):
    FULL = "full"
    SIMPLE = "simple"


class SkipReason(StrEnum):
    """Why a row was excluded. Reported so no filtering is invisible."""

    SUPPLEMENTAL_VIDEO = "supplemental_video"
    TOO_SHORT = "too_short"
    MISSING_TITLE = "missing_title"
    BAD_TIMESTAMP = "bad_timestamp"


@dataclass(frozen=True)
class RawWatchEvent:
    """One viewing session, before its title has been parsed or resolved."""

    raw_title: str
    watched_at: datetime
    duration_seconds: int | None = None
    profile_name: str | None = None
    device_type: str | None = None
    country: str | None = None


@dataclass(frozen=True)
class ParseResult:
    export_format: ExportFormat
    events: tuple[RawWatchEvent, ...] = ()
    total_rows: int = 0
    skipped: Mapping[SkipReason, int] = field(default_factory=dict)
    assumptions: tuple[str, ...] = ()

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())


def parse_netflix_export(
    data: bytes,
    *,
    min_watch_seconds: int = 60,
    day_first: bool | None = None,
) -> ParseResult:
    """Parse an uploaded Netflix export, zip or CSV.

    Args:
        data: Raw upload bytes. A zip is detected by its magic bytes and searched
            for a history CSV; anything else is treated as CSV.
        min_watch_seconds: Views shorter than this are counted as accidental
            starts rather than views. Only applies to the full export, which is
            the only one carrying durations.
        day_first: Force the reading of ambiguous ``dd/mm`` vs ``mm/dd`` dates in
            the simple export. ``None`` infers it from the file.

    Raises:
        NetflixExportError: if the upload is unreadable or is not a history
            export. The message names what was found, so the user can tell which
            file they picked by mistake.
    """
    text = _decode(_locate_csv(data))
    reader = csv.DictReader(io.StringIO(text))
    export_format = _detect_format(reader.fieldnames)
    rows = list(reader)

    if export_format is ExportFormat.FULL:
        return _parse_full(rows, min_watch_seconds=min_watch_seconds)
    return _parse_simple(rows, day_first=day_first)


def _locate_csv(data: bytes) -> bytes:
    """Return the history CSV, reaching inside a zip when given one."""
    if not data.startswith(_ZIP_MAGIC):
        return data

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            for wanted in _HISTORY_FILENAMES:
                for name in names:
                    if name.rsplit("/", 1)[-1].casefold() == wanted.casefold():
                        return archive.read(name)
    except zipfile.BadZipFile as error:
        raise NetflixExportError(f"the archive could not be read: {error}") from error

    raise NetflixExportError(
        "no viewing history found in the archive. Expected a file named "
        f"{' or '.join(_HISTORY_FILENAMES)} -- in the full Netflix download it "
        "sits at CONTENT_INTERACTION/ViewingActivity.csv."
    )


def _decode(data: bytes) -> str:
    """Decode as UTF-8, tolerating the byte-order mark Netflix often prepends."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise NetflixExportError(f"the file is not valid UTF-8 text: {error}") from error


def _detect_format(fieldnames: list[str] | None) -> ExportFormat:
    if not fieldnames:
        raise NetflixExportError("the file is empty or has no header row.")

    columns = {name.strip() for name in fieldnames if name}

    if {"Title", "Start Time"} <= columns:
        return ExportFormat.FULL
    if {"Title", "Date"} <= columns:
        return ExportFormat.SIMPLE

    raise NetflixExportError(
        f"unrecognised columns {sorted(columns)}. A Netflix history export has "
        "either 'Title' and 'Start Time' (the full download, at "
        "CONTENT_INTERACTION/ViewingActivity.csv) or 'Title' and 'Date' "
        "(NetflixViewingHistory.csv)."
    )


def _parse_full(rows: list[dict[str, str]], *, min_watch_seconds: int) -> ParseResult:
    events: list[RawWatchEvent] = []
    skipped: Counter[SkipReason] = Counter()

    for row in rows:
        title = _cell(row, "Title")
        if not title:
            skipped[SkipReason.MISSING_TITLE] += 1
            continue

        # Non-empty means TRAILER, TEASER_TRAILER, HOOK, RECAP or similar -- a
        # promo, not something the user chose to watch.
        if _cell(row, "Supplemental Video Type"):
            skipped[SkipReason.SUPPLEMENTAL_VIDEO] += 1
            continue

        watched_at = _parse_timestamp(_cell(row, "Start Time"))
        if watched_at is None:
            skipped[SkipReason.BAD_TIMESTAMP] += 1
            continue

        duration_seconds = _parse_duration(_cell(row, "Duration"))
        if duration_seconds is not None and duration_seconds < min_watch_seconds:
            skipped[SkipReason.TOO_SHORT] += 1
            continue

        events.append(
            RawWatchEvent(
                raw_title=title,
                watched_at=watched_at,
                duration_seconds=duration_seconds,
                profile_name=_cell(row, "Profile Name") or None,
                device_type=_cell(row, "Device Type") or None,
                country=_cell(row, "Country") or None,
            )
        )

    return ParseResult(
        export_format=ExportFormat.FULL,
        events=tuple(events),
        total_rows=len(rows),
        skipped=dict(skipped),
    )


def _parse_simple(rows: list[dict[str, str]], *, day_first: bool | None) -> ParseResult:
    resolved_day_first, assumptions = _resolve_date_order(
        [_cell(row, "Date") for row in rows], day_first
    )

    events: list[RawWatchEvent] = []
    skipped: Counter[SkipReason] = Counter()

    for row in rows:
        title = _cell(row, "Title")
        if not title:
            skipped[SkipReason.MISSING_TITLE] += 1
            continue

        watched_at = _parse_date(_cell(row, "Date"), day_first=resolved_day_first)
        if watched_at is None:
            skipped[SkipReason.BAD_TIMESTAMP] += 1
            continue

        # This export carries no duration, so there is nothing to threshold on
        # and no row can be dropped as too short.
        events.append(RawWatchEvent(raw_title=title, watched_at=watched_at))

    return ParseResult(
        export_format=ExportFormat.SIMPLE,
        events=tuple(events),
        total_rows=len(rows),
        skipped=dict(skipped),
        assumptions=assumptions,
    )


def _resolve_date_order(values: list[str], override: bool | None) -> tuple[bool, tuple[str, ...]]:
    """Decide whether slash-separated dates are day-first, using the whole file.

    A single ``01/02/2024`` cannot be read on its own, but one ``25/12/2023``
    anywhere in the file settles it for every other row.
    """
    if override is not None:
        return override, ()

    day_first_evidence = False
    month_first_evidence = False

    for value in values:
        parts = value.split("/")
        if len(parts) != 3:
            continue
        try:
            first, second = int(parts[0]), int(parts[1])
        except ValueError:
            continue

        if first > _MAX_MONTH:
            day_first_evidence = True
        if second > _MAX_MONTH:
            month_first_evidence = True

    if day_first_evidence and not month_first_evidence:
        return True, ()
    if month_first_evidence and not day_first_evidence:
        return False, ()
    if day_first_evidence and month_first_evidence:
        return True, (
            "Dates disagree about their order -- some look day/month, others "
            "month/day. Read them as day/month/year.",
        )

    # No slash-separated dates at all (they were ISO), so nothing was assumed.
    if not any(len(value.split("/")) == 3 for value in values):
        return True, ()

    return True, (
        "Dates such as 01/02/2024 are ambiguous and nothing in the file settles "
        "them. Read them as day/month/year.",
    )


def _parse_timestamp(value: str) -> datetime | None:
    """Parse the full export's UTC ``Start Time``.

    The column is documented as UTC, so the result is made tz-aware: a naive
    datetime would silently shift the moment it met anything timezone-aware.
    """
    try:
        return datetime.strptime(value, _FULL_TIMESTAMP).replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_date(value: str, *, day_first: bool) -> datetime | None:
    """Parse the simple export's date-only value to UTC midnight."""
    try:
        return datetime.strptime(value, _ISO_DATE).replace(tzinfo=UTC)
    except ValueError:
        pass

    parts = value.split("/")
    if len(parts) != 3:
        return None

    try:
        first, second, year = (int(part) for part in parts)
    except ValueError:
        return None

    day, month = (first, second) if day_first else (second, first)
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


def _parse_duration(value: str) -> int | None:
    """Parse a colon-separated duration into seconds.

    Deliberately not strptime: a session left running overnight yields an hour
    count above 23, which ``%H`` rejects outright.

    Components are read right-to-left, so ``H:MM:SS``, ``MM:SS`` and bare seconds
    all yield a number. That matters more than it looks: returning None disables
    the too-short filter, so a shorter-than-expected format would let a
    two-second view through as a genuine one.
    """
    parts = value.split(":")
    if not value or len(parts) > len(_DURATION_MULTIPLIERS):
        return None

    try:
        components = [int(part) for part in parts]
    except ValueError:
        return None

    # Pair the rightmost component with seconds, the next with minutes, and so on.
    return sum(
        component * multiplier
        for component, multiplier in zip(reversed(components), _DURATION_MULTIPLIERS, strict=False)
    )


def _cell(row: dict[str, str], column: str) -> str:
    """Read a column, tolerating absent keys and stray whitespace."""
    return (row.get(column) or "").strip()

"""Identify a viewing session by its content, so re-imports add nothing twice.

Netflix's export is cumulative: every download contains the entire history, not
just what is new. Users re-download it, so an importer that trusts the file to be
new duplicates the whole library on the second upload. Instead each row is
reduced to a digest of the fields that identify the session, and rows whose
digest is already stored are skipped.

Two details carry more weight than they look:

*Fields are length-prefixed before hashing.* Joining them with a separator lets
content impersonate that separator -- a profile named ``Sam|Inception`` watching
``Arrival`` would hash identically to ``Sam`` watching ``Inception|Arrival``, and
one row would silently swallow the other. Prefixing each field with its length
makes the encoding unambiguous whatever the values contain.

*Repeated rows are numbered.* The simple export records only a date, so watching
the same episode twice in one day produces two identical rows that are both
genuine. Numbering repeats within the file keeps them apart while staying stable
across re-imports: the same file always yields the same numbers, and a new copy
of a repeated row only ever extends the run.

This module is pure: no I/O, no network, no database.
"""

import hashlib
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime

from app.core.netflix_parser import RawWatchEvent


def watch_event_fingerprint(
    *,
    source: str,
    raw_title: str,
    watched_at: datetime,
    profile_name: str | None = None,
    occurrence: int = 0,
) -> str:
    """Return the stable identity of one viewing session as a hex digest.

    Args:
        source: Which service the row came from, e.g. ``"netflix"``. Included so
            two services can never collide on a shared title.
        raw_title: The title exactly as exported, before parsing. Parsing may
            improve between releases; the raw string will not.
        watched_at: When the session started. Compared as an instant, so the same
            moment expressed in another timezone is the same row.
        profile_name: The Netflix profile, where the export names one. Absent and
            blank are treated alike -- both mean "no profile".
        occurrence: Which copy of an otherwise identical row this is. See
            :func:`fingerprint_events`, which assigns it.

    Raises:
        ValueError: if ``watched_at`` has no timezone. Python would read it as
            the local time of whatever machine ran the import, so the digest --
            and with it the whole idempotency guarantee -- would depend on the
            server's clock settings rather than on the file.
    """
    if watched_at.tzinfo is None or watched_at.tzinfo.utcoffset(watched_at) is None:
        raise ValueError(f"watched_at needs a timezone, got naive {watched_at!r}")

    parts = (
        source,
        profile_name or "",
        raw_title,
        watched_at.astimezone(UTC).isoformat(),
        str(occurrence),
    )
    return hashlib.sha256(_encode(parts)).hexdigest()


def fingerprint_events(events: Sequence[RawWatchEvent], *, source: str) -> tuple[str, ...]:
    """Fingerprint a parsed file, numbering rows that repeat.

    Returned in the order given, so callers can pair each digest with its event.
    """
    seen: Counter[tuple[str, str, datetime]] = Counter()
    fingerprints: list[str] = []

    for event in events:
        key = (event.profile_name or "", event.raw_title, event.watched_at)
        fingerprints.append(
            watch_event_fingerprint(
                source=source,
                raw_title=event.raw_title,
                watched_at=event.watched_at,
                profile_name=event.profile_name,
                occurrence=seen[key],
            )
        )
        seen[key] += 1

    return tuple(fingerprints)


def video_event_fingerprint(
    *,
    source: str,
    video_id: str,
    watched_at: datetime,
    occurrence: int = 0,
) -> str:
    """Return the stable identity of one video view as a hex digest.

    The video's id rather than its title, because that is the part of a YouTube
    entry that cannot change. Titles are edited by the people who uploaded them,
    and a re-import after an edit would otherwise add every affected view a
    second time.

    Raises:
        ValueError: if ``watched_at`` is naive, for the reason given in
            :func:`watch_event_fingerprint`.
    """
    if watched_at.tzinfo is None or watched_at.tzinfo.utcoffset(watched_at) is None:
        raise ValueError(f"watched_at needs a timezone, got naive {watched_at!r}")

    parts = (source, video_id, watched_at.astimezone(UTC).isoformat(), str(occurrence))
    return hashlib.sha256(_encode(parts)).hexdigest()


class VideoFingerprints:
    """Fingerprints a history as it streams past, numbering rows that repeat.

    :func:`fingerprint_events` can number repeats because it is handed the whole
    file at once. A history too big to hold is the entire reason the YouTube
    importer streams, so this remembers one row instead of all of them: Takeout
    writes its entries in time order, which puts any two identical enough to need
    telling apart next to each other.

    An export shuffled out of that order would hand both such rows the same
    digest, and the second would be counted as already held. That is a lost view
    rather than a wrong one, and it costs a duplicate of something watched twice
    in the same millisecond -- the right way round to be wrong, given the
    alternative is holding every fingerprint in the file in memory.
    """

    def __init__(self, source: str) -> None:
        self._source = source
        self._previous: tuple[str, datetime] | None = None
        self._run = 0

    def of(self, *, video_id: str, watched_at: datetime) -> str:
        key = (video_id, watched_at)
        self._run = self._run + 1 if key == self._previous else 0
        self._previous = key
        return video_event_fingerprint(
            source=self._source,
            video_id=video_id,
            watched_at=watched_at,
            occurrence=self._run,
        )


def _encode(parts: Sequence[str]) -> bytes:
    """Join fields so that no value can be mistaken for a field boundary."""
    encoded = [part.encode("utf-8") for part in parts]
    return b"".join(b"%d:%s" % (len(part), part) for part in encoded)

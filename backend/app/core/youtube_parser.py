"""Read Google Takeout's YouTube watch history one entry at a time.

``watch-history.json`` is a single JSON array of activity entries and is
routinely 50-200 MB, because it holds every video anybody has opened for as long
as they have had the account. ``json.load`` on that is a memory failure waiting
for a big enough user, so this module streams: :func:`open_youtube_export` reads
the first half-kilobyte to check the upload is what it claims to be, and events
come out of :meth:`YouTubeExport.events` as the file is consumed.

The counts on the reader are therefore only complete once that iterator has been
drained. Everything else here reports as the Netflix parser does -- every
excluded entry counted by reason, nothing dropped silently -- but the running
totals cannot be handed over before the work is done.

Two things shape the rest of the module:

*Takeout is localized.* Titles, the ``activityControls`` labels and the sentence
Google writes where a deleted video's name used to be are all translated. Any
rule that decides an entry's fate by matching English text works perfectly on an
English export and throws away a Spanish one entirely. So removed videos are
found by their missing link and searches by their URL not being a video -- facts
that read the same in every language. The one place no structural signal exists
is the ``Watched`` prefix on every title, and that is handled by admitting it:
the prefix is left on and the export is reported as not being in English.

*YouTube is a taste and statistics signal only.* Videos are never recommendation
candidates -- nobody needs an app to tell them to watch YouTube -- so what comes
out here is deliberately thinner than a watch event: what, whose, and when.

This module is pure: no network, no database.
"""

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import IO
from urllib.parse import parse_qs, urlparse

import ijson

# Enough of the file to tell a JSON array from an HTML page, and nothing like
# enough to be a memory concern. Read again when a chunk is all whitespace, but
# stop eventually: a file that is nothing but blanks is not an export however
# long it goes on.
_SNIFF_BYTES = 512
_MAX_SNIFF_BYTES = 64 * 1024

_BOM = b"\xef\xbb\xbf"

# Zip local-file-header magic, same as the Netflix parser sniffs for.
_ZIP_MAGIC = b"PK\x03\x04"

# Google translates this, so a title that lacks it is not necessarily wrong.
_WATCHED_PREFIX = "Watched "

# Also translated. An advert whose label is in another language reaches the
# candidate rules as an ordinary view, which is a wrong row rather than a lost
# one -- the acceptable direction to fail in, given the alternative is dropping
# real views by guessing at a translation.
_ADVERT_DETAIL = "From Google Ads"

# Paths that carry the id in the path rather than the query string.
_ID_IN_PATH = ("/shorts/", "/live/", "/embed/", "/v/")

# Base64url, which is what a video id is made of. Eleven characters today; the
# length is left loose because assuming otherwise is how a parser starts
# dropping valid rows the day that changes.
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class YouTubeExportError(ValueError):
    """The upload is not a Takeout YouTube watch history we can read."""


class SkipReason(StrEnum):
    """Why an entry was excluded. Reported so no filtering is invisible."""

    UNAVAILABLE_VIDEO = "unavailable_video"
    NOT_A_VIDEO = "not_a_video"
    ADVERT = "advert"
    BAD_TIMESTAMP = "bad_timestamp"
    MISSING_TITLE = "missing_title"
    MALFORMED_ENTRY = "malformed_entry"


@dataclass(frozen=True)
class RawVideoEvent:
    """One video watched, before anything has been stored or aggregated."""

    video_id: str
    title: str
    watched_at: datetime
    channel_name: str | None = None


def open_youtube_export(stream: IO[bytes]) -> "YouTubeExport":
    """Check an upload looks like a Takeout history and return a reader over it.

    The check is eager and the reading is not: the two ways this goes wrong that
    a person can actually fix -- exporting HTML instead of JSON, or picking a
    file that is not the history at all -- are worth failing on immediately
    rather than a hundred megabytes later.

    Args:
        stream: The upload, open in binary mode. Only ``read`` is used, so an
            unseekable stream is fine; nothing rewinds it.

    Raises:
        YouTubeExportError: if the upload is empty, is HTML, or is JSON that is
            not the array Takeout writes.
    """
    head = stream.read(_SNIFF_BYTES).removeprefix(_BOM)
    start = head.lstrip()

    # Nothing but whitespace so far says nothing about the file, so keep
    # looking. Reporting a document that opens with a long preamble as empty
    # would send somebody hunting for a problem with their export.
    while not start and len(head) < _MAX_SNIFF_BYTES:
        more = stream.read(_SNIFF_BYTES)
        if not more:
            break
        head += more
        start = head.lstrip()

    if not head:
        raise YouTubeExportError("the file is empty.")

    if start.startswith(_ZIP_MAGIC):
        # The Netflix importer happily takes its zip, and a person who has just
        # done that will try the same here. Explaining the difference is worth
        # more than a generic refusal: a Takeout archive can run to gigabytes
        # and Google splits large ones across several files, so uploading the
        # whole thing to reach one JSON file inside it is the wrong shape of
        # request however convenient it looks.
        raise YouTubeExportError(
            "this is the Takeout archive rather than the history itself. Unzip "
            "it and upload 'Takeout/YouTube and YouTube Music/history/"
            "watch-history.json' -- the archive can be many gigabytes and Google "
            "splits big ones in two, so the single file is the one to send."
        )

    if start.startswith(b"<"):
        raise YouTubeExportError(
            "this looks like the HTML export. In Google Takeout, open the "
            "YouTube entry's 'Multiple formats' button and switch history from "
            "HTML to JSON, then export again and upload watch-history.json."
        )

    if not start.startswith(b"["):
        raise YouTubeExportError(
            "this is not a Takeout watch history. The file should be a JSON "
            "array of activity entries -- in the export it sits at "
            "Takeout/YouTube and YouTube Music/history/watch-history.json."
        )

    return YouTubeExport(_Rejoined(head, stream))


class YouTubeExport:
    """A watch history being read, and the tally of what it contained.

    Construct via :func:`open_youtube_export`. Iterate :meth:`events` to consume
    the file; the counters below are running totals and are final only once that
    iterator is exhausted.
    """

    def __init__(self, stream: IO[bytes]) -> None:
        self._stream = stream
        self.total_entries = 0
        self.kept = 0
        self.skipped: Counter[SkipReason] = Counter()
        self._consumed = False
        self._english_titles = 0
        self._other_titles = 0

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())

    @property
    def assumptions(self) -> tuple[str, ...]:
        """What the reader had to decide for itself, in the user's words.

        Only ever one thing, and only when the evidence is there: a majority of
        titles missing the English prefix means the export is in another
        language, and the word standing where "Watched" would be has been left
        on the front of every title.
        """
        if self._other_titles > self._english_titles:
            return (
                'Titles in this export begin with a translation of "Watched" '
                "rather than the word itself, so the prefix has been left on.",
            )
        return ()

    def events(self) -> Iterator[RawVideoEvent]:
        """Yield each genuine view in file order, counting the rest.

        Raises:
            RuntimeError: if called twice. The stream is consumed as it is read,
                so a second pass would find only the end of the file and report
                it as a corrupt export -- an error blaming the user's download
                for the caller's mistake.
            YouTubeExportError: if the JSON is malformed or ends early. This
                surfaces mid-iteration, since that is when a truncated download
                becomes apparent, so a caller writing rows as it goes needs a
                transaction it can abandon.
        """
        if self._consumed:
            raise RuntimeError(
                "this export has already been read; open the file again to read it twice."
            )
        self._consumed = True
        return self._events()

    def _events(self) -> Iterator[RawVideoEvent]:
        try:
            for entry in ijson.items(self._stream, "item"):
                self.total_entries += 1
                event = self._read(entry)
                if event is not None:
                    self.kept += 1
                    yield event
        except ijson.JSONError as error:
            raise YouTubeExportError(
                f"the file is not valid JSON and may be a partial download: {_first_line(error)}."
            ) from error

    def _read(self, entry: object) -> RawVideoEvent | None:
        """Turn one activity entry into an event, or count why it is not one."""
        if not isinstance(entry, dict):
            # Takeout has no reason to write one, but a single stray value must
            # not end an import that has already read a hundred thousand rows.
            self.skipped[SkipReason.MALFORMED_ENTRY] += 1
            return None

        if self._is_advert(entry):
            self.skipped[SkipReason.ADVERT] += 1
            return None

        url = entry.get("titleUrl")
        if not isinstance(url, str) or not url.strip():
            # A removed or private video keeps its row and loses its link. This
            # is what Google's translated "video has been removed" sentence
            # means, and unlike the sentence it reads the same in every locale.
            self.skipped[SkipReason.UNAVAILABLE_VIDEO] += 1
            return None

        video_id = youtube_video_id(url.strip())
        if video_id is None:
            # A search, a channel page, a post -- or, if the upload was the
            # whole MyActivity file rather than the watch history, another
            # product entirely.
            self.skipped[SkipReason.NOT_A_VIDEO] += 1
            return None

        watched_at = _parse_time(entry.get("time"))
        if watched_at is None:
            self.skipped[SkipReason.BAD_TIMESTAMP] += 1
            return None

        title = self._title(entry.get("title"))
        if title is None:
            self.skipped[SkipReason.MISSING_TITLE] += 1
            return None

        return RawVideoEvent(
            video_id=video_id,
            title=title,
            watched_at=watched_at,
            channel_name=_channel_name(entry.get("subtitles")),
        )

    def _is_advert(self, entry: dict) -> bool:
        details = entry.get("details")
        if not isinstance(details, list):
            return False
        return any(
            isinstance(detail, dict) and detail.get("name") == _ADVERT_DETAIL for detail in details
        )

    def _title(self, raw: object) -> str | None:
        """Strip the prefix Google puts on every title, where it recognises it.

        Also keeps score, because the prefix is the one signal in the file that
        says which language the export is in.
        """
        if not isinstance(raw, str) or not raw.strip():
            return None

        if raw.startswith(_WATCHED_PREFIX):
            self._english_titles += 1
            return raw[len(_WATCHED_PREFIX) :].strip() or None

        self._other_titles += 1
        return raw.strip()


def youtube_video_id(url: str) -> str | None:
    """Return the video a URL points at, or ``None`` if it points at no video.

    This is the filter that separates views from searches, channel visits and
    anything else sharing the file, so it identifies videos positively rather
    than excluding the shapes it happens to know about -- a URL it does not
    recognise is not a view.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    # ``hostname`` rather than ``netloc``: it is already lowercased and has any
    # port and credentials taken off, all of which would otherwise stop a
    # perfectly ordinary URL from matching.
    host = (parsed.hostname or "").removeprefix("www.")

    if host == "youtu.be":
        return _checked(parsed.path.strip("/").split("/")[0])

    # Any youtube.com subdomain -- m., music., the retired gaming., and whatever
    # replaces them. Matched on the leading dot, so youtube.com.example.net is
    # not one of them.
    if host != "youtube.com" and not host.endswith(".youtube.com"):
        return None

    if parsed.path == "/watch":
        values = parse_qs(parsed.query).get("v") or []
        return _checked(values[0]) if values else None

    for prefix in _ID_IN_PATH:
        if parsed.path.startswith(prefix):
            return _checked(parsed.path[len(prefix) :].split("/")[0])

    return None


def _checked(video_id: str) -> str | None:
    """Reject anything that is not shaped like a video id.

    Real ids are eleven base64url characters. The length is left loose, since
    guessing YouTube will never change it is how a parser starts silently
    dropping valid rows, but the alphabet is not: a "video id" carrying slashes
    or spaces came out of a URL that was never a video, and it would go on to be
    stored and put back into links.
    """
    return video_id if _VIDEO_ID.fullmatch(video_id) else None


def _first_line(error: Exception) -> str:
    """The readable part of a parser error, without the diagram under it.

    ijson draws an ASCII caret pointing at the offending byte, which is useful in
    a terminal and looks like the page is broken when it lands in a sentence on
    screen. The first line says what went wrong; the rest is scenery.
    """
    return str(error).splitlines()[0].strip() or error.__class__.__name__


def _parse_time(value: object) -> datetime | None:
    """Read Takeout's ISO 8601 timestamp as an instant in UTC.

    Converted rather than merely parsed: entries carry a trailing ``Z`` but not
    always, and the same moment written with an offset has to land on the same
    value or one view imports as two.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _channel_name(subtitles: object) -> str | None:
    """The channel, where the entry names one. Absent for some older rows."""
    if not isinstance(subtitles, list) or not subtitles:
        return None
    first = subtitles[0]
    if not isinstance(first, dict):
        return None
    name = first.get("name")
    return name.strip() or None if isinstance(name, str) else None


class _Rejoined:
    """A stream with its already-read first chunk put back on the front.

    :func:`open_youtube_export` has to look at the start of the file to reject
    the wrong one, and the stream it is handed may not rewind. Short reads are
    legal for a file object, so the head is simply returned first and everything
    after it comes from the real stream.
    """

    def __init__(self, head: bytes, rest: IO[bytes]) -> None:
        self._head = head
        self._rest = rest

    def read(self, size: int = -1) -> bytes:
        if not self._head:
            return self._rest.read(size)

        head, self._head = self._head, b""
        if size is None or size < 0:
            return head + self._rest.read()
        if len(head) > size:
            head, self._head = head[:size], head[size:]
        return head

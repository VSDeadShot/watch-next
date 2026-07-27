"""Parse Netflix's single-string title format into structured parts.

Netflix's viewing-history export puts show, season, and episode in one field:

    The Office (U.S.): Season 7: Ultimatum

Splitting on the colon looks correct and is wrong: ``Mission: Impossible -
Fallout`` and ``Avengers: Endgame`` are films whose titles contain colons, and
naive splitting invents a show called "Mission" with an episode called
"Impossible - Fallout". That failure is silent -- nothing raises, the numbers are
just quietly wrong -- which is the worst kind in a data pipeline.

So instead of splitting blindly we look for *structural markers* ("Season 7",
"Limited Series", "Part 1") and only treat a row as an episode when one is
present or the segment count makes it near-certain. Where the string genuinely
cannot be classified from its text alone, we pick the safer reading and set
``ambiguous``, leaving the final say to the JustWatch lookup, which knows whether
a title is a film or a show.

This module is pure: no I/O, no network, no database.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

# Season labels Netflix actually uses, e.g. "Season 7", "Part 1", "Series 2"
# (British), "Book 1" (Avatar), "Vol. 1". Prefix-anchored rather than
# full-string so compound labels like "Season 1 - Part 2" still yield a number.
_SEASON_NUMBERED = re.compile(
    r"^(?:season|series|part|volume|vol\.?|book)\s+(\d+)\b",
    re.IGNORECASE,
)

# Season labels with no number to extract.
_SEASON_UNNUMBERED = re.compile(r"^(?:limited series|mini-?series)\b", re.IGNORECASE)

# Netflix writes unnamed episodes as "Episode 4" or "Ep. 5". Digits only, which
# is what keeps "Star Wars: Episode IV - A New Hope" classified as a film.
_EPISODE_NUMBER = re.compile(r"^(?:episode|ep\.?)\s+(\d+)\b", re.IGNORECASE)

# "Chapter" is deliberately absent from the season markers above. It appears far
# more often as an episode title ("Chapter One: The Hellfire Club", The
# Mandalorian's "Chapter 1") than as a season label, so treating it as a season
# marker would misclassify more rows than it rescues. Shows using unusual season
# labels are caught by the segment-count fallback instead.

# Netflix separates the parts with a colon *and a space*. Splitting on a bare
# colon would corrupt titles that contain one as content -- Chernobyl's first
# episode is called "1:23:45" -- so the space is load-bearing.
_SEPARATOR = ": "


class TitleKind(StrEnum):
    """What a history row represents. Stored on watch events, hence StrEnum."""

    MOVIE = "movie"
    EPISODE = "episode"


@dataclass(frozen=True)
class ParsedTitle:
    """The structured reading of one raw Netflix title string.

    ``title`` is the searchable name -- the series name for an episode row, the
    film name for a film. It is what gets resolved against JustWatch, so callers
    never need to reassemble it.

    ``ambiguous`` means the kind was inferred from structure rather than proven
    by a marker. The resolver treats JustWatch's own answer as authoritative for
    these rows instead of trusting this guess.
    """

    raw: str
    kind: TitleKind
    title: str
    season_number: int | None = None
    episode_title: str | None = None
    episode_number: int | None = None
    ambiguous: bool = False


def parse_netflix_title(raw: str) -> ParsedTitle:
    """Parse one Netflix title string.

    Raises:
        ValueError: if the string contains no usable title. Callers should count
            these as skipped rows rather than aborting an import.
    """
    cleaned = raw.strip()

    # Separators and whitespace alone carry no title.
    if not cleaned.strip(":").strip():
        raise ValueError(f"no title in {raw!r}")

    # Split before trimming: stripping first would turn a truncated
    # "Some Show: Season 1: " into "...Season 1:", swallowing the final
    # separator and hiding the fact that an episode segment was expected.
    segments = [segment.strip() for segment in raw.split(_SEPARATOR)]

    # A leading empty segment means there is nothing to search for, whatever
    # follows it.
    if not segments[0]:
        raise ValueError(f"no title in {raw!r}")

    if len(segments) == 1:
        return ParsedTitle(raw=raw, kind=TitleKind.MOVIE, title=cleaned)

    if episode := _match_season_label(raw, segments):
        return episode

    if episode := _match_episode_label(raw, segments):
        return episode

    # Netflix's episode format is "Show: SeasonLabel: EpisodeTitle", so three or
    # more segments imply a show even when the season label is arbitrary text
    # ("Stranger Things: Stranger Things 4: ..."). Nothing proves it, hence
    # ambiguous.
    if len(segments) >= 3:
        episode_title = _join_or_none(segments[2:])
        return ParsedTitle(
            raw=raw,
            kind=TitleKind.EPISODE,
            title=segments[0],
            episode_title=episode_title,
            episode_number=_extract_episode_number(episode_title),
            ambiguous=True,
        )

    # Two segments, no marker: either a film with a colon in its name or a show
    # with an unlabelled season. Films are far more common in this shape, so we
    # read it as a film and let the resolver correct us.
    return ParsedTitle(raw=raw, kind=TitleKind.MOVIE, title=cleaned, ambiguous=True)


def _match_season_label(raw: str, segments: list[str]) -> ParsedTitle | None:
    """Find a season label that has an episode title after it.

    Requiring a following segment is what saves films like "Kill Bill: Vol. 1",
    whose title ends in something indistinguishable from a season label. A real
    episode row always has an episode title after the season label, because the
    row represents an episode rather than a whole season.
    """
    # Stop one short of the end so there is always a segment after the marker.
    for index in range(1, len(segments) - 1):
        segment = segments[index]

        if match := _SEASON_NUMBERED.match(segment):
            season_number = int(match.group(1))
        elif _SEASON_UNNUMBERED.match(segment):
            season_number = None
        else:
            continue

        episode_title = _join_or_none(segments[index + 1 :])
        return ParsedTitle(
            raw=raw,
            kind=TitleKind.EPISODE,
            title=_SEPARATOR.join(segments[:index]),
            season_number=season_number,
            episode_title=episode_title,
            episode_number=_extract_episode_number(episode_title),
        )

    return None


def _match_episode_label(raw: str, segments: list[str]) -> ParsedTitle | None:
    """Find a bare "Episode N" label, used by shows with no season label.

    Unlike a season label, this may be the final segment -- "Delhi Crime:
    Episode 4" is a complete episode row, since the label *is* the episode title.
    """
    for index in range(1, len(segments)):
        match = _EPISODE_NUMBER.match(segments[index])
        if not match:
            continue

        return ParsedTitle(
            raw=raw,
            kind=TitleKind.EPISODE,
            title=_SEPARATOR.join(segments[:index]),
            episode_title=_join_or_none(segments[index:]),
            episode_number=int(match.group(1)),
        )

    return None


def _extract_episode_number(episode_title: str | None) -> int | None:
    """Pull the number out of an episode title that is just "Episode 3"."""
    if not episode_title:
        return None

    match = _EPISODE_NUMBER.match(episode_title)
    return int(match.group(1)) if match else None


def _join_or_none(segments: list[str]) -> str | None:
    """Reassemble trailing segments, preserving any colons they contained.

    Returns None rather than an empty string for truncated rows like
    "Some Show: Season 1: ", so callers get a clean absent value.
    """
    return _SEPARATOR.join(segments).strip() or None

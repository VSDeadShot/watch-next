"""Tests for parsing Netflix's single-string title format.

Netflix packs show, season, and episode into one field: it exports
``The Office (U.S.): Season 7: Ultimatum`` rather than three columns. Recovering
the structure is guesswork, and the interesting cases are the ones where the
obvious approach (split on the colon) is actively wrong -- ``Mission: Impossible
- Fallout`` and ``Kill Bill: Vol. 1`` are films, not episodes.

The tables below are the specification. Each entry is a real-world title shape.
"""

import pytest

from app.core.title_parser import TitleKind, parse_netflix_title

# (raw, expected_title, expected_season, expected_episode_title)
EPISODE_CASES = [
    # --- Explicit "Season N" marker: the common, unambiguous case ------------
    (
        "The Office (U.S.): Season 7: Ultimatum",
        "The Office (U.S.)",
        7,
        "Ultimatum",
    ),
    ("Breaking Bad: Season 1: Pilot", "Breaking Bad", 1, "Pilot"),
    ("Arcane: Season 1: Welcome to the Playground", "Arcane", 1, "Welcome to the Playground"),
    # Multi-digit seasons must not be truncated to one character.
    (
        "Grey's Anatomy: Season 17: Someone Saved My Life",
        "Grey's Anatomy",
        17,
        "Someone Saved My Life",
    ),
    # Marker matching is case-insensitive -- casing varies across locales.
    ("Dark: SEASON 2: Beginnings and Endings", "Dark", 2, "Beginnings and Endings"),
    # --- Alternative season labels Netflix actually uses --------------------
    ("Money Heist: Part 1: Episode 1", "Money Heist", 1, "Episode 1"),
    ("Sherlock: Series 2: A Scandal in Belgravia", "Sherlock", 2, "A Scandal in Belgravia"),
    (
        "Avatar: The Last Airbender: Book 1: The Boy in the Iceberg",
        "Avatar: The Last Airbender",
        1,
        "The Boy in the Iceberg",
    ),
    # --- Unnumbered season labels ------------------------------------------
    ("The Queen's Gambit: Limited Series: Openings", "The Queen's Gambit", None, "Openings"),
    ("Maid: Limited Series: Cure", "Maid", None, "Cure"),
    # --- Season label that is arbitrary text (no marker word at all) --------
    # Netflix labels Stranger Things' seasons "Stranger Things 4", so there is
    # no "Season N" to find. Three-plus segments still implies a show.
    (
        "Stranger Things: Stranger Things 4: Chapter One: The Hellfire Club",
        "Stranger Things",
        None,
        # The episode title itself contains a colon and must survive intact.
        "Chapter One: The Hellfire Club",
    ),
    # --- Show titles containing a colon ------------------------------------
    (
        "Star Trek: Discovery: Season 1: The Vulcan Hello",
        "Star Trek: Discovery",
        1,
        "The Vulcan Hello",
    ),
    # --- Episode marker with no season label -------------------------------
    ("Delhi Crime: Episode 4", "Delhi Crime", None, "Episode 4"),
    # --- Colons *inside* an episode title ----------------------------------
    # Chernobyl's first episode is titled "1:23:45" (the time of the reactor
    # explosion). Netflix's separator is ": " with a space, so bare colons are
    # content and their exact spacing must survive the round trip.
    ("Chernobyl: Miniseries: 1:23:45", "Chernobyl", None, "1:23:45"),
    # --- Non-Latin scripts: no ASCII assumptions ---------------------------
    ("進撃の巨人: Season 1: エピソード1", "進撃の巨人", 1, "エピソード1"),
]

# (raw, expected_title)
MOVIE_CASES = [
    # --- No colon: trivially a film ----------------------------------------
    ("Inception", "Inception"),
    ("The Irishman", "The Irishman"),
    ("Zack Snyder's Justice League", "Zack Snyder's Justice League"),
    # --- Films whose *titles* contain a colon: the trap ---------------------
    # Splitting on the colon invents a show called "Mission" here.
    ("Mission: Impossible - Fallout", "Mission: Impossible - Fallout"),
    ("Avengers: Endgame", "Avengers: Endgame"),
    ("Spider-Man: No Way Home", "Spider-Man: No Way Home"),
    ("Blade Runner 2049: The Final Cut", "Blade Runner 2049: The Final Cut"),
    # --- Films whose titles contain a season-*looking* label ---------------
    # "Vol. 1" looks exactly like a season marker. What distinguishes it is
    # that nothing follows it -- a real episode row always has an episode
    # title after the season label, because the row represents an episode.
    ("Kill Bill: Vol. 1", "Kill Bill: Vol. 1"),
    ("Kill Bill: Vol. 2", "Kill Bill: Vol. 2"),
    # Roman numerals are not digits, so this stays a film.
    ("Star Wars: Episode IV - A New Hope", "Star Wars: Episode IV - A New Hope"),
    # A colon with no space after it is content, not a separator.
    ("12:01", "12:01"),
]


@pytest.mark.parametrize(("raw", "title", "season", "episode_title"), EPISODE_CASES)
def test_parses_episode_rows(raw, title, season, episode_title):
    result = parse_netflix_title(raw)

    assert result.kind is TitleKind.EPISODE
    assert result.title == title
    assert result.season_number == season
    assert result.episode_title == episode_title


@pytest.mark.parametrize(("raw", "title"), MOVIE_CASES)
def test_parses_movie_rows(raw, title):
    result = parse_netflix_title(raw)

    assert result.kind is TitleKind.MOVIE
    assert result.title == title
    assert result.season_number is None
    assert result.episode_title is None


def test_keeps_the_raw_string_unchanged():
    """The original is preserved so unresolved rows can be shown to the user."""
    raw = "Breaking Bad: Season 1: Pilot"

    assert parse_netflix_title(raw).raw == raw


def test_strips_surrounding_whitespace():
    assert parse_netflix_title("  Inception  ").title == "Inception"


class TestEpisodeNumber:
    """Episode numbers are extracted when Netflix states them explicitly."""

    def test_extracts_number_from_episode_label(self):
        result = parse_netflix_title("Sacred Games: Season 1: Episode 3")

        assert result.episode_number == 3

    def test_extracts_number_from_abbreviated_label(self):
        result = parse_netflix_title("Dark: Season 1: Ep. 5")

        assert result.episode_number == 5

    def test_is_none_when_the_episode_has_a_real_name(self):
        result = parse_netflix_title("Breaking Bad: Season 1: Pilot")

        assert result.episode_number is None


class TestAmbiguity:
    """`ambiguous` marks rows where the kind was inferred, not proven.

    The resolver uses this to prefer JustWatch's own answer about whether
    something is a film or a show, rather than trusting our guess.
    """

    def test_explicit_season_marker_is_not_ambiguous(self):
        assert parse_netflix_title("Breaking Bad: Season 1: Pilot").ambiguous is False

    def test_plain_title_is_not_ambiguous(self):
        assert parse_netflix_title("Inception").ambiguous is False

    def test_colon_in_film_title_is_ambiguous(self):
        """Two segments with no marker could be a film or a show. We guess film."""
        assert parse_netflix_title("Avengers: Endgame").ambiguous is True

    def test_unmarked_season_label_is_ambiguous(self):
        """Three segments imply a show, but nothing proves it."""
        result = parse_netflix_title("Stranger Things: Stranger Things 4: Chapter One")

        assert result.ambiguous is True


class TestRejectsUnusableInput:
    """Empty titles are a data error; the importer counts them as skipped."""

    @pytest.mark.parametrize("raw", ["", "   ", ":", " : : "])
    def test_raises_on_input_with_no_title(self, raw):
        with pytest.raises(ValueError, match="no title"):
            parse_netflix_title(raw)


def test_tolerates_a_trailing_season_separator():
    """A truncated export should degrade, not crash."""
    result = parse_netflix_title("Some Show: Season 1: ")

    assert result.kind is TitleKind.EPISODE
    assert result.title == "Some Show"
    assert result.season_number == 1
    assert result.episode_title is None

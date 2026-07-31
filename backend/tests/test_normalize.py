"""Tests for title normalisation.

Normalising is what lets "The Office (U.S.)" and "Office, The" be recognised as
the same search. It runs on both sides of every comparison, so it only has to be
consistent -- it does not have to produce something a human would want to read.
"""

import pytest

from app.core.normalize import normalize_title


class TestFoldsAwayCosmeticDifferences:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("Inception", "INCEPTION"),
            ("Mission: Impossible", "Mission Impossible"),
            ("Amelie", "Amélie"),
            ("Love, Death & Robots", "Love, Death and Robots"),
            ("Spider-Man", "Spider Man"),
            # Catalogues disagree about which dash they use, and an unspaced one
            # would otherwise glue the words together into "spiderman". The
            # suppressions are the point of the cases: these characters are
            # meant to be indistinguishable by eye.
            ("Spider–Man", "Spider-Man"),  # noqa: RUF001
            ("Spider—Man", "Spider-Man"),
            ("Spider−Man", "Spider-Man"),  # noqa: RUF001
            ("WALL·E", "WALL E"),
            ("Ocean's Eleven", "Oceans Eleven"),
            ("The   Office", "The Office"),
            (" Dune ", "Dune"),
        ],
    )
    def test_the_same_title_written_differently_normalises_alike(self, left: str, right: str):
        assert normalize_title(left) == normalize_title(right)


class TestStripsLeadingArticles:
    @pytest.mark.parametrize("raw", ["The Office", "office", "THE OFFICE"])
    def test_an_article_does_not_change_the_key(self, raw: str):
        assert normalize_title(raw) == "office"

    def test_an_article_inside_the_title_is_kept(self):
        """Only the leading article is noise; the rest carries meaning."""
        assert normalize_title("Pirates of the Caribbean") == normalize_title(
            "pirates of the caribbean"
        )
        assert "the" in normalize_title("Pirates of the Caribbean")

    def test_a_title_that_is_only_an_article_survives(self):
        """Stripping would leave nothing to search for."""
        assert normalize_title("The") == "the"


class TestStripsTrailingQualifiers:
    @pytest.mark.parametrize(
        "raw",
        [
            "The Office (U.S.)",
            "The Office (US)",
            "The Office (2005)",
            # Some catalogues disambiguate twice over.
            "The Office (U.S.) (2005)",
            "The Office",
        ],
    )
    def test_regional_and_year_suffixes_agree(self, raw: str):
        assert normalize_title(raw) == "office"

    def test_a_leading_parenthesis_is_part_of_the_title(self):
        """ "(500) Days of Summer" is not "Days of Summer"."""
        assert "500" in normalize_title("(500) Days of Summer")


class TestKeepsWhatDistinguishes:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("Dune", "Dune: Part Two"),
            ("The Office", "The Officer"),
            ("Star Wars: Episode IV", "Star Wars: Episode V"),
            ("Alone", "Alone Together"),
        ],
    )
    def test_different_titles_stay_different(self, left: str, right: str):
        assert normalize_title(left) != normalize_title(right)


class TestNonLatinTitles:
    def test_japanese_survives_normalisation(self):
        """Folding must not empty out a title that has no Latin characters."""
        assert normalize_title("進撃の巨人") == "進撃の巨人"

    def test_case_and_spacing_still_fold(self):
        assert normalize_title("  進撃の巨人  ") == normalize_title("進撃の巨人")


class TestNeverReturnsNothing:
    @pytest.mark.parametrize("raw", ["...", "!!!", "()", "   "])
    def test_punctuation_only_titles_keep_something_searchable(self, raw: str):
        """An empty key would collide with every other unparseable title."""
        assert normalize_title(raw) == raw.strip().lower()

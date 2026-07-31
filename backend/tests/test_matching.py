"""Tests for choosing a catalogue entry for a parsed title.

The behaviour these mostly describe is refusal. A matcher that always returns
its best guess will confidently attach the 1984 Dune to a 2021 viewing, and
nothing downstream can tell that it did. Being wrong silently is worse than
saying "I don't know" and letting someone pick, so most of what follows is about
when *not* to answer.
"""

import pytest

from app.core.matching import (
    MINIMUM_CONFIDENCE,
    Candidate,
    MatchMethod,
    MatchQuery,
    match_title,
)
from app.core.title_parser import TitleKind

DUNE_1984 = Candidate(node_id="tm84", title="Dune", object_type="MOVIE", release_year=1984)
DUNE_2021 = Candidate(node_id="tm21", title="Dune", object_type="MOVIE", release_year=2021)
FARGO_FILM = Candidate(node_id="tmf", title="Fargo", object_type="MOVIE", release_year=1996)
FARGO_SHOW = Candidate(node_id="tsf", title="Fargo", object_type="SHOW", release_year=2014)


def movie(title: str, year: int | None = None, ambiguous: bool = False) -> MatchQuery:
    return MatchQuery(title=title, kind=TitleKind.MOVIE, year=year, ambiguous=ambiguous)


def show(title: str, ambiguous: bool = False) -> MatchQuery:
    return MatchQuery(title=title, kind=TitleKind.EPISODE, ambiguous=ambiguous)


class TestConfidentMatches:
    def test_an_identical_title_resolves(self):
        result = match_title(movie("Inception"), [Candidate("tm1", "Inception", "MOVIE", 2010)])

        assert result.chosen is not None
        assert result.chosen.node_id == "tm1"
        assert result.method is MatchMethod.EXACT

    def test_cosmetic_differences_still_count_as_exact(self):
        """These are the same title; only the regional qualifier disagrees."""
        result = match_title(
            show("The Office (U.S.)"), [Candidate("ts1", "The Office", "SHOW", 2005)]
        )

        assert result.method is MatchMethod.EXACT

    def test_a_misspelling_resolves_as_fuzzy(self):
        """Same title, same length, a transposed pair of letters."""
        result = match_title(movie("Inceptoin"), [Candidate("tm1", "Inception", "MOVIE", 2010)])

        assert result.chosen is not None
        assert result.method is MatchMethod.FUZZY
        assert result.confidence >= MINIMUM_CONFIDENCE

    def test_the_better_of_two_candidates_wins(self):
        result = match_title(
            movie("Inception"),
            [
                Candidate("tm3", "Interstellar", "MOVIE", 2014),
                Candidate("tm1", "Inception", "MOVIE", 2010),
            ],
        )

        assert result.chosen is not None
        assert result.chosen.node_id == "tm1"


class TestRefusesToGuess:
    def test_two_equally_good_candidates_are_left_unresolved(self):
        """Searching "Dune" returns both films and nothing in the row says which.

        This is the case that makes the whole margin rule worth having.
        """
        result = match_title(movie("Dune"), [DUNE_1984, DUNE_2021])

        assert result.method is MatchMethod.UNRESOLVED
        assert result.chosen is None

    def test_the_refusal_says_it_could_not_choose_between_them(self):
        result = match_title(movie("Dune"), [DUNE_1984, DUNE_2021])

        assert "apart" in result.reason or "between" in result.reason

    @pytest.mark.parametrize(
        ("query", "candidate"),
        [
            ("Alone", "Alone Together"),
            ("Dune", "Dune: Part Two"),
            ("Star Wars", "Star Wars: Episode IV - A New Hope"),
            # A genuine alternate title is indistinguishable from the above by
            # the strings alone, so it goes to a person too.
            ("Amelie", "Amelie from Montmartre"),
        ],
    )
    def test_a_title_contained_in_a_longer_one_is_not_accepted(self, query: str, candidate: str):
        """The trap in every off-the-shelf token matcher.

        Token-set similarity scores a subset as a perfect match, so "Alone"
        matches "Alone Together" at 100. With a single candidate there is no
        runner-up for the margin rule to catch, and the wrong film gets attached
        with full confidence.
        """
        result = match_title(movie(query), [Candidate("tmx", candidate, "MOVIE", 2018)])

        assert result.method is MatchMethod.UNRESOLVED

    def test_the_shorter_of_two_still_wins_when_it_matches_exactly(self):
        """Refusing supersets must not stop the real title from being chosen."""
        result = match_title(
            movie("Dune"),
            [
                Candidate("a", "Dune: Part Two", "MOVIE", 2024),
                Candidate("b", "Dune", "MOVIE", 2021),
            ],
        )

        assert result.chosen is not None
        assert result.chosen.node_id == "b"

    @pytest.mark.parametrize(
        ("query", "candidate"),
        [
            ("Toy Story 3", "Toy Story 2"),
            ("Iron Man 2", "Iron Man 3"),
            ("Dune 2", "Dune"),
            ("Blade Runner 2049", "Blade Runner"),
        ],
    )
    def test_a_different_number_is_a_different_film(self, query: str, candidate: str):
        """Character similarity reads "2" against "3" as a one-letter typo.

        In a franchise the number is the only thing distinguishing the entries,
        and a number is never a misspelling -- so "Toy Story 3" scoring 0.91
        against "Toy Story 2" has to be rejected rather than accepted.
        """
        result = match_title(movie(query), [Candidate("tmx", candidate, "MOVIE", 2010)])

        assert result.method is MatchMethod.UNRESOLVED

    def test_the_same_number_is_still_a_match(self):
        result = match_title(movie("Toy Story 3"), [Candidate("tm5", "Toy Story 3", "MOVIE", 2010)])

        assert result.chosen is not None
        assert result.method is MatchMethod.EXACT

    def test_roman_numerals_are_left_to_the_title_comparison(self):
        """ "Episode IV" carries no digits, so the numeric rule does not apply."""
        result = match_title(
            movie("Star Wars: Episode IV - A New Hope"),
            [Candidate("tm6", "Star Wars: Episode IV - A New Hope", "MOVIE", 1977)],
        )

        assert result.chosen is not None

    def test_nothing_similar_enough_is_left_unresolved(self):
        result = match_title(
            movie("Inception"),
            [
                Candidate("tm3", "Interstellar", "MOVIE", 2014),
                Candidate("tm4", "Tenet", "MOVIE", 2020),
            ],
        )

        assert result.method is MatchMethod.UNRESOLVED

    def test_an_empty_search_is_left_unresolved(self):
        result = match_title(movie("Some Obscure Thing"), [])

        assert result.method is MatchMethod.UNRESOLVED
        assert result.chosen is None
        assert result.ranked == ()

    def test_a_refusal_still_returns_the_candidates_it_saw(self):
        """The UI turns these into a one-click manual fix, so a refusal has to
        hand back what it was choosing between rather than just failing."""
        result = match_title(movie("Dune"), [DUNE_1984, DUNE_2021])

        assert {scored.candidate.node_id for scored in result.ranked} == {"tm84", "tm21"}


class TestYearBreaksTies:
    def test_a_known_year_picks_between_two_films_of_the_same_name(self):
        result = match_title(movie("Dune", year=2021), [DUNE_1984, DUNE_2021])

        assert result.chosen is not None
        assert result.chosen.node_id == "tm21"

    def test_the_other_year_picks_the_other_film(self):
        result = match_title(movie("Dune", year=1984), [DUNE_1984, DUNE_2021])

        assert result.chosen is not None
        assert result.chosen.node_id == "tm84"

    def test_a_year_one_out_is_not_punished(self):
        """Release years differ by country, so a year is a hint, not a fact."""
        result = match_title(movie("Dune", year=2022), [DUNE_2021])

        assert result.chosen is not None


class TestKindBreaksTies:
    def test_an_episode_row_prefers_the_series(self):
        result = match_title(show("Fargo"), [FARGO_FILM, FARGO_SHOW])

        assert result.chosen is not None
        assert result.chosen.node_id == "tsf"

    def test_a_film_row_prefers_the_film(self):
        result = match_title(movie("Fargo"), [FARGO_FILM, FARGO_SHOW])

        assert result.chosen is not None
        assert result.chosen.node_id == "tmf"

    def test_a_guessed_kind_does_not_veto_the_catalogue(self):
        """The parser reads a two-part title as a film and flags the guess. The
        catalogue knows better, so an ambiguous guess must not exclude a show."""
        result = match_title(
            movie("Some Show: Unlabelled", ambiguous=True),
            [Candidate("ts9", "Some Show: Unlabelled", "SHOW", 2019)],
        )

        assert result.chosen is not None
        assert result.chosen.node_id == "ts9"

    def test_a_confident_kind_does_veto_it(self):
        """A proven "Season 3" row is not a film, whatever the title matches."""
        result = match_title(
            show("Barely Similar Title"), [Candidate("tm9", "Barely Similar Titles", "MOVIE", 2019)]
        )

        assert result.method is MatchMethod.UNRESOLVED


class TestRanking:
    def test_candidates_come_back_best_first(self):
        result = match_title(
            movie("Inception"),
            [
                Candidate("tm3", "Interstellar", "MOVIE", 2014),
                Candidate("tm1", "Inception", "MOVIE", 2010),
            ],
        )

        scores = [scored.score for scored in result.ranked]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.parametrize("candidates", [[DUNE_2021], [DUNE_1984, DUNE_2021], []])
    def test_confidence_stays_a_probability(self, candidates: list[Candidate]):
        result = match_title(movie("Dune"), candidates)

        assert 0.0 <= result.confidence <= 1.0

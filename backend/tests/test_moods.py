"""Tests for moods and the time budget.

Two questions get asked at the moment somebody wants something to watch, and
neither of them is about their history: what do I feel like, and how long have I
got. This is where both become something a scorer can use.

Pure functions over plain values: no database, no network, no clock.
"""

import pytest

from app.core.genres import (
    ACTION,
    ANIMATION,
    COMEDY,
    DOCUMENTARY,
    DRAMA,
    FAMILY,
    FANTASY,
    GENRE_NAMES,
    HORROR,
    SCIENCE_FICTION,
    THRILLER,
)
from app.core.moods import (
    MOOD_GENRE_WEIGHTS,
    RUNTIME_GRACE,
    UNKNOWN_RUNTIME_FIT,
    Mood,
    fits,
    mood_fit,
    mood_label,
    runtime_fit,
    runtime_window,
)


class TestTheMoodTable:
    def test_every_mood_has_an_entry(self):
        """A mood with no entry silently scores every title zero, which looks
        like a recommender with no opinion rather than like a missing row."""
        assert set(MOOD_GENRE_WEIGHTS) == set(Mood)

    def test_every_genre_named_is_a_genre_that_exists(self):
        """A mistyped code is invisible -- it simply never matches -- so the one
        place it can be caught is here."""
        named = {genre for weights in MOOD_GENRE_WEIGHTS.values() for genre in weights}

        assert named <= set(GENRE_NAMES)

    def test_every_mood_has_something_it_scores_full_marks(self):
        """A mood whose best genre scores 0.6 is quietly weaker than the others,
        and nothing about the feeling justifies that."""
        for mood, weights in MOOD_GENRE_WEIGHTS.items():
            if mood is Mood.SURPRISE_ME:
                continue
            assert max(weights.values()) == pytest.approx(1.0), mood

    def test_no_weight_is_outside_the_scale(self):
        for mood, weights in MOOD_GENRE_WEIGHTS.items():
            for genre, weight in weights.items():
                assert 0.0 < weight <= 1.0, (mood, genre)

    def test_every_mood_reads_as_english(self):
        for mood in Mood:
            assert mood_label(mood) != mood.value

    def test_the_table_cannot_be_edited(self):
        with pytest.raises(TypeError):
            MOOD_GENRE_WEIGHTS[Mood.LAUGH] = {}


class TestMoodFit:
    def test_the_genre_a_mood_is_about_scores_full_marks(self):
        assert mood_fit(Mood.LAUGH, (COMEDY,)) == pytest.approx(1.0)

    def test_a_genre_the_mood_is_not_about_scores_nothing(self):
        assert mood_fit(Mood.LAUGH, (HORROR,)) == 0.0

    def test_the_best_matching_genre_wins_rather_than_the_average(self):
        """Deliberately unlike the taste affinity, which averages. A mood is a
        request for one quality -- a war comedy still makes you laugh -- whereas
        taste describes a whole habit."""
        assert mood_fit(Mood.LAUGH, (COMEDY, HORROR, DOCUMENTARY)) == pytest.approx(1.0)

    def test_carrying_several_genres_a_mood_likes_does_not_stack(self):
        """A comedy is a comedy. Tagging it animation and family as well does
        not make it funnier than the funniest thing on offer, and a fit that
        added up past 1.0 would outrank every honest perfect match."""
        assert mood_fit(Mood.LAUGH, (COMEDY, ANIMATION, FAMILY)) == pytest.approx(1.0)

    def test_is_never_outside_the_scale(self):
        for mood, weights in MOOD_GENRE_WEIGHTS.items():
            assert 0.0 <= mood_fit(mood, tuple(weights)) <= 1.0, mood

    def test_a_partial_match_scores_partly(self):
        fit = mood_fit(Mood.THRILL, (ACTION,))

        assert 0.0 < fit < 1.0

    def test_a_title_with_no_genres_scores_nothing(self):
        assert mood_fit(Mood.LAUGH, ()) == 0.0

    def test_an_unrecognised_genre_code_scores_nothing_rather_than_raising(self):
        """JustWatch can add a genre at any time, and a recommendation request
        is not the place to find that out."""
        assert mood_fit(Mood.LAUGH, ("zzz",)) == 0.0

    def test_surprise_me_has_no_opinion_about_any_genre(self):
        """Not a weak opinion -- none at all, so taste and quality decide. Every
        candidate in one request is treated the same way, so a flat zero here
        changes the ranking not at all."""
        for genres in ((COMEDY,), (HORROR,), (DOCUMENTARY,), ()):
            assert mood_fit(Mood.SURPRISE_ME, genres) == 0.0

    @pytest.mark.parametrize(
        ("mood", "genre"),
        [
            (Mood.LAUGH, COMEDY),
            (Mood.THRILL, THRILLER),
            (Mood.THINK, DOCUMENTARY),
            (Mood.MOVED, DRAMA),
            (Mood.ESCAPE, FANTASY),
            (Mood.ESCAPE, SCIENCE_FICTION),
        ],
    )
    def test_each_mood_points_at_the_genre_somebody_would_expect(self, mood: Mood, genre: str):
        """The table is a product decision, not an implementation detail. If
        wanting a thriller stops returning thrillers, that is worth a failure."""
        assert mood_fit(mood, (genre,)) == pytest.approx(1.0)


class TestTheTimeBudget:
    def test_an_hour_and_a_half_allows_a_little_over_an_hour_and_a_half(self):
        """Nobody who says "about ninety minutes" means they will walk out at
        ninety-one, and a film rejected for being four minutes long is a worse
        answer than no film."""
        window = runtime_window(90)

        assert window.max_minutes == round(90 * (1 + RUNTIME_GRACE))

    def test_no_budget_means_no_limit(self):
        window = runtime_window(None)

        assert window.max_minutes is None

    def test_a_nonsensical_budget_is_treated_as_no_budget(self):
        """Rather than dividing by zero somewhere further down, where the cause
        would be much harder to see."""
        assert runtime_window(0).max_minutes is None
        assert runtime_window(-30).max_minutes is None


class TestWhatFitsTheBudget:
    def test_something_shorter_than_the_budget_fits(self):
        assert fits(95, runtime_window(120))

    def test_something_far_longer_does_not(self):
        assert not fits(180, runtime_window(90))

    def test_something_just_over_still_fits(self):
        assert fits(95, runtime_window(90))

    def test_an_unknown_runtime_fits(self):
        """JustWatch frequently has no runtime for a show. Excluding those would
        quietly remove most series from consideration for ever, which is not a
        decision anybody made."""
        assert fits(None, runtime_window(90))

    def test_everything_fits_when_there_is_no_budget(self):
        assert fits(240, runtime_window(None))
        assert fits(None, runtime_window(None))


class TestHowWellSomethingFits:
    def test_using_almost_all_of_the_time_scores_best(self):
        assert runtime_fit(115, runtime_window(120)) > runtime_fit(70, runtime_window(120))

    def test_a_short_episode_in_a_long_evening_scores_poorly(self):
        """Not disqualifying -- twenty minutes is a fine thing to watch -- but
        it is not what somebody with two free hours asked for."""
        assert runtime_fit(22, runtime_window(120)) < 0.3

    def test_using_the_grace_still_scores_full_marks(self):
        """Slightly over is exactly what the grace exists to permit, so it must
        not be scored as a worse fit than slightly under."""
        assert runtime_fit(125, runtime_window(120)) == pytest.approx(1.0)

    def test_something_that_does_not_fit_at_all_scores_nothing(self):
        assert runtime_fit(200, runtime_window(90)) == 0.0

    def test_an_unknown_runtime_is_neither_rewarded_nor_buried(self):
        """Scored below a title we measured and found to be a good fit, above
        one we measured and found to be a poor one. Missing data should not win
        against evidence, and it should not be a punishment either."""
        window = runtime_window(120)

        assert runtime_fit(None, window) == pytest.approx(UNKNOWN_RUNTIME_FIT)
        assert runtime_fit(115, window) > runtime_fit(None, window)
        assert runtime_fit(20, window) < runtime_fit(None, window)

    def test_with_no_budget_every_runtime_scores_the_same(self):
        """A constant, because "no opinion" and "everything is perfect" rank
        candidates identically and only one of them is honest to write down."""
        window = runtime_window(None)

        assert runtime_fit(20, window) == runtime_fit(200, window)

    def test_is_never_outside_the_scale(self):
        window = runtime_window(100)

        for runtime in (1, 50, 99, 100, 101, 110, 111, 500):
            assert 0.0 <= runtime_fit(runtime, window) <= 1.0, runtime

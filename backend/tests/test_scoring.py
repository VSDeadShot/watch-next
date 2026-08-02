"""Tests for scoring one candidate against a taste, a mood and a clock.

This is where the app decides. Availability has already had its say by the time
anything gets here -- it is a gate, never a weight -- so what is left is the
question of which of several watchable things is the *right* one tonight.

Two properties matter more than any individual weight. The ranking has to be
deterministic, because a recommender that returns a different answer to the same
question looks broken rather than clever. And it has to be able to explain
itself, because "watch this" with no reason attached is not advice.

Pure functions over plain records: no database, no network, no clock of its own.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.genres import COMEDY, DOCUMENTARY, DRAMA, HORROR, THRILLER
from app.core.moods import Mood
from app.core.scoring import (
    GOOD_ENOUGH_TO_MENTION,
    MAX_REASONS,
    WATCHLIST_BONUS,
    CandidateTitle,
    KindPreference,
    RecommendationRequest,
    is_eligible,
    rank_titles,
    score_title,
)
from app.core.taste import MOVIE, SHOW, WatchRecord, build_taste_profile

NOW = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)


def profile_of(*genres: str, titles: int = 8):
    """A taste profile built from a history of one genre, watched recently."""
    records = [
        WatchRecord(
            title_id=index,
            watched_at=NOW - timedelta(days=index),
            object_type=MOVIE,
            genres=genres,
            runtime_minutes=110,
            release_year=2020,
        )
        for index in range(1, titles + 1)
    ]
    return build_taste_profile(records, now=NOW)


EMPTY_PROFILE = build_taste_profile([], now=NOW)


def candidate(
    title_id: int = 1,
    *,
    title: str = "Something",
    object_type: str = MOVIE,
    genres: tuple[str, ...] = (COMEDY,),
    runtime_minutes: int | None = 100,
    release_year: int | None = 2021,
    imdb_score: float | None = 7.5,
    tmdb_score: float | None = None,
    on_watchlist: bool = False,
) -> CandidateTitle:
    return CandidateTitle(
        title_id=title_id,
        title=title,
        object_type=object_type,
        genres=genres,
        runtime_minutes=runtime_minutes,
        release_year=release_year,
        imdb_score=imdb_score,
        tmdb_score=tmdb_score,
        on_watchlist=on_watchlist,
    )


class TestTheHardGates:
    """Nothing here is a preference. A candidate that fails one of these cannot
    be recommended at all, however well it would have scored."""

    def test_a_film_is_not_offered_to_somebody_who_asked_for_a_series(self):
        request = RecommendationRequest(kind=KindPreference.SERIES)

        assert not is_eligible(candidate(object_type=MOVIE), request)

    def test_a_series_is_not_offered_to_somebody_who_asked_for_a_film(self):
        request = RecommendationRequest(kind=KindPreference.MOVIE)

        assert not is_eligible(candidate(object_type=SHOW), request)

    def test_either_will_do_by_default(self):
        request = RecommendationRequest()

        assert is_eligible(candidate(object_type=MOVIE), request)
        assert is_eligible(candidate(object_type=SHOW), request)

    def test_something_longer_than_the_evening_is_not_offered(self):
        request = RecommendationRequest(minutes_available=45)

        assert not is_eligible(candidate(runtime_minutes=160), request)

    def test_something_of_unknown_length_is_still_offered(self):
        """Most shows have no runtime in the catalogue, and excluding them would
        silently make this a film recommender."""
        request = RecommendationRequest(minutes_available=45)

        assert is_eligible(candidate(runtime_minutes=None), request)

    def test_the_gates_ignore_taste_entirely(self):
        """A gate that could be talked round by a high score is not a gate."""
        request = RecommendationRequest(kind=KindPreference.MOVIE, minutes_available=30)

        assert not is_eligible(candidate(object_type=SHOW, runtime_minutes=25), request)


class TestWhatMovesTheScore:
    def test_something_in_a_favourite_genre_beats_something_that_is_not(self):
        profile = profile_of(COMEDY)
        request = RecommendationRequest()

        liked = score_title(candidate(1, genres=(COMEDY,)), profile, request)
        disliked = score_title(candidate(2, genres=(HORROR,)), profile, request)

        assert liked.score > disliked.score

    def test_the_mood_asked_for_beats_the_habit(self):
        """The point of asking. Somebody who watches comedy all year and says
        they want a thriller tonight is telling us something we do not already
        know, and it has to be able to win."""
        profile = profile_of(COMEDY)
        request = RecommendationRequest(mood=Mood.THRILL)

        thriller = score_title(candidate(1, genres=(THRILLER,)), profile, request)
        comedy = score_title(candidate(2, genres=(COMEDY,)), profile, request)

        assert thriller.score > comedy.score

    def test_the_better_rated_of_two_equals_wins(self):
        profile = profile_of(COMEDY)
        request = RecommendationRequest()

        good = score_title(candidate(1, imdb_score=8.4), profile, request)
        poor = score_title(candidate(2, imdb_score=4.1), profile, request)

        assert good.score > poor.score

    def test_a_tmdb_score_is_used_when_imdb_has_none(self):
        profile = profile_of(COMEDY)
        request = RecommendationRequest()

        rated = score_title(candidate(1, imdb_score=None, tmdb_score=8.8), profile, request)
        unrated = score_title(candidate(2, imdb_score=None, tmdb_score=None), profile, request)

        assert rated.score > unrated.score

    def test_something_that_fills_the_evening_beats_something_that_barely_starts_it(self):
        profile = profile_of(COMEDY)
        request = RecommendationRequest(minutes_available=120)

        feature = score_title(candidate(1, runtime_minutes=115), profile, request)
        episode = score_title(candidate(2, runtime_minutes=22), profile, request)

        assert feature.score > episode.score

    def test_the_watchlist_wins_a_tie(self):
        """Somebody who wrote a title down has told us something explicit, and
        explicit beats inferred when there is nothing else to separate them."""
        profile = profile_of(COMEDY)
        request = RecommendationRequest()

        wanted = score_title(candidate(1, on_watchlist=True), profile, request)
        neither = score_title(candidate(2), profile, request)

        assert wanted.score == pytest.approx(neither.score + WATCHLIST_BONUS)

    def test_the_watchlist_does_not_win_everything(self):
        """A bonus, not an override. A watchlist entry that suits neither the
        mood nor the evening should still lose to something that does."""
        profile = profile_of(COMEDY)
        request = RecommendationRequest(mood=Mood.THRILL, minutes_available=120)

        stale = score_title(
            candidate(
                1,
                genres=(DOCUMENTARY,),
                runtime_minutes=25,
                imdb_score=5.0,
                on_watchlist=True,
            ),
            profile,
            request,
        )
        apt = score_title(
            candidate(2, genres=(THRILLER,), runtime_minutes=118, imdb_score=8.2),
            profile,
            request,
        )

        assert apt.score > stale.score

    def test_the_score_stays_on_a_sane_scale(self):
        profile = profile_of(COMEDY)
        request = RecommendationRequest(mood=Mood.LAUGH, minutes_available=100)

        best = score_title(
            candidate(genres=(COMEDY,), runtime_minutes=100, imdb_score=10.0, on_watchlist=True),
            profile,
            request,
        )
        worst = score_title(
            candidate(genres=(HORROR,), runtime_minutes=5, imdb_score=0.0),
            profile,
            request,
        )

        assert worst.score >= 0.0
        assert best.score <= 1.0 + WATCHLIST_BONUS


class TestColdStart:
    def test_a_history_too_thin_to_read_is_not_read(self):
        """One evening of viewing is not a taste. Acting on it would produce a
        confident recommendation built on a single data point."""
        thin = profile_of(COMEDY, titles=1)
        request = RecommendationRequest()

        scored = score_title(candidate(genres=(COMEDY,)), thin, request)

        assert scored.components["taste"] == 0.0

    def test_with_no_history_quality_and_mood_still_decide(self):
        request = RecommendationRequest(mood=Mood.LAUGH)

        funny = score_title(candidate(1, genres=(COMEDY,), imdb_score=8.0), EMPTY_PROFILE, request)
        grim = score_title(candidate(2, genres=(HORROR,), imdb_score=4.0), EMPTY_PROFILE, request)

        assert funny.score > grim.score

    def test_an_unrated_title_is_not_treated_as_a_bad_one(self):
        """Obscure is not the same as poor, and burying everything the catalogue
        has no score for would quietly restrict this to famous titles."""
        request = RecommendationRequest()

        unrated = score_title(candidate(1, imdb_score=None), EMPTY_PROFILE, request)
        bad = score_title(candidate(2, imdb_score=2.0), EMPTY_PROFILE, request)

        assert unrated.score > bad.score


class TestSayingWhy:
    def test_a_recommendation_always_comes_with_a_reason(self):
        profile = profile_of(COMEDY)
        request = RecommendationRequest(mood=Mood.LAUGH, minutes_available=100)

        scored = score_title(candidate(genres=(COMEDY,)), profile, request)

        assert scored.reasons

    def test_the_reasons_name_the_genre_in_english(self):
        profile = profile_of(COMEDY)

        scored = score_title(candidate(genres=(COMEDY,)), profile, RecommendationRequest())

        assert any("Comedy" in reason for reason in scored.reasons)

    def test_the_genre_named_is_the_titles_own_not_the_overall_favourite(self):
        """Explaining a comedy with "you watch a lot of drama" is a sentence
        that does not survive being read, however true its second half is."""
        mostly_drama = [
            WatchRecord(title_id=index, watched_at=NOW, genres=(DRAMA,)) for index in range(1, 9)
        ]
        some_comedy = [
            WatchRecord(title_id=index, watched_at=NOW, genres=(COMEDY,)) for index in range(9, 14)
        ]
        profile = build_taste_profile(mostly_drama + some_comedy, now=NOW)

        scored = score_title(candidate(genres=(COMEDY,)), profile, RecommendationRequest())

        assert profile.top_genres(1) == (DRAMA,)
        assert any("Comedy" in reason for reason in scored.reasons)
        assert not any("Drama" in reason for reason in scored.reasons)

    def test_the_watchlist_is_said_out_loud(self):
        scored = score_title(candidate(on_watchlist=True), EMPTY_PROFILE, RecommendationRequest())

        assert any("watchlist" in reason.lower() for reason in scored.reasons)

    def test_the_time_is_said_out_loud_when_it_was_asked_about(self):
        request = RecommendationRequest(minutes_available=120)

        scored = score_title(candidate(runtime_minutes=115), EMPTY_PROFILE, request)

        assert any("115" in reason for reason in scored.reasons)

    def test_the_time_is_not_mentioned_when_nobody_asked(self):
        scored = score_title(candidate(runtime_minutes=115), EMPTY_PROFILE, RecommendationRequest())

        assert not any("115" in reason for reason in scored.reasons)

    def test_a_taste_too_thin_to_read_is_not_claimed_as_a_reason(self):
        """Saying "you watch a lot of comedy" to somebody who has watched one
        comedy is the kind of wrong that makes a person stop believing the rest
        of it."""
        thin = profile_of(COMEDY, titles=1)

        scored = score_title(candidate(genres=(COMEDY,)), thin, RecommendationRequest())

        assert not any("Comedy" in reason for reason in scored.reasons)

    def test_a_mediocre_rating_is_not_advertised(self):
        """A literal rating rather than one derived from the threshold, so that
        moving the threshold is something this test can notice."""
        scored = score_title(candidate(imdb_score=5.4), EMPTY_PROFILE, RecommendationRequest())

        assert not any("rated" in reason.lower() for reason in scored.reasons)

    def test_the_bar_for_mentioning_a_rating_is_a_recommendation_worthy_one(self):
        assert GOOD_ENOUGH_TO_MENTION >= 7.0

    def test_a_good_rating_is(self):
        scored = score_title(candidate(imdb_score=8.6), EMPTY_PROFILE, RecommendationRequest())

        assert any("8.6" in reason for reason in scored.reasons)

    def test_a_rating_off_the_scale_is_not_quoted_back(self):
        """The score survives a bad rating because it is clamped. The sentence
        quotes the number as it came, and "rated 47 out of 10" is the kind of
        thing somebody screenshots."""
        scored = score_title(candidate(imdb_score=47.0), EMPTY_PROFILE, RecommendationRequest())

        assert not any("47" in reason for reason in scored.reasons)

    def test_a_rating_off_the_scale_still_cannot_outscore_a_perfect_one(self):
        request = RecommendationRequest()

        absurd = score_title(candidate(1, imdb_score=47.0), EMPTY_PROFILE, request)
        perfect = score_title(candidate(2, imdb_score=10.0), EMPTY_PROFILE, request)

        assert absurd.score == pytest.approx(perfect.score)

    def test_an_absent_rating_is_never_described_as_a_rating(self):
        scored = score_title(
            candidate(imdb_score=None, tmdb_score=None), EMPTY_PROFILE, RecommendationRequest()
        )

        assert not any("rated" in reason.lower() for reason in scored.reasons)

    def test_there_are_never_more_reasons_than_somebody_will_read(self):
        """This candidate earns all five reasons there are. Three come back --
        a literal three, so that raising the cap is something this notices."""
        profile = profile_of(COMEDY)
        request = RecommendationRequest(mood=Mood.LAUGH, minutes_available=100)

        scored = score_title(
            candidate(genres=(COMEDY,), runtime_minutes=98, imdb_score=9.1, on_watchlist=True),
            profile,
            request,
        )

        assert len(scored.reasons) == 3
        assert MAX_REASONS == 3

    def test_the_strongest_reason_comes_first(self):
        """The watchlist is worth less than taste on the score, so with a strong
        taste behind it the genre has to lead."""
        profile = profile_of(COMEDY)
        request = RecommendationRequest()

        scored = score_title(candidate(genres=(COMEDY,), on_watchlist=True), profile, request)

        assert "Comedy" in scored.reasons[0]

    def test_the_same_candidate_always_gives_the_same_reasons(self):
        profile = profile_of(COMEDY, DRAMA)
        request = RecommendationRequest(mood=Mood.LAUGH, minutes_available=90)
        subject = candidate(genres=(COMEDY, DRAMA), runtime_minutes=88, imdb_score=8.0)

        first = score_title(subject, profile, request)
        second = score_title(subject, profile, request)

        assert first.reasons == second.reasons


class TestRanking:
    def test_returns_the_best_first(self):
        profile = profile_of(COMEDY)
        request = RecommendationRequest()
        candidates = [
            candidate(1, genres=(HORROR,), imdb_score=5.0),
            candidate(2, genres=(COMEDY,), imdb_score=8.5),
            candidate(3, genres=(DRAMA,), imdb_score=6.0),
        ]

        ranked = rank_titles(candidates, profile, request)

        assert [scored.candidate.title_id for scored in ranked] == [2, 3, 1]

    def test_drops_everything_that_fails_a_gate(self):
        request = RecommendationRequest(kind=KindPreference.MOVIE, minutes_available=60)
        candidates = [
            candidate(1, object_type=SHOW, runtime_minutes=30),
            candidate(2, object_type=MOVIE, runtime_minutes=200),
            candidate(3, object_type=MOVIE, runtime_minutes=55),
        ]

        ranked = rank_titles(candidates, EMPTY_PROFILE, request)

        assert [scored.candidate.title_id for scored in ranked] == [3]

    def test_a_watchlist_entry_that_will_not_fit_is_still_dropped(self):
        """The bonus settles close calls. It does not buy a way past the clock,
        and something somebody cannot finish tonight is not an answer."""
        request = RecommendationRequest(minutes_available=45)
        candidates = [
            candidate(1, runtime_minutes=200, on_watchlist=True),
            candidate(2, runtime_minutes=40),
        ]

        ranked = rank_titles(candidates, EMPTY_PROFILE, request)

        assert [scored.candidate.title_id for scored in ranked] == [2]

    def test_an_empty_pool_ranks_to_nothing_rather_than_raising(self):
        assert rank_titles([], EMPTY_PROFILE, RecommendationRequest()) == ()

    def test_ties_break_the_same_way_every_time(self):
        """Two candidates the scorer genuinely cannot separate must not swap
        places between two identical requests. The app promises one answer, and
        an answer that changes at random is not one."""
        request = RecommendationRequest()
        identical = [candidate(7), candidate(3), candidate(5)]

        first = rank_titles(identical, EMPTY_PROFILE, request)
        second = rank_titles(list(reversed(identical)), EMPTY_PROFILE, request)

        assert [scored.candidate.title_id for scored in first] == [3, 5, 7]
        assert [scored.candidate.title_id for scored in first] == [
            scored.candidate.title_id for scored in second
        ]

"""Tests for the taste profile.

The profile is the app's only opinion about a person, and it is built from the
one thing it actually knows: what they watched and when. These tests are mostly
about the two ways a naive reading of a watch history lies.

A binge is one decision, not sixty. Somebody who watched every episode of a
sitcom made one choice about comedy, and counting each episode as a separate
vote drowns out every film they have ever seen.

Taste is not permanent. What somebody watched last month says more about tonight
than what they watched four years ago, so the history is weighted by age rather
than counted flat.

Pure functions over plain records: no database, no network, no clock of its own.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.genres import COMEDY, DOCUMENTARY, DRAMA, HORROR, ROMANCE, THRILLER
from app.core.taste import (
    HALF_LIFE_DAYS,
    MOVIE,
    SHOW,
    WatchRecord,
    build_taste_profile,
)

NOW = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)


def watched(
    title_id: int,
    *,
    days_ago: float = 0.0,
    genres: tuple[str, ...] = (COMEDY,),
    object_type: str = MOVIE,
    runtime_minutes: int | None = 100,
    release_year: int | None = 2020,
) -> WatchRecord:
    return WatchRecord(
        title_id=title_id,
        watched_at=NOW - timedelta(days=days_ago),
        object_type=object_type,
        genres=genres,
        runtime_minutes=runtime_minutes,
        release_year=release_year,
    )


def binge(title_id: int, episodes: int, **extra) -> list[WatchRecord]:
    """One show, watched over consecutive evenings."""
    return [
        watched(title_id, days_ago=extra.pop("days_ago", 0.0) + index, object_type=SHOW, **extra)
        for index in range(episodes)
    ]


class TestAnEmptyHistory:
    """Everything downstream runs before a single import, so the profile has to
    be usable when it knows nothing at all."""

    def test_has_no_genre_opinions(self):
        profile = build_taste_profile([], now=NOW)

        assert profile.genre_weights == {}

    def test_is_neutral_about_every_title(self):
        profile = build_taste_profile([], now=NOW)

        assert profile.affinity((COMEDY, DRAMA)) == 0.0

    def test_has_no_runtime_habit(self):
        profile = build_taste_profile([], now=NOW)

        assert profile.mean_runtime_minutes is None

    def test_knows_it_is_empty(self):
        profile = build_taste_profile([], now=NOW)

        assert profile.titles == 0
        assert profile.sessions == 0
        assert not profile.is_informative


class TestWhatGetsCounted:
    def test_the_top_genre_scores_one(self):
        """Weights are relative, not absolute. A library of nine titles and one
        of nine hundred should both hand the scorer a 0-to-1 affinity."""
        profile = build_taste_profile([watched(1, genres=(COMEDY,))], now=NOW)

        assert profile.genre_weights[COMEDY] == pytest.approx(1.0)

    def test_a_genre_never_watched_is_absent_rather_than_zero(self):
        profile = build_taste_profile([watched(1, genres=(COMEDY,))], now=NOW)

        assert HORROR not in profile.genre_weights
        assert profile.affinity((HORROR,)) == 0.0

    def test_a_titles_weight_is_shared_between_its_genres(self):
        """A romantic comedy is half a vote for each. Giving both a full vote
        would let a heavily tagged title outweigh a plainly tagged one for no
        reason a person would recognise."""
        records = [
            watched(1, genres=(COMEDY, ROMANCE)),
            watched(2, genres=(DRAMA,)),
        ]

        profile = build_taste_profile(records, now=NOW)

        assert profile.genre_weights[DRAMA] == pytest.approx(1.0)
        assert profile.genre_weights[COMEDY] == pytest.approx(0.5)
        assert profile.genre_weights[ROMANCE] == pytest.approx(0.5)

    def test_a_title_with_no_genres_is_not_an_opinion_about_genre(self):
        records = [watched(1, genres=()), watched(2, genres=(DRAMA,))]

        profile = build_taste_profile(records, now=NOW)

        assert set(profile.genre_weights) == {DRAMA}

    def test_a_genre_code_we_have_never_seen_is_kept(self):
        """JustWatch is free to add one, and an unrecognised code is still a real
        thing somebody watched. Dropping it would lose signal silently."""
        profile = build_taste_profile([watched(1, genres=("zzz",))], now=NOW)

        assert profile.genre_weights["zzz"] == pytest.approx(1.0)


class TestABingeIsOneDecision:
    def test_sixty_episodes_do_not_bury_a_film(self):
        """The bug this whole design exists to avoid. Counting episodes flat
        would give comedy sixty votes to drama's one, and every recommendation
        for the rest of time would be a sitcom."""
        records = [*binge(1, 60, genres=(COMEDY,)), watched(2, genres=(DRAMA,))]

        profile = build_taste_profile(records, now=NOW)

        assert profile.genre_weights[DRAMA] > 0.4

    def test_but_a_binge_still_counts_for_more_than_one_film(self):
        """It is not one vote either. Somebody who watched sixty episodes of a
        show liked it more than somebody who watched one film once."""
        binged = build_taste_profile(binge(1, 60, genres=(COMEDY,)), now=NOW)
        once = build_taste_profile(
            [watched(1, genres=(COMEDY,), object_type=SHOW)],
            now=NOW,
        )

        assert binged.weight_of(1) > once.weight_of(1)

    def test_the_extra_weight_grows_slowly(self):
        """Twice the episodes is nothing like twice the evidence. Doubling a
        binge should move the weight a little, not double it."""
        sixty = build_taste_profile(binge(1, 60), now=NOW).weight_of(1)
        hundred_and_twenty = build_taste_profile(binge(1, 120), now=NOW).weight_of(1)

        assert hundred_and_twenty > sixty
        assert hundred_and_twenty < 1.5 * sixty

    def test_two_shows_watched_equally_weigh_the_same(self):
        records = [*binge(1, 20, genres=(COMEDY,)), *binge(2, 20, genres=(DRAMA,))]

        profile = build_taste_profile(records, now=NOW)

        assert profile.genre_weights[COMEDY] == pytest.approx(profile.genre_weights[DRAMA])

    def test_distinct_titles_are_counted_separately_from_sessions(self):
        records = [*binge(1, 10), watched(2), watched(3)]

        profile = build_taste_profile(records, now=NOW)

        assert profile.titles == 3
        assert profile.sessions == 12


class TestOldTasteFadesOut:
    def test_something_watched_recently_outweighs_something_old(self):
        records = [
            watched(1, genres=(COMEDY,), days_ago=4 * HALF_LIFE_DAYS),
            watched(2, genres=(DRAMA,), days_ago=0),
        ]

        profile = build_taste_profile(records, now=NOW)

        assert profile.genre_weights[DRAMA] > profile.genre_weights[COMEDY]

    def test_one_half_life_ago_counts_half(self):
        records = [
            watched(1, genres=(COMEDY,), days_ago=HALF_LIFE_DAYS),
            watched(2, genres=(DRAMA,), days_ago=0),
        ]

        profile = build_taste_profile(records, now=NOW)

        assert profile.genre_weights[COMEDY] == pytest.approx(0.5)

    def test_a_show_is_dated_by_its_most_recent_episode(self):
        """Somebody who finished a show last night is still interested in it,
        however long ago they started."""
        records = [
            *binge(1, 30, genres=(COMEDY,), days_ago=0),
            watched(2, genres=(DRAMA,), days_ago=0),
        ]

        profile = build_taste_profile(records, now=NOW)

        assert profile.genre_weights[COMEDY] == pytest.approx(1.0)

    def test_a_timestamp_in_the_future_is_treated_as_now(self):
        """Clock skew between a machine and its database is not a reason to
        hand one title unbounded weight."""
        records = [
            watched(1, genres=(COMEDY,), days_ago=-400),
            watched(2, genres=(DRAMA,), days_ago=0),
        ]

        profile = build_taste_profile(records, now=NOW)

        assert profile.genre_weights[COMEDY] == pytest.approx(1.0)
        assert profile.genre_weights[DRAMA] == pytest.approx(1.0)

    def test_nothing_is_ever_weighed_at_zero(self):
        """Far enough back the decay underflows a float outright, and a profile
        whose every weight has collapsed to zero has no opinions at all -- which
        is worse than faint ones."""
        profile = build_taste_profile([watched(1, days_ago=2000 * HALF_LIFE_DAYS)], now=NOW)

        assert profile.weight_of(1) > 0.0


class TestAffinity:
    def test_averages_over_a_titles_genres(self):
        """Scored the same way it was learned. A comedy-horror is not as much
        somebody's thing as a straight comedy when they never watch horror."""
        records = [watched(1, genres=(COMEDY,)), watched(2, genres=(DRAMA,))]

        profile = build_taste_profile(records, now=NOW)

        assert profile.affinity((COMEDY,)) == pytest.approx(1.0)
        assert profile.affinity((COMEDY, HORROR)) == pytest.approx(0.5)

    def test_a_title_with_no_genres_gets_no_credit(self):
        profile = build_taste_profile([watched(1, genres=(COMEDY,))], now=NOW)

        assert profile.affinity(()) == 0.0

    def test_never_exceeds_one(self):
        records = [watched(1, genres=(COMEDY, DRAMA))]

        profile = build_taste_profile(records, now=NOW)

        assert profile.affinity((COMEDY, DRAMA)) <= 1.0


class TestTopGenres:
    def test_are_ordered_by_weight(self):
        records = [
            watched(1, genres=(COMEDY,)),
            watched(2, genres=(COMEDY,)),
            watched(3, genres=(DRAMA,)),
            watched(4, genres=(THRILLER,), days_ago=3 * HALF_LIFE_DAYS),
        ]

        profile = build_taste_profile(records, now=NOW)

        assert profile.top_genres(2) == (COMEDY, DRAMA)

    def test_ties_break_the_same_way_every_time(self):
        """Two genres watched exactly as much must not swap places between
        calls; a reason that changes wording at random reads like a bug."""
        records = [watched(1, genres=(DRAMA,)), watched(2, genres=(COMEDY,))]

        profile = build_taste_profile(records, now=NOW)

        assert profile.top_genres() == build_taste_profile(records, now=NOW).top_genres()

    def test_asking_for_more_than_exist_returns_what_there_is(self):
        profile = build_taste_profile([watched(1, genres=(COMEDY,))], now=NOW)

        assert profile.top_genres(5) == (COMEDY,)


class TestRuntimeHabit:
    def test_is_the_weighted_average_of_what_was_watched(self):
        records = [watched(1, runtime_minutes=90), watched(2, runtime_minutes=110)]

        profile = build_taste_profile(records, now=NOW)

        assert profile.mean_runtime_minutes == pytest.approx(100.0)

    def test_ignores_titles_whose_runtime_is_unknown(self):
        """JustWatch often has no runtime for a show. Reading that as zero would
        drag the average towards a habit nobody has."""
        records = [watched(1, runtime_minutes=90), watched(2, runtime_minutes=None)]

        profile = build_taste_profile(records, now=NOW)

        assert profile.mean_runtime_minutes == pytest.approx(90.0)

    def test_is_unknown_when_no_runtime_is_known_at_all(self):
        profile = build_taste_profile([watched(1, runtime_minutes=None)], now=NOW)

        assert profile.mean_runtime_minutes is None

    def test_recent_viewing_moves_it_more_than_old_viewing(self):
        records = [
            watched(1, runtime_minutes=40, days_ago=4 * HALF_LIFE_DAYS),
            watched(2, runtime_minutes=140, days_ago=0),
        ]

        profile = build_taste_profile(records, now=NOW)

        assert profile.mean_runtime_minutes > 120


class TestFilmsVersusSeries:
    def test_a_history_of_only_films_has_no_series_share(self):
        profile = build_taste_profile([watched(1), watched(2)], now=NOW)

        assert profile.series_share == pytest.approx(0.0)

    def test_a_history_of_only_series_is_all_series(self):
        profile = build_taste_profile(binge(1, 5), now=NOW)

        assert profile.series_share == pytest.approx(1.0)

    def test_a_binge_does_not_make_somebody_a_series_watcher_by_itself(self):
        """Same rollup as the genre weights, for the same reason: sixty episodes
        is one show, and a person who also watches films still watches films."""
        records = [*binge(1, 60), watched(2), watched(3)]

        profile = build_taste_profile(records, now=NOW)

        assert profile.series_share < 0.6


class TestDecades:
    def test_group_by_release_decade(self):
        records = [
            watched(1, release_year=2021),
            watched(2, release_year=2024),
            watched(3, release_year=1997),
        ]

        profile = build_taste_profile(records, now=NOW)

        assert profile.decade_weights[2020] == pytest.approx(1.0)
        assert profile.decade_weights[1990] == pytest.approx(0.5)

    def test_a_title_with_no_release_year_is_in_no_decade(self):
        records = [watched(1, release_year=None), watched(2, release_year=2001)]

        profile = build_taste_profile(records, now=NOW)

        assert set(profile.decade_weights) == {2000}


class TestBeingInformative:
    def test_one_film_is_not_enough_to_have_a_taste(self):
        """Cold start is real and worth admitting to. Two evenings of history is
        not a taste, and pretending otherwise produces confident nonsense."""
        profile = build_taste_profile([watched(1)], now=NOW)

        assert not profile.is_informative

    def test_a_real_history_is(self):
        records = [watched(index, genres=(COMEDY,)) for index in range(1, 20)]

        profile = build_taste_profile(records, now=NOW)

        assert profile.is_informative

    def test_a_binge_of_one_show_is_not_a_taste(self):
        """Sixty episodes of one sitcom is one data point about one show, not a
        picture of what somebody likes."""
        profile = build_taste_profile(binge(1, 60), now=NOW)

        assert not profile.is_informative


class TestTheProfileIsInert:
    def test_its_weights_cannot_be_edited(self):
        profile = build_taste_profile([watched(1)], now=NOW)

        with pytest.raises(TypeError):
            profile.genre_weights[DOCUMENTARY] = 1.0

    def test_the_same_history_always_gives_the_same_answer(self):
        records = [*binge(1, 7), watched(2, genres=(DRAMA, THRILLER)), watched(3, genres=())]

        first = build_taste_profile(records, now=NOW)
        second = build_taste_profile(list(reversed(records)), now=NOW)

        assert first.genre_weights == second.genre_weights
        assert first.mean_runtime_minutes == second.mean_runtime_minutes

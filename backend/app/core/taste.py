"""What somebody actually watches, learned from what they have watched.

The recommender needs an opinion about a person, and the only honest source for
one is their history. Turning a list of viewing sessions into that opinion is
not a matter of counting, because a raw count lies in two specific ways.

**A binge is one decision, not sixty.** Somebody who watched every episode of a
sitcom made a single choice about comedy. Counting each episode as its own vote
gives that one choice sixty times the weight of every film they have ever seen,
and from then on the app recommends sitcoms for ever. So sessions are rolled up
per title first, and a title's weight grows with the logarithm of how many
sessions it has: watching more of something does mean more, but nothing like
proportionally more.

**Taste is not permanent.** What somebody watched last month predicts tonight
far better than what they watched four years ago, so every title is weighted by
how recently it was watched, halving every :data:`HALF_LIFE_DAYS`.

Everything the profile exposes is relative -- the favourite genre scores 1.0
whether the history holds nine titles or nine hundred -- so the scorer can treat
it as a 0-to-1 affinity without knowing how big the library is.

This module is pure: no I/O, no network, no clock of its own -- ``now`` is passed
in, which is what makes the decay testable.
"""

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

# JustWatch's two kinds of thing. Defined here rather than repeated as bare
# strings at each comparison: this is the first module that has to reason about
# the distinction publicly, and a mistyped "SHOWS" would silently classify a
# whole library as films.
MOVIE = "MOVIE"
SHOW = "SHOW"

# How long it takes for something watched to count half as much. A year is
# chosen to be roughly the span over which somebody's viewing habits visibly
# change: shorter and one unusual month rewrites the whole profile, longer and a
# phase somebody grew out of keeps being recommended back to them.
HALF_LIFE_DAYS = 365.0

# How much extra weight bingeing earns, per natural log of the session count.
# At 0.25, sixty episodes of one show weigh about twice a single film rather
# than sixty times it -- which is roughly how much more it tells us.
ENGAGEMENT_BONUS = 0.25

# The floor under a title's recency. A history old enough for the decay to
# underflow a float is still a history, and a profile whose every weight has
# collapsed to zero has no opinions at all -- which is worse than faint ones.
MINIMUM_RECENCY = 1e-6

# How many distinct titles it takes before the profile is worth listening to.
# Two evenings of history is not a taste, and a recommender that acts as though
# it were produces confident nonsense -- so it says so instead, and the caller
# can lean on quality and mood until there is more to go on.
MINIMUM_TITLES_FOR_TASTE = 5

_SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class WatchRecord:
    """One viewing session, reduced to what taste can be inferred from.

    Deliberately not a database row. The profile is built from a join across
    watch events and catalogue titles, and taking a plain record means this can
    be tested -- and reasoned about -- without either of them.
    """

    title_id: int
    watched_at: datetime
    object_type: str = MOVIE
    genres: tuple[str, ...] = ()
    # For a series this is JustWatch's per-episode runtime, which is the right
    # comparable: a viewing session is one episode, not one series.
    runtime_minutes: int | None = None
    release_year: int | None = None


@dataclass(frozen=True)
class TasteProfile:
    """What we believe somebody likes, on a scale of nothing to their favourite.

    ``genre_weights`` and ``decade_weights`` are normalised so the strongest
    scores 1.0, because the scorer needs a comparable affinity and the size of a
    library is not information about its owner's taste.

    ``title_weights`` is deliberately *not* normalised. It is the raw evidence
    each title contributed, which is what makes it comparable between two
    profiles -- useful for asking whether a binge really did outweigh a film.

    ``decade_weights`` is not read by the scorer and is not an oversight: it is
    a description of somebody's viewing rather than a reason to recommend one
    thing over another, and it is computed here because this is the only place
    that already knows how much each title counts for.
    """

    genre_weights: Mapping[str, float] = field(default_factory=dict)
    decade_weights: Mapping[int, float] = field(default_factory=dict)
    title_weights: Mapping[int, float] = field(default_factory=dict)
    mean_runtime_minutes: float | None = None
    # The share of the evidence that came from series rather than films, rolled
    # up per title -- so somebody who binged one show and watched two films is
    # about half a series watcher, not a pure one.
    series_share: float = 0.0
    titles: int = 0
    sessions: int = 0

    @property
    def is_informative(self) -> bool:
        """Whether there is enough history here to be worth weighing at all."""
        return self.titles >= MINIMUM_TITLES_FOR_TASTE

    def affinity(self, genres: Sequence[str]) -> float:
        """How much this looks like somebody's kind of thing, from 0 to 1.

        Averaged over the title's genres, mirroring the way the weights were
        learned: a title hands its evidence out in equal shares to each genre it
        carries, so it collects its credit back the same way. A comedy-horror is
        genuinely less somebody's thing than a straight comedy when they never
        watch horror, and averaging is what says so.

        A title with no genres scores 0. That is not a judgement of it -- the
        score is a sum of evidence, and there is none here either way.
        """
        if not genres:
            return 0.0
        return sum(self.genre_weights.get(genre, 0.0) for genre in genres) / len(genres)

    def weight_of(self, title_id: int) -> float:
        """The raw evidence one title contributed, or 0 if it is not in here."""
        return self.title_weights.get(title_id, 0.0)

    def top_genres(self, limit: int = 3) -> tuple[str, ...]:
        """The strongest genres, best first, for saying why out loud.

        Ties break on the genre code so that the same history always produces
        the same sentence. A reason whose wording changes between two identical
        requests reads as a bug, whatever the numbers underneath are doing.
        """
        ranked = sorted(self.genre_weights.items(), key=lambda item: (-item[1], item[0]))
        return tuple(genre for genre, _ in ranked[:limit])


@dataclass
class _TitleEvidence:
    """Everything one distinct title contributed, before it is weighed."""

    sessions: int = 0
    recency: float = 0.0
    object_type: str = MOVIE
    genres: tuple[str, ...] = ()
    runtime_minutes: int | None = None
    release_year: int | None = None

    @property
    def weight(self) -> float:
        """Recency, scaled by how much of the thing was actually watched.

        ``log`` rather than the session count itself: the difference between
        watching one episode and watching thirty is real evidence, the
        difference between thirty and sixty is barely any, and a linear count
        cannot express both.
        """
        return self.recency * (1 + ENGAGEMENT_BONUS * math.log(self.sessions))


def build_taste_profile(
    records: Iterable[WatchRecord],
    *,
    now: datetime,
    half_life_days: float = HALF_LIFE_DAYS,
) -> TasteProfile:
    """Read a watch history into a taste profile.

    Empty history is a supported answer, not an error: every caller runs before
    the first import, and a profile with no opinions is exactly the right thing
    to hand them.
    """
    evidence = _roll_up(records, now=now, half_life_days=half_life_days)
    if not evidence:
        return TasteProfile()

    weights = {title_id: found.weight for title_id, found in evidence.items()}

    return TasteProfile(
        genre_weights=_normalized(_genre_totals(evidence, weights)),
        decade_weights=_normalized(_decade_totals(evidence, weights)),
        title_weights=MappingProxyType(weights),
        mean_runtime_minutes=_mean_runtime(evidence, weights),
        series_share=_series_share(evidence, weights),
        titles=len(evidence),
        sessions=sum(found.sessions for found in evidence.values()),
    )


def _roll_up(
    records: Iterable[WatchRecord], *, now: datetime, half_life_days: float
) -> dict[int, _TitleEvidence]:
    """Collapse sessions into one entry per distinct title.

    A title is dated by its *most recent* session, not its first. Somebody who
    finished a show last night is still interested in it, however long ago they
    started it -- and taking the earliest date would age a show out of the
    profile exactly when it had most recently been watched.
    """
    evidence: dict[int, _TitleEvidence] = {}
    for record in records:
        found = evidence.get(record.title_id)
        if found is None:
            # The catalogue metadata is the same on every session for a title,
            # because it comes from one catalogue row, so the first one seen is
            # as good as any other.
            found = _TitleEvidence(
                object_type=record.object_type,
                genres=record.genres,
                runtime_minutes=record.runtime_minutes,
                release_year=record.release_year,
            )
            evidence[record.title_id] = found
        found.sessions += 1
        found.recency = max(found.recency, _recency(record.watched_at, now, half_life_days))
    return evidence


def _recency(watched_at: datetime, now: datetime, half_life_days: float) -> float:
    """How much something watched then still counts now, from 0 to 1.

    A timestamp in the future counts as fully recent rather than as more than
    fully recent. Clock skew between a machine and its database is real, and it
    is not a reason to hand one title unbounded weight over everything else.
    """
    age_days = (now - watched_at).total_seconds() / _SECONDS_PER_DAY
    if age_days <= 0:
        return 1.0
    return max(MINIMUM_RECENCY, 0.5 ** (age_days / half_life_days))


def _genre_totals(
    evidence: Mapping[int, _TitleEvidence], weights: Mapping[int, float]
) -> dict[str, float]:
    """Hand each title's weight out in equal shares to the genres it carries.

    Shares rather than copies: a title tagged with four genres is not four times
    the evidence of one tagged with a single genre, and giving each genre the
    full weight would say that it is.

    An unrecognised code is kept as it is. JustWatch can add a genre whenever it
    likes, and a code we have never seen still describes something somebody
    genuinely watched.
    """
    totals: dict[str, float] = defaultdict(float)
    for title_id, found in evidence.items():
        if not found.genres:
            continue
        share = weights[title_id] / len(found.genres)
        for genre in found.genres:
            totals[genre] += share
    return dict(totals)


def _decade_totals(
    evidence: Mapping[int, _TitleEvidence], weights: Mapping[int, float]
) -> dict[int, float]:
    totals: dict[int, float] = defaultdict(float)
    for title_id, found in evidence.items():
        if found.release_year is None:
            continue
        totals[(found.release_year // 10) * 10] += weights[title_id]
    return dict(totals)


def _series_share(evidence: Mapping[int, _TitleEvidence], weights: Mapping[int, float]) -> float:
    """How much of the evidence came from series rather than films.

    Weighted like everything else, so somebody who binged one show and watched
    two films is about half a series watcher rather than a pure one -- the same
    rollup that stops a binge dominating the genre weights.
    """
    total = sum(weights.values())
    if not total:
        return 0.0
    series = sum(
        weights[title_id] for title_id, found in evidence.items() if found.object_type == SHOW
    )
    return series / total


def _mean_runtime(
    evidence: Mapping[int, _TitleEvidence], weights: Mapping[int, float]
) -> float | None:
    """The length of sitting somebody is used to, weighted towards lately.

    Titles with no runtime are left out rather than read as zero. JustWatch
    often has no runtime for a show, and counting those as zero-minute viewings
    would drag the average down towards a habit nobody has.
    """
    weighted = 0.0
    total = 0.0
    for title_id, found in evidence.items():
        if found.runtime_minutes is None:
            continue
        weight = weights[title_id]
        weighted += weight * found.runtime_minutes
        total += weight
    if not total:
        return None
    return weighted / total


def _normalized(totals: Mapping[str, float] | Mapping[int, float]):
    """Rescale so the strongest entry is 1.0, and freeze the result.

    Relative rather than absolute because the size of a library says nothing
    about its owner's taste: a favourite genre is a favourite genre whether it
    came from nine titles or nine hundred, and the scorer needs one comparable
    number either way.
    """
    if not totals:
        return MappingProxyType({})
    strongest = max(totals.values())
    if strongest <= 0:
        return MappingProxyType({})
    return MappingProxyType({key: value / strongest for key, value in totals.items()})

"""Choosing between things somebody could watch, and saying why.

Availability has already had its say by the time anything reaches this module.
It is a gate, never a weight -- a title the user cannot actually watch tonight is
not a worse recommendation, it is not a recommendation at all -- so it is applied
before scoring and is deliberately absent from every number below.

What is left is the interesting question: of several watchable things, which is
the right one *tonight*. Four pieces of evidence answer it, and the weights they
carry are a product decision rather than a derivation, so they live together in
one block where they can be argued with:

* **mood** carries the most, because it is the only input that describes tonight
  rather than the past. Somebody who watches comedy all year and asks for a
  thriller is telling us something we do not already know, and it has to be able
  to beat a year of history.
* **taste** and **quality** carry the same, one saying "this is your sort of
  thing" and the other "this is good".
* **runtime** carries least, because it has already had its say as a gate:
  everything still in the running fits, and this only prefers the thing that
  uses the evening rather than barely starting it.

The other half of the job is the explanation. "Watch this" with no reason
attached is not advice, and a reason invented after the fact would be worse than
none -- so the reasons are read straight off the components that actually moved
the score, strongest first.

This module is pure: no I/O, no network, no clock of its own.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from app.core.genres import genre_name
from app.core.moods import Mood, RuntimeWindow, fits, mood_fit, mood_label, runtime_fit
from app.core.moods import runtime_window as _runtime_window
from app.core.taste import MOVIE, SHOW, TasteProfile

# --- What each piece of evidence is worth ------------------------------------
# They add to 1.0, so a score is readable as a proportion of the best possible
# case. The watchlist sits outside that on purpose: it is not evidence about
# whether somebody would enjoy this, it is a note they left themselves, and it
# should settle a close call rather than take part in the argument.
WEIGHT_MOOD = 0.35
WEIGHT_TASTE = 0.25
WEIGHT_QUALITY = 0.25
WEIGHT_RUNTIME = 0.15

WATCHLIST_BONUS = 0.15

# What an unrated title scores for quality. Obscure is not the same as bad, and
# scoring missing data as zero would quietly restrict this to famous titles.
UNKNOWN_QUALITY = 0.5

# The highest rating either source gives, used to put both on the same 0-to-1
# scale. IMDb and TMDB both mark out of ten.
MAX_RATING = 10.0

# --- When a component is worth saying out loud -------------------------------
# Separate from the weights, because a component can move the score a little
# without being worth a sentence. Claiming "you watch a lot of comedy" on the
# strength of one comedy is the kind of wrong that makes somebody stop believing
# the rest of the explanation too.
GOOD_ENOUGH_TO_MENTION = 7.0
STRONG_GENRE_AFFINITY = 0.5
STRONG_MOOD_FIT = 0.5
STRONG_RUNTIME_FIT = 0.6

# Three is about as many as anybody reads before deciding to press play.
MAX_REASONS = 3

TASTE = "taste"
MOOD = "mood"
QUALITY = "quality"
RUNTIME = "runtime"
WATCHLIST = "watchlist"


class KindPreference(StrEnum):
    """Whether somebody wants a film, a series, or does not mind."""

    ANY = "any"
    MOVIE = "movie"
    SERIES = "series"


_OBJECT_TYPE_FOR_PREFERENCE = {KindPreference.MOVIE: MOVIE, KindPreference.SERIES: SHOW}


@dataclass(frozen=True)
class CandidateTitle:
    """Something that could be recommended, reduced to what decides it.

    Availability is not on here, and its absence is the design: everything that
    gets this far is already known to be watchable, so there is no field a
    scoring bug could accidentally trade against.
    """

    title_id: int
    title: str
    object_type: str = MOVIE
    genres: tuple[str, ...] = ()
    # For a series this is JustWatch's per-episode runtime, which is what an
    # evening's budget should be measured against: nobody sets aside time for a
    # whole series.
    runtime_minutes: int | None = None
    release_year: int | None = None
    imdb_score: float | None = None
    tmdb_score: float | None = None
    on_watchlist: bool = False


@dataclass(frozen=True)
class RecommendationRequest:
    """What was asked for tonight. Every field has a defensible default, so a
    request with nothing filled in is still a request."""

    mood: Mood = Mood.SURPRISE_ME
    minutes_available: int | None = None
    kind: KindPreference = KindPreference.ANY

    @property
    def window(self) -> RuntimeWindow:
        return _runtime_window(self.minutes_available)


@dataclass(frozen=True)
class ScoredTitle:
    """One candidate, weighed, with the working shown.

    ``components`` holds the *weighted* contributions, so they add up to the
    score. Keeping them is what lets the reasons be read off what actually moved
    the number rather than reconstructed afterwards from the same inputs.
    """

    candidate: CandidateTitle
    score: float = 0.0
    components: Mapping[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()


def is_eligible(candidate: CandidateTitle, request: RecommendationRequest) -> bool:
    """The hard gates that are not about availability. Nothing outscores these.

    A gate that a high score could talk its way round is not a gate: somebody
    who asked for a film has said something about tonight that no amount of
    genre affinity gets a vote on.
    """
    wanted = _OBJECT_TYPE_FOR_PREFERENCE.get(request.kind)
    if wanted is not None and candidate.object_type != wanted:
        return False
    return fits(candidate.runtime_minutes, request.window)


def score_title(
    candidate: CandidateTitle, profile: TasteProfile, request: RecommendationRequest
) -> ScoredTitle:
    """Weigh one candidate, and work out what to say about it.

    Does not check the gates. Scoring something ineligible is meaningless rather
    than wrong, and keeping the two apart means a gate can never be quietly
    turned into a preference by a change to a weight.
    """
    affinity = profile.affinity(candidate.genres) if profile.is_informative else 0.0
    feeling = mood_fit(request.mood, candidate.genres)
    length = runtime_fit(candidate.runtime_minutes, request.window)
    rating = _rating(candidate)

    components = {
        MOOD: WEIGHT_MOOD * feeling,
        TASTE: WEIGHT_TASTE * affinity,
        QUALITY: WEIGHT_QUALITY * _quality(rating),
        RUNTIME: WEIGHT_RUNTIME * length,
        WATCHLIST: WATCHLIST_BONUS if candidate.on_watchlist else 0.0,
    }

    return ScoredTitle(
        candidate=candidate,
        score=sum(components.values()),
        components=MappingProxyType(components),
        reasons=_reasons(
            candidate,
            profile,
            request,
            components,
            affinity=affinity,
            feeling=feeling,
            length=length,
            rating=rating,
        ),
    )


def rank_titles(
    candidates: Iterable[CandidateTitle], profile: TasteProfile, request: RecommendationRequest
) -> tuple[ScoredTitle, ...]:
    """Every eligible candidate, best first.

    Ties break on the title id so that two candidates the scorer genuinely
    cannot separate come back in the same order every time. The app promises one
    answer to a question, and an answer that changes at random between two
    identical requests is not one.
    """
    scored = [
        score_title(candidate, profile, request)
        for candidate in candidates
        if is_eligible(candidate, request)
    ]
    scored.sort(key=lambda title: (-title.score, title.candidate.title_id))
    return tuple(scored)


def _rating(candidate: CandidateTitle) -> float | None:
    """The best rating we have, out of ten, or None if nobody has scored it.

    IMDb is preferred where both exist: more people vote on it, and it is the
    number somebody is most likely to recognise when they read it back.
    """
    if candidate.imdb_score is not None:
        return candidate.imdb_score
    return candidate.tmdb_score


def _quality(rating: float | None) -> float:
    if rating is None:
        return UNKNOWN_QUALITY
    return min(1.0, max(0.0, rating / MAX_RATING))


def _reasons(
    candidate: CandidateTitle,
    profile: TasteProfile,
    request: RecommendationRequest,
    components: Mapping[str, float],
    *,
    affinity: float,
    feeling: float,
    length: float,
    rating: float | None,
) -> tuple[str, ...]:
    """Say why, strongest first, and only where there is something to say.

    Each reason is gated on its own evidence rather than on its contribution, so
    nothing is claimed that is not true -- a title can pick up runtime credit for
    fitting an evening without that being worth mentioning. The *order* then
    comes from the contributions, so the sentence somebody reads first is the
    one that actually did the most work.
    """
    said: dict[str, str] = {}

    # No check on ``profile.is_informative`` here: a history too thin to read
    # has already had its affinity zeroed by the caller, so a second guard would
    # be dead code that looks like the thing keeping this honest.
    genre = _defining_genre(candidate, profile)
    if affinity >= STRONG_GENRE_AFFINITY and genre is not None:
        said[TASTE] = f"you watch a lot of {genre_name(genre)}"

    # No check for "surprise me" here, for the same reason: it carries no genre
    # weights, so its fit is always zero and the gate below already refuses it.
    if feeling >= STRONG_MOOD_FIT:
        said[MOOD] = f"a good match when you want {mood_label(request.mood)}"

    # Bounded above as well as below. The score survives a rating off the scale
    # because it is clamped, but the sentence quotes the number as it came --
    # and "rated 47 out of 10" is the kind of thing somebody screenshots.
    if rating is not None and GOOD_ENOUGH_TO_MENTION <= rating <= MAX_RATING:
        said[QUALITY] = f"rated {rating:g} out of 10"

    if (
        not request.window.unbounded
        and candidate.runtime_minutes is not None
        and length >= STRONG_RUNTIME_FIT
    ):
        said[RUNTIME] = (
            f"{candidate.runtime_minutes} min fits the {request.window.minutes_available} you have"
        )

    if candidate.on_watchlist:
        said[WATCHLIST] = "it has been on your watchlist"

    # Ties break on the component name for the same reason the ranking breaks
    # on the title id: an explanation whose sentences reorder between two
    # identical requests reads as a bug, whatever the numbers are doing.
    ordered = sorted(said, key=lambda name: (-components[name], name))
    return tuple(said[name] for name in ordered[:MAX_REASONS])


def _defining_genre(candidate: CandidateTitle, profile: TasteProfile) -> str | None:
    """Whichever of *this title's* genres the person watches most.

    Taken from the candidate rather than from the profile's overall favourite,
    because the reason has to be about the thing being offered. Explaining a
    documentary with "you watch a lot of comedy" is a sentence that does not
    survive being read, however true the second half of it is.

    Ties break on the code so the same title always produces the same sentence.
    """
    if not candidate.genres:
        return None
    return max(
        candidate.genres,
        key=lambda genre: (profile.genre_weights.get(genre, 0.0), genre),
    )

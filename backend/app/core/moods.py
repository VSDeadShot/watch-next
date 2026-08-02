"""What somebody feels like tonight, and how long they have got.

The taste profile knows what a person watches in general. Neither of the two
questions asked at the moment of choosing is about that. "I want to laugh" is a
request that may contradict a year of history, and "I have forty minutes" is a
hard fact about tonight rather than a preference at all -- and both have to beat
the profile when they disagree with it, or the app is just a mirror.

The mood table is a product decision written down in one place. It is opinion,
not derivation: nothing computes that wanting a thriller means thrillers, crime
and a bit of action, and pretending otherwise would hide the one part of this
file somebody might reasonably want to argue with.

This module is pure: no I/O, no network, no clock.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from app.core.genres import (
    ACTION,
    ANIMATION,
    COMEDY,
    CRIME,
    DOCUMENTARY,
    DRAMA,
    FAMILY,
    FANTASY,
    HISTORY,
    HORROR,
    MUSIC,
    ROMANCE,
    SCIENCE_FICTION,
    THRILLER,
    WAR,
    WESTERN,
)

# How far over the stated budget still counts as fitting. Nobody who says "about
# ninety minutes" means they will walk out at ninety-one, and a film rejected
# for being four minutes too long is a worse answer than no film at all.
RUNTIME_GRACE = 0.10

# What a title whose runtime nobody knows scores against the budget. Below a
# title we measured and found to be a good fit, above one we measured and found
# to be a poor one: missing data should not beat evidence, and it should not be
# punished as though it were evidence against.
UNKNOWN_RUNTIME_FIT = 0.5

# What every runtime scores when no budget was given. A constant, because "no
# opinion" and "everything is perfect" rank candidates identically and only one
# of the two is honest to write down.
UNBOUNDED_RUNTIME_FIT = 1.0


class Mood(StrEnum):
    """What somebody is in the mood for, in the words they would use."""

    # The default, and not a mood at all: an explicit refusal to steer, leaving
    # taste and quality to decide. Named rather than modelled as "no mood" so
    # the picker has a button for it and the API has no null to interpret.
    SURPRISE_ME = "surprise_me"
    LAUGH = "laugh"
    THRILL = "thrill"
    THINK = "think"
    COMFORT = "comfort"
    MOVED = "moved"
    ESCAPE = "escape"


# Which genres serve which feeling, and how well. Read-only, because a caller
# that could edit this would be editing it for the whole process.
#
# Every mood has at least one genre at 1.0, so that no feeling is quietly harder
# to satisfy than the others. Weights below 1.0 mean "this will do": an action
# film is a decent thriller substitute, a romance is a plausible comedy, and
# saying so is what keeps a small library from returning nothing.
MOOD_GENRE_WEIGHTS: Mapping[Mood, Mapping[str, float]] = MappingProxyType(
    {
        # No entries on purpose. Not a weak opinion -- none at all.
        Mood.SURPRISE_ME: MappingProxyType({}),
        Mood.LAUGH: MappingProxyType({COMEDY: 1.0, ANIMATION: 0.4, FAMILY: 0.35, ROMANCE: 0.25}),
        Mood.THRILL: MappingProxyType({THRILLER: 1.0, HORROR: 0.85, CRIME: 0.8, ACTION: 0.6}),
        Mood.THINK: MappingProxyType(
            {DOCUMENTARY: 1.0, HISTORY: 0.7, DRAMA: 0.55, THRILLER: 0.45, SCIENCE_FICTION: 0.4}
        ),
        Mood.COMFORT: MappingProxyType(
            {FAMILY: 1.0, ANIMATION: 0.9, COMEDY: 0.85, ROMANCE: 0.7, MUSIC: 0.5}
        ),
        Mood.MOVED: MappingProxyType(
            {DRAMA: 1.0, ROMANCE: 0.8, WAR: 0.7, HISTORY: 0.5, MUSIC: 0.4}
        ),
        Mood.ESCAPE: MappingProxyType(
            {FANTASY: 1.0, SCIENCE_FICTION: 1.0, ACTION: 0.8, ANIMATION: 0.5, WESTERN: 0.4}
        ),
    }
)

# How each mood is spoken about in a reason. Written as a fragment that follows
# "you asked for", so the scorer can compose a sentence without a table of its
# own.
MOOD_LABELS: Mapping[Mood, str] = MappingProxyType(
    {
        Mood.SURPRISE_ME: "something unexpected",
        Mood.LAUGH: "a laugh",
        Mood.THRILL: "a thrill",
        Mood.THINK: "something to think about",
        Mood.COMFORT: "something comforting",
        Mood.MOVED: "something moving",
        Mood.ESCAPE: "somewhere else entirely",
    }
)


@dataclass(frozen=True)
class RuntimeWindow:
    """How long somebody has, and the longest thing that still counts as fitting.

    Both fields are None when no budget was given, which means every runtime
    fits and none of them is preferred.
    """

    minutes_available: int | None = None
    max_minutes: int | None = None

    @property
    def unbounded(self) -> bool:
        return self.minutes_available is None


def mood_label(mood: Mood) -> str:
    """How to refer to a mood in a sentence somebody reads."""
    return MOOD_LABELS.get(mood, mood.value)


def mood_fit(mood: Mood, genres: Sequence[str]) -> float:
    """How well a title serves the mood, from 0 to 1.

    The *best* matching genre wins, rather than the average -- deliberately
    unlike :meth:`TasteProfile.affinity`, which averages, and worth being clear
    about because the inconsistency is the point. Taste describes a whole habit,
    so every genre a title carries is part of what somebody is being offered. A
    mood is a request for one quality, and a war comedy still makes you laugh.

    An unrecognised genre code scores nothing rather than raising. JustWatch can
    add a genre whenever it likes, and a recommendation request is not the place
    to discover that.
    """
    weights = MOOD_GENRE_WEIGHTS.get(mood, {})
    return max((weights.get(genre, 0.0) for genre in genres), default=0.0)


def runtime_window(minutes_available: int | None) -> RuntimeWindow:
    """Turn "I have an hour" into something a filter can use.

    A budget of zero or less is treated as no budget rather than as no time.
    It is not a request anybody means, and the alternative is a division by zero
    somewhere further down where the cause would be much harder to see.
    """
    if minutes_available is None or minutes_available <= 0:
        return RuntimeWindow()
    return RuntimeWindow(
        minutes_available=minutes_available,
        max_minutes=round(minutes_available * (1 + RUNTIME_GRACE)),
    )


def fits(runtime_minutes: int | None, window: RuntimeWindow) -> bool:
    """Whether this is short enough to watch tonight. A hard gate.

    An unknown runtime fits. JustWatch frequently has no runtime for a show, and
    excluding those would quietly remove most series from consideration for
    ever -- which is not a decision anybody made deliberately.
    """
    if window.max_minutes is None or runtime_minutes is None:
        return True
    return runtime_minutes <= window.max_minutes


def runtime_fit(runtime_minutes: int | None, window: RuntimeWindow) -> float:
    """How well a runtime uses the time available, from 0 to 1.

    Filling the window scores best and being far short of it scores poorly. That
    is a preference rather than a rule: twenty minutes is a perfectly good thing
    to watch, but it is not what somebody with two free hours asked for.

    Anything past the grace scores nothing, matching :func:`fits`, so a title
    that could not be watched tonight can never be talked up by this component.
    """
    if window.unbounded or window.minutes_available is None:
        return UNBOUNDED_RUNTIME_FIT
    if runtime_minutes is None:
        return UNKNOWN_RUNTIME_FIT
    if not fits(runtime_minutes, window):
        return 0.0
    # Capped at the stated budget rather than at the grace, so that using the
    # grace is scored as a full fit instead of as a slightly worse one -- the
    # grace exists to permit those, not to grudge them.
    return min(runtime_minutes, window.minutes_available) / window.minutes_available

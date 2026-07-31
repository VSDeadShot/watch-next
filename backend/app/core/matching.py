"""Choose a catalogue entry for a parsed title, or decline to.

Netflix gives a title string; JustWatch has a catalogue. Connecting them is
fuzzy by nature -- spellings differ, regions add qualifiers, and two unrelated
films share a name. The part that matters is that this is allowed to answer "I
don't know".

Searching "Dune" returns the 1984 film and the 2021 film. They are equally good
matches for the string, and nothing in a Netflix history row distinguishes them.
A matcher that returns its best guess attaches one of them, and from then on
every recommendation, every statistic and every availability check is quietly
built on a coin flip that nobody can see was tossed. So a match is accepted only
when the best candidate is *both* good enough on its own *and* clearly better
than the runner-up. Otherwise the row comes back unresolved, carrying the
candidates it was choosing between, and a person picks.

This module is pure: no I/O, no network, no database.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from rapidfuzz import fuzz

from app.core.normalize import normalize_title
from app.core.title_parser import TitleKind

# --- Acceptance gates -------------------------------------------------------
# Three of them, and a candidate has to clear all three.

# How alike the two titles must be *on their own*, before anything else is taken
# into account. Separate from the gate below on purpose: kind and year are
# corroborating evidence, and evidence that corroborates must not be able to
# carry a title that is not similar enough to begin with. "Alone" against "Alone
# Together" scores 0.76 here, and a +0.05 bonus for being the right kind would
# otherwise be enough to push it through.
MINIMUM_SIMILARITY = 0.80

# How strong the case must be once kind and year are weighed in. A penalty *can*
# sink an otherwise similar title, because a penalty is evidence against.
MINIMUM_CONFIDENCE = 0.80

# How far clear of the runner-up it must be. Without this, two identical
# candidates both clear the bars above and the first one silently wins.
MINIMUM_MARGIN = 0.05

# --- Score adjustments ------------------------------------------------------
# Small next to the title similarity, because they are corroborating evidence
# rather than evidence of their own. Only the kind penalty is large: a proven
# episode row is genuinely not a film, so that is close to a veto.

KIND_BONUS = 0.05
KIND_PENALTY = 0.25
YEAR_BONUS = 0.05
YEAR_PENALTY = 0.10

# Applied to the similarity itself when the two titles carry different numbers.
# Large enough to always sink a match, because a differing number is never a
# near miss. See _number_tokens.
NUMBER_MISMATCH_PENALTY = 0.30

# Release years differ by country and by a catalogue's idea of "released", so a
# year this close is treated as agreement rather than as a contradiction.
YEAR_TOLERANCE = 1

# What JustWatch calls the two kinds of thing we can match against.
_OBJECT_TYPE_FOR_KIND = {TitleKind.MOVIE: "MOVIE", TitleKind.EPISODE: "SHOW"}


class MatchMethod(StrEnum):
    """How a title came to be linked to a catalogue entry.

    Stored alongside the link, so a resolution made by a person is never
    silently overwritten by a later automatic pass.
    """

    EXACT = "exact"
    FUZZY = "fuzzy"
    MANUAL = "manual"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Candidate:
    """A catalogue search result, reduced to what choosing between them needs."""

    node_id: str
    title: str
    object_type: str
    release_year: int | None = None


@dataclass(frozen=True)
class MatchQuery:
    """What we know about the row we are trying to place.

    ``ambiguous`` means the parser inferred the kind from the shape of the
    string rather than proving it from a marker, so the kind is a hint here
    rather than a constraint.
    """

    title: str
    kind: TitleKind
    year: int | None = None
    ambiguous: bool = False


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Candidate
    # The overall case, used for ranking and for the margin rule.
    score: float
    # The title comparison alone, kept separate because it has its own gate.
    similarity: float


@dataclass(frozen=True)
class MatchResult:
    method: MatchMethod
    chosen: Candidate | None = None
    confidence: float = 0.0
    # Every candidate considered, best first -- kept even when nothing was
    # chosen, because that list is what a person picks from.
    ranked: tuple[ScoredCandidate, ...] = ()
    reason: str = ""


def match_title(query: MatchQuery, candidates: Sequence[Candidate]) -> MatchResult:
    """Pick the candidate that is clearly right, or return an unresolved match."""
    if not candidates:
        return MatchResult(
            method=MatchMethod.UNRESOLVED,
            reason=f"nothing in the catalogue matched {query.title!r}.",
        )

    ranked = tuple(
        sorted(
            (_score(query, candidate) for candidate in candidates),
            key=lambda scored: scored.score,
            reverse=True,
        )
    )
    best = ranked[0]
    confidence = _as_probability(best.score)

    if best.similarity < MINIMUM_SIMILARITY or best.score < MINIMUM_CONFIDENCE:
        return MatchResult(
            method=MatchMethod.UNRESOLVED,
            confidence=confidence,
            ranked=ranked,
            reason=(
                f"the closest match for {query.title!r} was "
                f"{best.candidate.title!r}, which is not close enough to accept."
            ),
        )

    if len(ranked) > 1 and best.score - ranked[1].score < MINIMUM_MARGIN:
        return MatchResult(
            method=MatchMethod.UNRESOLVED,
            confidence=confidence,
            ranked=ranked,
            reason=(
                f"{best.candidate.title!r} and {ranked[1].candidate.title!r} both "
                f"match {query.title!r} and there is nothing to tell them apart."
            ),
        )

    return MatchResult(
        method=_method_for(query, best.candidate),
        chosen=best.candidate,
        confidence=confidence,
        ranked=ranked,
    )


def _score(query: MatchQuery, candidate: Candidate) -> ScoredCandidate:
    """Score one candidate. Deliberately not clamped to 1.

    Clamping here would flatten two strong candidates to the same value and
    destroy the margin that decides whether to accept either of them.
    """
    similarity = _similarity(query.title, candidate.title)
    score = (
        similarity
        + _kind_adjustment(query, candidate)
        + _year_adjustment(query.year, candidate.release_year)
    )
    return ScoredCandidate(candidate=candidate, score=score, similarity=similarity)


def _similarity(left: str, right: str) -> float:
    """How alike two titles are.

    Two measures, averaged, because either alone gets a whole class wrong.

    ``token_set_ratio`` ignores word order and extra words, which is what lets
    "The Office (U.S.)" match "The Office". Alone it is dangerous: a subset
    scores a perfect 100, so "Alone" matches "Alone Together" and "Dune" matches
    "Dune: Part Two" with full confidence. When such a candidate is the only
    result there is no runner-up for the margin rule to catch, and the wrong
    title is attached silently.

    ``token_sort_ratio`` compares the whole strings, so it notices that one is
    much longer than the other. Alone it would reject genuine alternate titles.

    Averaging keeps real matches above the bar and pushes "this might be a
    different film in the same series" below it, where a person decides.
    """
    normalized_left, normalized_right = normalize_title(left), normalize_title(right)
    covers = fuzz.token_set_ratio(normalized_left, normalized_right)
    whole = fuzz.token_sort_ratio(normalized_left, normalized_right)
    similarity = (covers + whole) / 200

    if _number_tokens(normalized_left) != _number_tokens(normalized_right):
        similarity -= NUMBER_MISMATCH_PENALTY

    return similarity


def _number_tokens(normalized: str) -> frozenset[str]:
    """The numbers a title contains, which are never a spelling difference.

    Character similarity reads "Toy Story 3" against "Toy Story 2" as a
    one-character typo and scores it 0.91. In a franchise the number is the only
    thing telling the entries apart, so a title with different numbers -- or with
    a number the other lacks, as in "Blade Runner 2049" against "Blade Runner" --
    is a different title, however alike the letters are.

    Roman numerals are not digits and are left to the title comparison.
    """
    return frozenset(token for token in normalized.split() if token.isdigit())


def _kind_adjustment(query: MatchQuery, candidate: Candidate) -> float:
    if candidate.object_type == _OBJECT_TYPE_FOR_KIND.get(query.kind):
        return KIND_BONUS
    # The parser guessed rather than proved, so the catalogue is the better
    # authority and disagreeing with our guess costs nothing.
    if query.ambiguous:
        return 0.0
    return -KIND_PENALTY


def _year_adjustment(wanted: int | None, released: int | None) -> float:
    if wanted is None or released is None:
        return 0.0
    if abs(wanted - released) <= YEAR_TOLERANCE:
        return YEAR_BONUS
    return -YEAR_PENALTY


def _method_for(query: MatchQuery, candidate: Candidate) -> MatchMethod:
    same = normalize_title(query.title) == normalize_title(candidate.title)
    return MatchMethod.EXACT if same else MatchMethod.FUZZY


def _as_probability(score: float) -> float:
    return min(1.0, max(0.0, score))

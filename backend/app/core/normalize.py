"""Reduce a title to a form two spellings of the same thing can agree on.

Netflix and JustWatch write the same title differently often enough that exact
comparison is useless: "The Office (U.S.)" against "The Office", "Amélie"
against "Amelie", "Love, Death & Robots" against "Love, Death and Robots". This
folds away the differences that never distinguish two titles, and keeps the ones
that do -- "Dune" and "Dune: Part Two" must not collapse together.

The output is not meant to be read by a human. It is a comparison key and a
cache key, so all it has to be is consistent.

This module is pure: no I/O, no network, no database.
"""

import re
import unicodedata

# Only ever stripped from the front. An article inside a title carries meaning
# ("Pirates of the Caribbean").
_LEADING_ARTICLES = ("the", "a", "an")

# A trailing "(U.S.)", "(UK)" or "(2005)" is a disambiguator one catalogue adds
# and the other does not. Anchored to the end so "(500) Days of Summer" keeps
# its number, which is part of the title rather than a note about it, and
# repeated so "The Office (U.S.) (2005)" loses both rather than only the last.
_TRAILING_QUALIFIER = re.compile(r"(?:\s*\([^()]*\))+\s*$")

# Written either way about equally often, and it never distinguishes two titles.
_AMPERSAND = re.compile(r"\s*&\s*")

# Characters that join words rather than separating them, so they become spaces
# ("Spider-Man" -> "spider man"). Everything else simply vanishes
# ("Ocean's" -> "oceans").
#
# The range covers every Unicode dash, not just the ASCII hyphen: catalogues
# disagree about which one they use, and an unspaced en dash would otherwise be
# deleted rather than spaced, gluing both halves of "Spider-Man" into
# "spiderman".
#
# The lint suppression is deliberate: this character class exists precisely to
# name the dashes that look alike, so being told they look alike is not useful.
_WORD_JOINERS = re.compile(r"[-‐-―−/_·]")  # noqa: RUF001

_NON_ALPHANUMERIC = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalize_title(raw: str) -> str:
    """Return the comparison key for a title.

    Never returns an empty string: a title that is nothing but punctuation would
    otherwise key to "" and collide with every other unmatchable title in the
    cache.
    """
    folded = _fold_accents(raw).strip().lower()

    without_qualifier = _TRAILING_QUALIFIER.sub("", folded)
    expanded = _AMPERSAND.sub(" and ", without_qualifier)
    spaced = _WORD_JOINERS.sub(" ", expanded)
    stripped = _NON_ALPHANUMERIC.sub("", spaced)
    collapsed = _WHITESPACE.sub(" ", stripped).strip()

    if not collapsed:
        # Nothing survived -- the title was punctuation, or entirely a trailing
        # qualifier. Fall back to the raw text so the key stays distinct from
        # every other unmatchable title.
        return raw.strip().lower()

    return _strip_leading_article(collapsed)


def _fold_accents(value: str) -> str:
    """Drop diacritics, leaving scripts that do not use them untouched.

    Decomposing and removing combining marks turns "Amélie" into "Amelie"
    without touching Japanese or Korean, which have no combining marks to remove.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _strip_leading_article(value: str) -> str:
    head, _, tail = value.partition(" ")
    # Only when something is left behind: "The" alone is the whole title.
    if head in _LEADING_ARTICLES and tail:
        return tail
    return value

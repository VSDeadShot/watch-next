"""JustWatch's genre codes, and what they mean in English.

JustWatch does not return genres as words. It returns short codes -- ``cmy``,
``trl``, ``scf`` -- and every genre-shaped decision in this app is therefore made
against codes rather than against names. Two things follow, and both are the
reason this table exists.

First, the mood table has to be written in the same codes, or a mood will
silently match nothing. Second, nothing a person reads can be written in them:
"you watch a lot of cmy" is not a sentence, so the reasons attached to a
recommendation need a way back to English.

The table is deliberately not treated as exhaustive. JustWatch can add a genre
whenever it likes, and a code we have never seen is still real signal about what
someone watches -- so an unknown code is kept and shown as itself rather than
dropped.

This module is pure: no I/O, no network, no database.
"""

from collections.abc import Mapping
from types import MappingProxyType

# Named constants rather than bare strings at the call sites. A typo in a code
# is invisible -- it simply never matches an offer or a title -- so the mood
# table names these instead of spelling them out.
ACTION = "act"
ANIMATION = "ani"
COMEDY = "cmy"
CRIME = "crm"
DOCUMENTARY = "doc"
DRAMA = "drm"
EUROPEAN = "eur"
FAMILY = "fml"
FANTASY = "fnt"
HISTORY = "hst"
HORROR = "hrr"
MUSIC = "msc"
REALITY = "rly"
ROMANCE = "rma"
SCIENCE_FICTION = "scf"
SPORT = "spt"
THRILLER = "trl"
WAR = "war"
WESTERN = "wsn"

# Read-only so that a caller cannot edit the table for everybody else. It is
# module-level reference data, and reference data that can be mutated in place
# is reference data that will be, once, in a way nobody finds for a month.
GENRE_NAMES: Mapping[str, str] = MappingProxyType(
    {
        ACTION: "Action & Adventure",
        ANIMATION: "Animation",
        COMEDY: "Comedy",
        CRIME: "Crime",
        DOCUMENTARY: "Documentary",
        DRAMA: "Drama",
        EUROPEAN: "Made in Europe",
        FAMILY: "Kids & Family",
        FANTASY: "Fantasy",
        HISTORY: "History",
        HORROR: "Horror",
        MUSIC: "Music & Musical",
        REALITY: "Reality TV",
        ROMANCE: "Romance",
        SCIENCE_FICTION: "Science-Fiction",
        SPORT: "Sport",
        THRILLER: "Mystery & Thriller",
        WAR: "War & Military",
        WESTERN: "Western",
    }
)


def genre_name(code: str) -> str:
    """The English name for a genre code, or the code itself if it is new.

    Falling back to the code rather than to "Unknown" on purpose. A reason that
    reads "you watch a lot of xyz" is odd but truthful and traceable; one that
    reads "you watch a lot of Unknown" hides which genre it meant, which is the
    one piece of information that would let somebody fix this table.
    """
    return GENRE_NAMES.get(code, code)

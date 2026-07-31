"""A live check that JustWatch still behaves the way the client assumes.

Run by hand, never by pytest. Everything in ``tests/`` is deliberately offline,
which is what makes the suite fast and deterministic -- and also what means it
cannot notice the one failure mode this project is most exposed to: JustWatch
has no public API, and the GraphQL endpoint behind ``simplejustwatchapi`` can
change or disappear without notice. A green test suite proves the client handles
the shapes we expect. Only this proves those are still the shapes we get.

    python scripts/smoke_justwatch.py
    python scripts/smoke_justwatch.py --country US "Some Title"

Exits non-zero if anything it needs is missing, so it can be wired into a check
later if that is ever wanted.
"""

import argparse
import time

from simplejustwatchapi.exceptions import JustWatchError

from app.config import get_settings
from app.services.justwatch_client import JustWatchClient

# One that should be unambiguous, one that is famously not: two films called
# Dune is the case the matcher's margin rule exists for, and a search that stops
# returning both would silently turn a refusal into a wrong answer.
DEFAULT_TITLES = ("Inception", "Dune")


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("titles", nargs="*", default=list(DEFAULT_TITLES))
    parser.add_argument("--country", default=settings.jw_country)
    parser.add_argument("--language", default=settings.jw_language)
    args = parser.parse_args()

    client = JustWatchClient(country=args.country, language=args.language)
    titles = args.titles or list(DEFAULT_TITLES)
    print(f"country={args.country} language={args.language}")

    first_node: str | None = None
    for title in titles:
        started = time.monotonic()
        try:
            entries = client.search(title)
        except JustWatchError as error:
            print(f"\n{title!r}: FAILED -- {type(error).__name__}: {error}")
            return 1

        print(f"\n{title!r}: {len(entries)} results in {time.monotonic() - started:.1f}s")
        if not entries:
            # Not an exception, and not nothing: an endpoint that answers with
            # an empty list for "Inception" is broken in a way that would look
            # like a library of unresolvable titles rather than like an outage.
            print("  (nothing came back -- worth investigating, this should match)")
        for entry in entries[:3]:
            print(
                f"  {entry.node_id:>12}  {entry.title} ({entry.release_year})"
                f"  {entry.object_type.lower()}  {entry.runtime_minutes}min"
                f"  genres={list(entry.genres)}  imdb={entry.imdb_score}"
            )
        first_node = first_node or (entries[0].node_id if entries else None)

    if first_node is None:
        print("\nno node id to look up -- search returned nothing at all")
        return 1

    print(f"\nlooking up {first_node!r} by id, the way a manual fix does")
    try:
        entry = client.details(first_node)
    except JustWatchError as error:
        print(f"  FAILED -- {type(error).__name__}: {error}")
        return 1

    print(f"  {entry.title} ({entry.release_year})  runtime={entry.runtime_minutes}")
    # The fields a manual fix exists to fetch. A lookup that returns the title
    # but none of these still resolves, and quietly leaves the recommender with
    # nothing to score.
    missing = [
        name for name in ("runtime_minutes", "genres", "object_type") if not getattr(entry, name)
    ]
    if missing:
        print(f"  WARNING: no {', '.join(missing)} -- the recommender needs these")

    print("\nok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

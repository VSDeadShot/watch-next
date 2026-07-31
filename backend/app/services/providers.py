"""The list of streaming services, and which of them the user actually has.

Two different kinds of data live here and the distinction is the whole design.

The **catalogue** is JustWatch's: which services exist in a country, what they
are called, what they do. It is refreshed periodically and is entirely
disposable -- losing it costs one request to rebuild.

The **subscriptions** are the user's: typed in by hand, and the only input the
availability filter has. Losing them is not recoverable by asking anyone, and it
silently breaks every recommendation, because a user with no subscriptions has
nothing available. So a refresh of the first is never allowed to touch the
second: ``user_providers`` deliberately holds a short name rather than a foreign
key into ``providers``, and nothing here deletes across that line.

This module is impure: it owns the session and the catalogue client.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import DEFAULT_USER_ID, Provider, UserProvider
from app.services.justwatch_client import CatalogueProviders

_log = logging.getLogger(__name__)


class UnknownProvider(ValueError):
    """A subscription was claimed to a service the catalogue does not list."""


@dataclass(frozen=True)
class ProviderRefresh:
    """What one catalogue refresh did."""

    fetched: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0


def refresh_providers(
    session: Session,
    catalogue: CatalogueProviders,
    *,
    now: datetime | None = None,
) -> ProviderRefresh:
    """Bring the stored catalogue for one country in line with JustWatch.

    The country is read off the client rather than passed in, for the same
    reason the offer cache does it: the list describes whichever country the
    request was actually made for, and a second source for that answer is a
    second chance to disagree with it.
    """
    when = now or datetime.now(UTC)
    country = catalogue.country
    listed = catalogue.providers()

    if not listed:
        # A country with no streaming services does not exist, so an empty
        # answer is a bad answer rather than news. Acting on it would empty the
        # settings picker, and an empty picker means no subscriptions can be
        # chosen -- which makes every title in the library unavailable.
        _log.warning("JustWatch listed no providers for %r; keeping the stored catalogue", country)
        return ProviderRefresh()

    existing = {
        row.short_name: row
        for row in session.scalars(select(Provider).where(Provider.country == country))
    }

    seen: set[str] = set()
    added = 0
    updated = 0
    for entry in listed:
        # JustWatch is under no obligation to deduplicate for us, and the unique
        # key would reject the second row and take the whole refresh with it.
        if entry.short_name in seen:
            continue
        seen.add(entry.short_name)

        row = existing.get(entry.short_name)
        if row is None:
            row = Provider(country=country, short_name=entry.short_name)
            session.add(row)
            added += 1
        else:
            updated += 1

        row.technical_name = entry.technical_name
        row.name = entry.name
        row.icon_url = entry.icon_url
        row.monetization_types = list(entry.monetization_types)
        row.fetched_at = when

    # Services that have left the listing go. Offering one in the picker would
    # let somebody subscribe to something no offer can ever name. Their
    # subscriptions are a separate table and are deliberately left standing.
    gone = [row for short_name, row in existing.items() if short_name not in seen]
    for row in gone:
        session.delete(row)

    session.commit()
    return ProviderRefresh(fetched=len(seen), added=added, updated=updated, removed=len(gone))


def available_providers(session: Session, *, country: str) -> list[Provider]:
    """Everything the settings picker can offer, in the order it should show.

    Sorted in Python rather than in SQL on purpose: text collation differs
    between SQLite and Postgres, so an ``ORDER BY name`` would put a
    lowercase-named service somewhere different in development than in
    production, and a picker whose order moves is a picker people misread.
    """
    rows = session.scalars(select(Provider).where(Provider.country == country)).all()
    return sorted(rows, key=lambda row: (row.name.casefold(), row.short_name))


def subscriptions(session: Session, *, country: str, user_id: str = DEFAULT_USER_ID) -> list[str]:
    """The short names of the services the user has, sorted.

    Short names rather than rows: this is what an offer names its provider, so
    it is the form the availability rule compares against, and handing back
    anything richer would invite a caller to join on something else by mistake.
    """
    rows = session.scalars(
        select(UserProvider.short_name).where(
            UserProvider.user_id == user_id,
            UserProvider.country == country,
        )
    ).all()
    return sorted(rows)


def set_subscriptions(
    session: Session,
    short_names: list[str],
    *,
    country: str,
    user_id: str = DEFAULT_USER_ID,
) -> list[str]:
    """Replace what the user has in one country. Empty is a valid answer.

    Every name is checked first, and one bad name refuses the whole request. A
    subscription to a service that does not exist is invisible rather than noisy
    -- it matches no offer, so the only symptom is never being recommended
    anything on a service you believe you told us about. That is worth an error
    while somebody is still looking at the screen.

    Raises:
        UnknownProvider: if any name is neither in the catalogue for this
            country nor already stored. Nothing is written in that case.
    """
    # Deduplicated because the unique key would otherwise reject a repeat and
    # take the request with it. Order is kept so the error names the offending
    # entries as they were sent.
    wanted = list(dict.fromkeys(short_names))

    listed = set(
        session.scalars(select(Provider.short_name).where(Provider.country == country)).all()
    )
    # What they already have counts as valid even if the catalogue has stopped
    # listing it. A refresh deliberately leaves subscriptions standing, and
    # validating against the catalogue alone would undo that at the next save:
    # a client that reads its current settings and sends them back would be
    # refused outright, naming a service the picker can no longer even show.
    # Keeping something we previously accepted is not the mistake this guard is
    # for -- typos are.
    allowed = listed | set(subscriptions(session, country=country, user_id=user_id))
    unknown = [name for name in wanted if name not in allowed]
    if unknown:
        # Raised before anything is deleted, so a refusal leaves the previous
        # settings exactly as they were. Half-applied settings are worse than
        # rejected ones, because nobody is told which half survived.
        raise UnknownProvider(
            f"JustWatch does not list {', '.join(repr(name) for name in unknown)} in {country!r}"
        )

    session.execute(
        delete(UserProvider).where(
            UserProvider.user_id == user_id,
            UserProvider.country == country,
        )
    )
    # Flush the deletes before the inserts, or the unique key sees the old rows
    # and the new ones at once.
    session.flush()

    for short_name in wanted:
        session.add(UserProvider(user_id=user_id, country=country, short_name=short_name))

    session.commit()
    return sorted(wanted)

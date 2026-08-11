"""The shared secret in front of the API.

This app has one user and no login, which was the right shape for something
that only ever answered on localhost. Deployed, that same shape means anybody
holding the URL can read a viewing history, rewrite which services it believes
you pay for, and spend a rate-limited budget against somebody else's API.

CORS does not help with any of that. `FRONTEND_ORIGIN` tells a *browser* which
page may read a response; it is not access control, and curl has never asked
permission. So the gate is here, in front of the routes.

The secret is held by the Next.js proxy and never by the browser. That is the
whole reason the proxy exists: this is a client-rendered app talking to the API
from the page, so anything the frontend knows ships in the bundle and is public
by construction.

**Off unless configured, but only where that is plausible.** With no secret set
the gate stands aside, so a fresh checkout runs exactly as it did before this
file existed and nobody has to hold a credential to work on the recommender.

That default used to hold everywhere, which made it the failure it was meant to
prevent: a deployment whose environment variable was misspelled or dropped
became a public copy of somebody's viewing history, and looked exactly like a
working one from the outside -- every route answering 200, the health check
green, nothing said anywhere. :func:`gate_status` is the boot-time check that
ends that, and ``app/main.py`` refuses to start on its verdict.
"""

import secrets
from enum import Enum
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.api.deps import SettingsDep
from app.config import is_sqlite_url

#: Named here rather than spelled at each call site, because the proxy has to
#: send exactly this and a typo is a 401 nobody can see the cause of.
API_KEY_HEADER = "X-API-Key"


class GateStatus(Enum):
    """What the configuration adds up to, decided once at startup."""

    #: A secret is set. The ordinary deployed case, and nothing to say about it.
    ENFORCED = "enforced"
    #: No secret, and a SQLite database. A laptop. Stands aside in silence.
    WAIVED_LOCAL = "waived_local"
    #: No secret, and somebody said so on purpose. Stands aside, but says so.
    WAIVED_EXPLICIT = "waived_explicit"
    #: No secret, no waiver, and no evidence this is a laptop. Refuse to start.
    MISCONFIGURED = "misconfigured"


def gate_status(*, api_secret: str, database_url: str, allow_unauthenticated: bool) -> GateStatus:
    """Decide whether an absent secret is a preference or an accident.

    Pure, and takes the three values rather than ``Settings``, so the whole rule
    can be argued with in a test that boots nothing.

    There is no ``APP_ENV`` in this app, and every way of inventing one is wrong
    in some direction -- so the question is only which direction. A platform
    variable like ``RENDER`` is right on Render and silently useless anywhere
    else. A new setting defaulting to "local" recreates the original bug the
    first time somebody forgets it. Both of those fail *open*, and failing open
    is the thing being fixed.

    So the database decides, which is not a guess about intent but a fact about
    this app: it cannot usefully be deployed on SQLite. Render's disk is
    ephemeral, so the file does not survive a redeploy, and the README and
    CLAUDE.md both name Postgres as the deployed configuration. The inference
    therefore rides on a constraint that is already true for other reasons.

    Read the wrong way round it refuses to start a laptop running Postgres,
    which is fail-closed and is one environment variable to undo. That is the
    trade, and it is deliberately not symmetric.

    Not covered, and worth being plain about: SQLite served from a public host
    lands in :attr:`GateStatus.WAIVED_LOCAL` and is neither gated nor warned
    about. Closing that would mean warning on every local run, which is how a
    warning stops being read.
    """
    if _configured(api_secret):
        # Both set is not a contradiction to refuse over. The gate works; the
        # waiver simply has nothing left to waive.
        return GateStatus.ENFORCED
    if allow_unauthenticated:
        return GateStatus.WAIVED_EXPLICIT
    if is_sqlite_url(database_url):
        return GateStatus.WAIVED_LOCAL
    return GateStatus.MISCONFIGURED


def _configured(api_secret: str) -> bool:
    """Whether a secret was actually supplied.

    Blank *and* whitespace-only both mean "not set". A variable holding spaces
    is one somebody meant to fill in, and treating it as configured would gate
    the API behind a credential nobody can type and then report success.

    Only the emptiness test strips. The comparison in :func:`require_api_key`
    uses the raw value, because trimming a real secret would quietly change the
    credential rather than merely tidy it.
    """
    return bool(api_secret.strip())


def require_api_key(
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Refuse anything that cannot present the configured secret.

    Compared with :func:`secrets.compare_digest` rather than ``==``: the
    difference is unobservable here and free to have, and the habit is worth
    more than the reasoning about whether this particular comparison is
    remotely timeable.
    """
    if not _configured(settings.api_secret):
        return

    if x_api_key is None or not secrets.compare_digest(x_api_key, settings.api_secret):
        # Names the header, because the reader of this message is whoever is
        # wiring the proxy to the backend, and "401" alone is indistinguishable
        # from the backend being broken.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing or invalid {API_KEY_HEADER} key.",
        )

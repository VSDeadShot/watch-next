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

**Off unless configured.** With no secret set the gate stands aside entirely,
so a fresh checkout runs exactly as it did before this file existed and nobody
has to hold a credential to work on the recommender.
"""

import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.api.deps import SettingsDep

#: Named here rather than spelled at each call site, because the proxy has to
#: send exactly this and a typo is a 401 nobody can see the cause of.
API_KEY_HEADER = "X-API-Key"


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
    if not settings.api_secret:
        return

    if x_api_key is None or not secrets.compare_digest(x_api_key, settings.api_secret):
        # Names the header, because the reader of this message is whoever is
        # wiring the proxy to the backend, and "401" alone is indistinguishable
        # from the backend being broken.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing or invalid {API_KEY_HEADER} key.",
        )

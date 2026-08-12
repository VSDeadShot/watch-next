"""Dependencies shared by the routers.

Kept in one place so that a test overriding a dependency overrides it for every
endpoint, rather than for whichever router happened to declare it.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import DEFAULT_USER_ID
from app.services.justwatch_client import JustWatchClient


@lru_cache
def get_catalogue() -> JustWatchClient:
    """The JustWatch client, built once for the process.

    Cached deliberately rather than for speed. The client's rate limiter is
    instance state -- when the last request went out -- so a fresh client per
    request would start every request believing none had ever been sent, and the
    self-imposed limit would never fire at all.
    """
    settings = get_settings()
    return JustWatchClient(country=settings.jw_country, language=settings.jw_language)


def current_user() -> str:
    """Whose data this request is about.

    There is one user and no login, so this answers with the constant every
    table has carried since the first migration. It exists anyway, and every
    router asks it rather than letting the services fall back to their own
    default.

    The difference is invisible today and total tomorrow. A route that omits
    ``user_id`` is not asking for "whoever is asking" -- it is asking for the
    string ``local``, and those two only agree while there is one account. The
    day there are two, every route that relied on the default serves one
    person's history to another, and nothing fails anywhere: the query is valid,
    the rows are real, and the answer is somebody else's.

    So this is the single place that knows, which makes adding accounts an edit
    here rather than an audit of every route. ``tests/test_api_user_scoping.py``
    reads ``app/api/`` and fails if any call goes without it.

    The services keep their defaults deliberately. Making ``user_id`` required
    would have touched some five hundred call sites, all but a handful of them
    tests and internal calls where the default is what keeps them readable, to
    catch a mistake only possible in the eighteen places the test already
    covers.
    """
    return DEFAULT_USER_ID


UserDep = Annotated[str, Depends(current_user)]

SessionDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CatalogueDep = Annotated[JustWatchClient, Depends(get_catalogue)]

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


SessionDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CatalogueDep = Annotated[JustWatchClient, Depends(get_catalogue)]

"""Runtime settings, read from the environment or a local ``.env``.

See ``.env.example`` for the full list with sensible defaults.
"""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Schemes a hosted Postgres hands out that SQLAlchemy cannot use as given.
#: ``postgres://`` is the spelling SQLAlchemy 2 dropped support for outright,
#: and a bare ``postgresql://`` has no driver in it, which since psycopg2 is no
#: longer the default means no driver at all.
_DRIVERLESS_POSTGRES = ("postgresql://", "postgres://")
_POSTGRES_DRIVER = "postgresql+psycopg://"


def is_sqlite_url(url: str) -> bool:
    """Whether this URL names a local SQLite file.

    One definition with three readers, because they must not be able to
    disagree. ``db.py`` uses it to decide on the cross-thread check, and
    ``api/security.py`` uses it as the only available evidence of whether this
    process is a laptop or a deployment -- and a rule about connection pooling
    silently drifting away from a rule about authentication is exactly the kind
    of divergence nothing would notice.
    """
    return url.startswith("sqlite")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite locally, Postgres in deployment -- SQLAlchemy makes them the same
    # code, so the URL is the only thing that changes.
    database_url: str = "sqlite:///./watch_next.db"

    # JustWatch answers "where can I watch this" differently per country, so
    # availability is meaningless without one.
    jw_country: str = "IN"
    jw_language: str = "en"

    # Below this, a view is an accidental start rather than something watched.
    min_watch_seconds: int = 60

    frontend_origin: str = "http://localhost:3000"

    # The shared secret the Next.js proxy presents, and the browser never sees.
    # Empty means the gate stands aside, which is the local default: a checkout
    # runs with no credential and behaves as it always has. Set it in the
    # deployment and the API stops answering strangers. See app/api/security.py
    # for why CORS is not a substitute for this.
    api_secret: str = ""

    # "Yes, I know this API answers anyone, and I mean it." The escape hatch for
    # the one honest case the rule in api/security.py reads the wrong way round:
    # a Postgres running on the same laptop. Named for the decision rather than
    # for an environment, so that setting it by accident is hard and reading it
    # in a dashboard leaves nobody in any doubt about what it does.
    allow_unauthenticated: bool = False

    @property
    def is_sqlite(self) -> bool:
        return is_sqlite_url(self.database_url)

    @field_validator("database_url")
    @classmethod
    def _name_the_driver(cls, value: str) -> str:
        """Make a provider's connection string usable without editing it.

        Neon, Render and Heroku all hand out a URL with no driver in the scheme,
        and SQLAlchemy rejects it at ``create_engine`` -- which is at import, so
        the failure is a stack trace about dialects rather than anything that
        mentions configuration. Rewriting only the scheme leaves the credentials
        and the query string exactly as they came, which matters because
        ``?sslmode=require`` is not optional on Neon and a password is allowed
        to contain anything.

        A URL that already names a driver is left alone: somebody who asked for
        asyncpg has said so, and is not helped by being overruled.
        """
        for scheme in _DRIVERLESS_POSTGRES:
            if value.startswith(scheme):
                return _POSTGRES_DRIVER + value[len(scheme) :]
        return value


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is read once per process."""
    return Settings()

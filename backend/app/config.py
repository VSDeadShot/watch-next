"""Runtime settings, read from the environment or a local ``.env``.

See ``.env.example`` for the full list with sensible defaults.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is read once per process."""
    return Settings()

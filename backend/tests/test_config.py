"""Tests for the settings, and mostly for the database URL.

Neon -- and every other hosted Postgres -- hands out a URL with no driver in the
scheme, and some of them still hand out the ``postgres://`` spelling that
SQLAlchemy 2 removed support for. Both are rejected at ``create_engine``, which
is to say at import time, which is to say the deployment fails to boot with a
stack trace about dialects rather than anything about configuration.

Normalising here means the URL a provider gives you can be pasted into the
environment unedited. That is worth more than it looks: the alternative is a
hand-edited connection string, and hand-editing is done once, correctly, and
then never again by whoever rotates the password at two in the morning.
"""

import pytest

from app.config import Settings

NEON = "ep-cool-darkness-123456.eu-central-1.aws.neon.tech/watchnext?sslmode=require"


def url_for(value: str) -> str:
    return Settings(database_url=value).database_url


class TestPostgresUrlsGainADriver:
    @pytest.mark.parametrize(
        "given",
        [
            f"postgresql://user:pw@{NEON}",
            # The spelling Heroku popularised and several providers still emit.
            # SQLAlchemy 2 refuses it outright.
            f"postgres://user:pw@{NEON}",
        ],
    )
    def test_a_driverless_url_is_given_psycopg(self, given: str):
        assert url_for(given).startswith("postgresql+psycopg://")

    def test_everything_after_the_scheme_is_left_alone(self):
        # The query string carries `sslmode=require`, which Neon needs and which
        # a normaliser that rebuilt the URL would be able to lose.
        assert url_for(f"postgresql://user:pw@{NEON}").endswith(f"user:pw@{NEON}")

    def test_a_password_containing_slashes_survives(self):
        # Only the scheme is rewritten, so nothing in the credentials can be
        # mistaken for a separator.
        given = "postgresql://user:a//b@host/db"
        assert url_for(given) == "postgresql+psycopg://user:a//b@host/db"


class TestUrlsThatAlreadySayWhatTheyWant:
    @pytest.mark.parametrize(
        "given",
        [
            "postgresql+psycopg://user:pw@host/db",
            # Somebody who has deliberately chosen another driver has said so,
            # and is not helped by being overruled.
            "postgresql+asyncpg://user:pw@host/db",
            "postgresql+psycopg2://user:pw@host/db",
        ],
    )
    def test_an_explicit_driver_is_kept(self, given: str):
        assert url_for(given) == given


class TestSqliteIsUntouched:
    @pytest.mark.parametrize(
        "given",
        ["sqlite:///./watch_next.db", "sqlite://", "sqlite:////tmp/absolute.db"],
    )
    def test_local_urls_pass_through(self, given: str):
        assert url_for(given) == given

    def test_the_default_is_still_sqlite(self):
        """The default is what a fresh checkout runs on, and it must not need a
        Postgres anywhere in sight."""
        assert Settings().database_url.startswith("sqlite:")

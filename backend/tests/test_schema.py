"""Tests for the schema as Postgres will see it.

SQLite ignores ``VARCHAR(n)`` limits entirely; Postgres rejects anything longer.
So a title that imports cleanly in development can abort the whole import in
deployment, and no amount of local testing would show it. Compiling the tables
against the Postgres dialect catches that here, without needing a Postgres
server to run the suite.
"""

import pytest
from sqlalchemy import Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models import ImportRun, WatchEvent


def column_type(table: Table, column: str) -> str:
    return table.c[column].type.compile(dialect=postgresql.dialect())


class TestFreeTextIsUnbounded:
    """Nothing sets a maximum length on a title, a profile name or a filename,
    so neither should we -- an invented limit only shows up as a failed import."""

    @pytest.mark.parametrize(
        "column",
        ["raw_title", "title", "episode_title", "profile_name", "device_type"],
    )
    def test_watch_event_text_columns(self, column: str):
        assert column_type(WatchEvent.__table__, column) == "TEXT"

    def test_the_uploaded_filename(self):
        assert column_type(ImportRun.__table__, "filename") == "TEXT"


class TestBoundedColumnsKeepTheirBounds:
    """Where a real limit exists, it is worth stating -- it documents the value
    and lets the database index it tightly."""

    def test_the_fingerprint_is_exactly_a_sha256_digest(self):
        assert column_type(WatchEvent.__table__, "fingerprint") == "VARCHAR(64)"

    def test_a_country_is_an_iso_code(self):
        assert column_type(WatchEvent.__table__, "country") == "VARCHAR(8)"


class TestSchemaCompilesForPostgres:
    def test_every_table_has_valid_postgres_ddl(self):
        """The deployment target is Postgres; the suite runs on SQLite. This is
        the cheapest thing that keeps them from drifting apart."""
        for table in (ImportRun.__table__, WatchEvent.__table__):
            assert "CREATE TABLE" in str(CreateTable(table).compile(dialect=postgresql.dialect()))

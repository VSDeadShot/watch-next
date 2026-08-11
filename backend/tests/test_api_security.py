"""Tests for the shared-secret gate in front of the API.

This app has one user and no login, which was right for something that only
ever answered on localhost. Deployed, the same design means anyone holding the
URL can read a viewing history, change which services it thinks you pay for and
spend the JustWatch budget -- and CORS stops none of that, being a browser
policy rather than access control.

The gate is therefore a shared secret the Next.js proxy holds and the browser
never sees. It is off unless configured, so running this locally needs no
setup and behaves exactly as it did before.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.security import GateStatus, gate_status
from app.config import Settings, get_settings
from app.db import get_db
from app.main import app

SECRET = "a-secret-the-browser-never-sees"

#: The backend project root, so a subprocess can import ``app`` from anywhere.
BACKEND = Path(__file__).resolve().parents[1]

#: Cleared before booting a subprocess, so that whatever the developer happens
#: to have exported cannot decide the answer the test is checking.
_GATE_VARS = frozenset({"DATABASE_URL", "API_SECRET", "ALLOW_UNAUTHENTICATED"})


def build(session: Session, **settings) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_settings] = lambda: Settings(
        jw_country="IN", jw_language="en", **settings
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def open_client(session: Session) -> Iterator[TestClient]:
    """No secret configured, which is how this runs on a laptop."""
    yield from build(session)


@pytest.fixture
def guarded(session: Session) -> Iterator[TestClient]:
    yield from build(session, api_secret=SECRET)


class TestWithNoSecretConfigured:
    """The local default. Turning the gate on must be a deployment decision, not
    something that breaks every developer's checkout the day it lands."""

    def test_a_request_with_no_key_is_answered(self, open_client: TestClient):
        assert open_client.get("/api/stats").status_code == 200

    def test_a_stray_key_is_ignored_rather_than_refused(self, open_client: TestClient):
        response = open_client.get("/api/stats", headers={"X-API-Key": "anything at all"})
        assert response.status_code == 200


class TestWithASecretConfigured:
    def test_a_request_with_no_key_is_refused(self, guarded: TestClient):
        assert guarded.get("/api/stats").status_code == 401

    def test_a_wrong_key_is_refused(self, guarded: TestClient):
        assert guarded.get("/api/stats", headers={"X-API-Key": "wrong"}).status_code == 401

    def test_the_right_key_is_answered(self, guarded: TestClient):
        response = guarded.get("/api/stats", headers={"X-API-Key": SECRET})
        assert response.status_code == 200

    def test_the_refusal_says_what_is_missing(self, guarded: TestClient):
        # A bare 401 in a proxy's logs is indistinguishable from the backend
        # being broken. This is read by whoever is wiring the two together.
        assert "key" in guarded.get("/api/stats").json()["detail"].lower()

    def test_a_key_that_is_a_prefix_of_the_secret_is_refused(self, guarded: TestClient):
        # Guards the comparison itself: `startswith` or a truncated compare
        # would let this through.
        response = guarded.get("/api/stats", headers={"X-API-Key": SECRET[:-1]})
        assert response.status_code == 401


class TestASecretOfOnlyWhitespace:
    """The boot check and the gate itself have to agree about what "set" means.

    If they disagreed, ``API_SECRET="   "`` would be refused at startup as
    unconfigured and then enforced at request time as a credential -- an API
    gated behind a secret nobody can type, reported as working.
    """

    @pytest.fixture
    def blank(self, session: Session) -> Iterator[TestClient]:
        yield from build(session, api_secret="   ")

    def test_it_is_treated_as_no_secret_at_all(self, blank: TestClient):
        assert blank.get("/api/stats").status_code == 200

    def test_the_whitespace_itself_is_not_accepted_as_a_key(self, blank: TestClient):
        # Not a gate that can be satisfied: it is not a gate.
        assert blank.get("/api/stats", headers={"X-API-Key": "   "}).status_code == 200


class TestASecretIsComparedExactly:
    """Only the emptiness test strips. A real secret keeps whatever it was given,
    because trimming one would change the credential rather than tidy it."""

    @pytest.fixture
    def padded(self, session: Session) -> Iterator[TestClient]:
        yield from build(session, api_secret=f" {SECRET} ")

    def test_the_padded_secret_is_what_opens_it(self, padded: TestClient):
        response = padded.get("/api/stats", headers={"X-API-Key": f" {SECRET} "})
        assert response.status_code == 200

    def test_the_trimmed_secret_does_not(self, padded: TestClient):
        assert padded.get("/api/stats", headers={"X-API-Key": SECRET}).status_code == 401


class TestWhatTheGateDoesNotCover:
    def test_health_answers_without_a_key(self, guarded: TestClient):
        """Render polls this to decide whether the service is up. Behind the
        gate, a healthy deployment would look permanently broken."""
        assert guarded.get("/health").status_code == 200


class TestDecidingWhetherAGateIsRequired:
    """The rule behind the boot check, as a pure function.

    An empty secret used to mean "stand aside" everywhere, which is right on a
    laptop and is how a deployment that lost its environment variable turns into
    a public copy of somebody's viewing history that still answers 200 to every
    health check. Nothing said so, in a log or anywhere else.

    There is no APP_ENV here, and every way of guessing one is wrong in some
    direction -- so what matters is which direction. A platform variable like
    RENDER is right today and silently useless on the next host; a new setting
    defaulting to "local" reproduces the bug the day somebody forgets it. Both
    of those fail *open*, which is the thing being fixed.

    The database is used instead, because it is not a guess about intent: this
    app cannot usefully be deployed on SQLite. Render's disk is ephemeral, so
    the file does not survive a redeploy, and both the README and CLAUDE.md
    already name Postgres as the deployed configuration. Reading it the wrong
    way round -- local Postgres in Docker -- refuses to boot, which is
    fail-closed and is one environment variable to undo.
    """

    def test_a_configured_secret_is_simply_enforced(self):
        assert (
            gate_status(
                api_secret=SECRET,
                database_url="postgresql+psycopg://user:pw@host/db",
                allow_unauthenticated=False,
            )
            is GateStatus.ENFORCED
        )

    def test_sqlite_with_no_secret_is_the_local_default_and_stands_aside(self):
        # The promise this file's docstring makes: a fresh checkout runs with no
        # credential and behaves exactly as it did before the gate existed.
        assert (
            gate_status(
                api_secret="",
                database_url="sqlite:///./watch_next.db",
                allow_unauthenticated=False,
            )
            is GateStatus.WAIVED_LOCAL
        )

    def test_postgres_with_no_secret_is_refused(self):
        # The whole point. A deployment is the one place an absent secret is not
        # a preference.
        assert (
            gate_status(
                api_secret="",
                database_url="postgresql+psycopg://user:pw@host/db",
                allow_unauthenticated=False,
            )
            is GateStatus.MISCONFIGURED
        )

    def test_postgres_with_no_secret_can_be_waived_on_purpose(self):
        # Named for the decision rather than for a proxy of it, so that setting
        # it by accident or misreading it in a dashboard is hard.
        assert (
            gate_status(
                api_secret="",
                database_url="postgresql+psycopg://user:pw@host/db",
                allow_unauthenticated=True,
            )
            is GateStatus.WAIVED_EXPLICIT
        )

    def test_a_secret_outranks_the_waiver(self):
        # Both set is not a contradiction worth refusing over: the secret is
        # present, so the gate works, and the waiver has nothing to waive.
        assert (
            gate_status(
                api_secret=SECRET,
                database_url="postgresql+psycopg://user:pw@host/db",
                allow_unauthenticated=True,
            )
            is GateStatus.ENFORCED
        )

    def test_a_secret_of_only_spaces_does_not_count_as_one(self):
        # An environment variable set to whitespace is a variable somebody meant
        # to fill in. Treating it as configured would gate the API behind a
        # secret nobody can type and call that success.
        assert (
            gate_status(
                api_secret="   ",
                database_url="postgresql+psycopg://user:pw@host/db",
                allow_unauthenticated=False,
            )
            is GateStatus.MISCONFIGURED
        )

    def test_the_waiver_still_speaks_up_on_sqlite(self):
        # Waived either way, so only the warning differs -- and somebody who
        # went to the trouble of setting the variable has asked to be told.
        # Ordering matters here: checking for SQLite first would swallow it.
        assert (
            gate_status(
                api_secret="",
                database_url="sqlite:///./watch_next.db",
                allow_unauthenticated=True,
            )
            is GateStatus.WAIVED_EXPLICIT
        )

    def test_a_host_merely_named_sqlite_is_not_a_local_database(self):
        # `"sqlite" in url` would read this as a laptop and stand aside on a
        # real deployment. The scheme is the claim; the rest of the URL is not.
        assert (
            gate_status(
                api_secret="",
                database_url="postgresql+psycopg://user:pw@sqlite.example.com/db",
                allow_unauthenticated=False,
            )
            is GateStatus.MISCONFIGURED
        )

    @pytest.mark.parametrize(
        "database_url",
        [
            "sqlite:///./watch_next.db",
            "sqlite:///:memory:",
            "sqlite+pysqlite:///./watch_next.db",
        ],
    )
    def test_every_spelling_of_sqlite_reads_as_local(self, database_url: str):
        assert (
            gate_status(api_secret="", database_url=database_url, allow_unauthenticated=False)
            is GateStatus.WAIVED_LOCAL
        )

    @pytest.mark.parametrize(
        "database_url",
        [
            "postgresql+psycopg://user:pw@host/db",
            "postgresql://user:pw@host/db",
            "mysql://user:pw@host/db",
            "",
        ],
    )
    def test_anything_that_is_not_sqlite_is_treated_as_deployed(self, database_url: str):
        # Including the empty string. A URL we cannot read is not evidence of a
        # laptop, and the safe reading of "no idea" is the one that refuses.
        assert (
            gate_status(api_secret="", database_url=database_url, allow_unauthenticated=False)
            is GateStatus.MISCONFIGURED
        )


class TestTheProcessActuallyRefusesToStart:
    """The rule above is only worth having if importing the app obeys it.

    Run as a real subprocess rather than by reloading the module, for two
    reasons. Reloading ``app.main`` rebuilds the ``app`` object every other test
    in the suite has already imported, and the thing being verified is
    specifically what ``uvicorn app.main:app`` does -- which is an import, and
    then an exit code the platform reads. A test that only called
    :func:`gate_status` again would prove the rule and not the refusal.

    The working directory is a temporary one so that a developer's own
    ``backend/.env`` cannot reach in and decide the outcome; the package is
    found on PYTHONPATH instead.
    """

    def _boot(self, tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
        environment = {k: v for k, v in os.environ.items() if k not in _GATE_VARS}
        environment["PYTHONPATH"] = str(BACKEND)
        environment.update(env)
        return subprocess.run(
            [sys.executable, "-c", "import app.main"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=environment,
        )

    def test_postgres_with_no_secret_exits_non_zero(self, tmp_path: Path):
        # Non-zero is the whole mechanism: it is what makes Render report a
        # failed deploy and keep the previous version serving, instead of
        # bringing up an ungated one that passes every health check.
        result = self._boot(tmp_path, DATABASE_URL="postgresql+psycopg://user:pw@host/db")
        assert result.returncode != 0

    def test_the_refusal_names_both_ways_out(self, tmp_path: Path):
        result = self._boot(tmp_path, DATABASE_URL="postgresql+psycopg://user:pw@host/db")
        assert "API_SECRET" in result.stderr
        assert "ALLOW_UNAUTHENTICATED" in result.stderr

    def test_a_secret_lets_it_start(self, tmp_path: Path):
        result = self._boot(
            tmp_path,
            DATABASE_URL="postgresql+psycopg://user:pw@host/db",
            API_SECRET=SECRET,
        )
        assert result.returncode == 0, result.stderr

    def test_the_waiver_lets_it_start(self, tmp_path: Path):
        result = self._boot(
            tmp_path,
            DATABASE_URL="postgresql+psycopg://user:pw@host/db",
            ALLOW_UNAUTHENTICATED="true",
        )
        assert result.returncode == 0, result.stderr

    def test_the_waiver_says_so_out_loud(self, tmp_path: Path):
        # The point of the explicit waiver is that it is visible afterwards. A
        # silent one would be the original bug with an extra step.
        result = self._boot(
            tmp_path,
            DATABASE_URL="postgresql+psycopg://user:pw@host/db",
            ALLOW_UNAUTHENTICATED="true",
        )
        assert "ALLOW_UNAUTHENTICATED" in result.stderr

    def test_sqlite_starts_with_nothing_configured_and_says_nothing(self, tmp_path: Path):
        # The fresh-checkout promise, checked end to end rather than asserted.
        result = self._boot(tmp_path, DATABASE_URL="sqlite:///./probe.db")
        assert result.returncode == 0, result.stderr
        assert result.stderr.strip() == ""


class TestEveryRouterIsBehindIt:
    """A gate applied router by router is a gate somebody can forget to apply to
    the next one. This fails when a router is added without it."""

    def test_no_api_route_answers_without_a_key(self, guarded: TestClient):
        unguarded = []
        for path, operations in app.openapi()["paths"].items():
            if not path.startswith("/api/"):
                continue
            for method in operations:
                # A concrete id, so a path parameter does not 422 before the
                # gate has had a chance to refuse.
                url = path.replace("{resolution_id}", "1").replace("{title_id}", "1")
                response = guarded.request(method.upper(), url)
                if response.status_code != 401:
                    unguarded.append(f"{method.upper()} {path} -> {response.status_code}")
        assert unguarded == []

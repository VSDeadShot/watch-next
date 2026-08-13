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
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.security import GateStatus, gate_status
from app.config import Settings, get_settings
from app.db import get_db
from app.main import app, create_app

SECRET = "a-secret-the-browser-never-sees"

#: Any non-SQLite URL reads as a deployment. Never connected to -- create_engine
#: does not dial out, and these tests only ask what the routing table looks like.
DEPLOYED_URL = "postgresql+psycopg://user:pw@host/db"

#: The one route that must answer a stranger. Render polls it to decide whether
#: the service is up, and a healthy deployment behind a 401 looks permanently
#: broken. Everything else is a bug if it is on this list.
PUBLIC_BY_DESIGN = {"/health"}

#: The page this app is served from, as the backend is told about it.
FRONTEND = "http://localhost:3000"


def routing_table(application: FastAPI) -> list[tuple[str, tuple[str, ...]]]:
    """Every route the app will actually match, as ``(path, methods)``.

    Not ``app.openapi()``. The schema is a *description* of the app, and the
    routes that go missing from a description are precisely the ones nothing is
    describing -- FastAPI's own ``/docs``, ``/redoc``, ``/openapi.json`` and the
    OAuth redirect appear in none of it. Walking the schema to check that
    everything is guarded therefore cannot, even in principle, find the category
    of route that was unguarded.

    Included routers are not flattened into ``app.routes`` in FastAPI 0.140, so
    this recurses through them. That is version-sensitive by nature, which is
    why ``TestTheRouteEnumeratorItself`` exists: an enumerator that quietly
    returned nothing would make every test below pass while checking nothing.
    """
    found: list[tuple[str, tuple[str, ...]]] = []

    def walk(routes: object, prefix: str) -> None:
        for route in routes:  # type: ignore[attr-defined]
            path = getattr(route, "path", None)
            if path is not None:
                methods = tuple(sorted(getattr(route, "methods", ()) or ()))
                found.append((prefix + path, methods))
            elif (inner := getattr(route, "original_router", None)) is not None:
                context = getattr(route, "include_context", None)
                walk(inner.routes, prefix + (getattr(context, "prefix", "") or ""))

    walk(application.routes, "")
    return sorted(set(found))


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


@pytest.fixture
def deployed(session: Session) -> Iterator[TestClient]:
    """An app built the way a deployment builds it, rather than the way the
    suite does: a secret set and a Postgres URL, so the reference docs are gone
    and the gate is live. The default app is the local shape, and asking it
    whether documentation is exposed would be asking about a configuration that
    is never deployed."""
    application = create_app(
        Settings(database_url=DEPLOYED_URL, api_secret=SECRET, jw_country="IN", jw_language="en")
    )
    application.dependency_overrides[get_db] = lambda: session
    application.dependency_overrides[get_settings] = lambda: Settings(
        jw_country="IN", jw_language="en", api_secret=SECRET
    )
    with TestClient(application) as client:
        yield client


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


class TestWhatTheBrowserIsTold:
    """The CORS block, which is the other thing here that is not access control.

    Worth being exact about how little it does. Every request the backend
    actually receives arrives from ``app/api/[...path]/route.ts``, server-side,
    and a server-side fetch sends no ``Origin`` header -- so the middleware
    returns before it adds anything. That is the first test below, and it is the
    one describing the real path.

    The rest describe a path nothing in this app takes: `lib/api.ts` only ever
    fetches its own origin, locally as well as deployed. They are here because
    the headers still get emitted to anyone who asks with an ``Origin``, and
    ``Access-Control-Allow-Credentials`` was among them -- sent to refused
    origins too, since Starlette holds it in the unconditional header set while
    only the origin echo is conditional. Nothing in this app has a credential a
    browser could send: no cookie, no session, no basic auth. The one credential
    is the ``X-API-Key`` the proxy holds on the server, which is not a thing CORS
    has any say over. So the flag described a session model that never existed,
    and these tests are what stops it coming back.
    """

    @pytest.fixture
    def cors_app(self) -> Iterator[TestClient]:
        """An app whose CORS block was built from a known origin.

        Built with :func:`create_app` rather than by overriding
        ``get_settings``, because middleware is configured once when the app is
        constructed and an override arrives per request -- far too late to
        decide what the middleware was told. Overriding it would leave these
        tests answering to whatever ``FRONTEND_ORIGIN`` the developer happens to
        have in ``backend/.env``.

        The settings passed to :func:`create_app` decide the middleware; the
        ones the *routes* see come from ``get_settings`` and are a separate
        thing, so both are set here. Nothing below needs the second -- the two
        routes used are ``/health``, which is public by design, and a preflight,
        which the middleware answers itself before any route is reached. But a
        test added to this class later would otherwise answer to whichever
        ``API_SECRET`` the developer happens to have exported, and pass or fail
        by machine.
        """
        deployed_settings = Settings(
            frontend_origin=FRONTEND,
            database_url=DEPLOYED_URL,
            api_secret=SECRET,
            jw_country="IN",
            jw_language="en",
        )
        application = create_app(deployed_settings)
        application.dependency_overrides[get_settings] = lambda: deployed_settings
        with TestClient(application) as client:
            yield client

    def test_a_server_to_server_call_gets_no_cors_headers_at_all(self, cors_app: TestClient):
        # The real path, and the reason the rest of this is inert. No `Origin`,
        # so Starlette hands the request straight on.
        response = cors_app.get("/health")
        assert [name for name in response.headers if name.startswith("access-control")] == []

    def test_the_page_the_app_is_served_from_may_read_the_answer(self, cors_app: TestClient):
        # Echoed rather than `*`, which is what the middleware is still here for.
        response = cors_app.get("/health", headers={"Origin": FRONTEND})
        assert response.headers["access-control-allow-origin"] == FRONTEND

    def test_any_other_page_may_not(self, cors_app: TestClient):
        response = cors_app.get("/health", headers={"Origin": "https://evil.test"})
        assert "access-control-allow-origin" not in response.headers

    @pytest.mark.parametrize("origin", [FRONTEND, "https://evil.test"])
    def test_no_response_claims_a_credential_can_be_sent(self, cors_app: TestClient, origin: str):
        # Both origins, because this header went out regardless of whether the
        # origin was allowed -- so testing only the allowed one would miss most
        # of the requests that used to receive it.
        response = cors_app.get("/health", headers={"Origin": origin})
        assert "access-control-allow-credentials" not in response.headers

    @pytest.mark.parametrize("origin", [FRONTEND, "https://evil.test"])
    def test_no_preflight_claims_one_either(self, cors_app: TestClient, origin: str):
        # A separate header set in Starlette, built at construction time, so it
        # is a separate assertion rather than the same one twice.
        response = cors_app.options(
            "/api/stats",
            headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-credentials" not in response.headers

    def test_a_preflight_from_that_page_still_succeeds(self, cors_app: TestClient):
        # The check that the removal took a claim away and not the policy. A
        # cross-origin POST is refused before it is sent if this stops being 200.
        response = cors_app.options(
            "/api/stats",
            headers={
                "Origin": FRONTEND,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-api-key",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == FRONTEND


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


class TestTheRouteEnumeratorItself:
    """The test below is only as good as its list of routes, so the list is
    checked first.

    An enumerator that silently returned nothing would make the guard test pass
    perfectly while proving nothing at all -- the worst failure available to a
    security test, because it looks like success. It walks FastAPI internals to
    flatten included routers, so it is exactly the kind of thing a dependency
    bump breaks quietly.
    """

    def test_it_finds_more_than_the_schema_documents(self):
        # The whole point. /docs, /redoc and /openapi.json are real routes that
        # appear nowhere in app.openapi(), which is why walking the schema could
        # never have found them.
        assert len(routing_table(app)) > len(app.openapi()["paths"])

    def test_every_documented_path_is_in_it(self):
        # If FastAPI changes how included routers are stored, this is what
        # notices, rather than the guard test quietly walking an empty list.
        found = {path for path, _ in routing_table(app)}
        assert set(app.openapi()["paths"]) <= found

    def test_it_finds_the_routes_the_schema_hides(self):
        found = {path for path, _ in routing_table(app)}
        assert {"/docs", "/redoc", "/openapi.json"} <= found


class TestEverythingIsBehindIt:
    """A gate applied router by router is a gate somebody can forget to apply to
    the next one -- and FastAPI mounts four routes of its own that no router
    owns. This walks the real routing table and fails on anything that answers.

    Built the way a deployment builds it rather than the way the test suite
    does. In the local shape the reference docs are meant to be reachable, so
    asking the default app this question would be asking it about a
    configuration nobody deploys.
    """

    def test_no_route_answers_without_a_key(self, deployed: TestClient):
        unguarded = []
        for path, methods in routing_table(deployed.app):
            if path in PUBLIC_BY_DESIGN:
                continue
            for method in methods:
                # A concrete id, so a path parameter does not 422 before the
                # gate has had a chance to refuse.
                url = path.replace("{resolution_id}", "1").replace("{title_id}", "1")
                response = deployed.request(method, url)
                if response.status_code != 401:
                    unguarded.append(f"{method} {path} -> {response.status_code}")
        assert unguarded == []

    def test_health_is_the_only_thing_public_by_design(self):
        assert {"/health"} == PUBLIC_BY_DESIGN


class TestTheReferenceDocs:
    """They describe every endpoint in the app, and they sit outside every
    router, so the dependency guarding the rest cannot reach them. On the
    deployed backend they answered 200 while `/api/stats` answered 401."""

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
    def test_they_do_not_exist_where_the_gate_is_enforced(self, deployed: TestClient, path: str):
        # 404 rather than 401: the route is gone, not refused. There is nothing
        # to brute-force and nothing to confirm the guess against.
        assert deployed.get(path).status_code == 404

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_they_are_there_when_the_api_is_open_anyway(self, open_client: TestClient, path: str):
        # Where anyone can call every endpoint already, hiding the description
        # of them protects nothing and costs the one place they are useful.
        assert open_client.get(path).status_code == 200

    def test_the_schema_is_still_generated_without_a_route_to_serve_it(self):
        # openapi_url=None removes the route, not the method. The suite reads
        # the schema directly, and so does anything generating a client.
        built = create_app(Settings(database_url=DEPLOYED_URL, api_secret=SECRET))
        assert built.openapi()["paths"]

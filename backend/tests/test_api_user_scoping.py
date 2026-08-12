"""Every router says whose data it is working on.

Thirty service functions take ``user_id`` with a default of ``DEFAULT_USER_ID``.
That default is right for the services -- internal calls and tests read far
better without it, and one user is the whole truth today -- but it is wrong for
the routers. A router that omits it is not asking for "the current user", it is
asking for the constant, and the two only look the same while there is one
account. The day there are two, every route that relied on the default serves
one person's history to another, and nothing anywhere fails.

So the rule is: nothing under ``app/api/`` calls one of those functions without
saying whose data it is. ``api/deps.current_user`` is the one place that answers
the question, and it answers with the constant for now.

Checked by reading the code rather than by calling it, for the same reason
``test_api_security.py`` walks the real routing table instead of the OpenAPI
schema: what matters is what is *there*, and a runtime test cannot tell a route
that passed the right user from a route that got it by default.

The blind spot, stated rather than papered over: this sees calls that appear
lexically inside ``app/api/``. That deliberately includes router-local helpers
like ``watchlist._where`` -- which is exactly the shape that would otherwise
slip past -- but a router that reached a service through something defined
elsewhere would not be seen. Nothing does today.
"""

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_catalogue
from app.config import Settings, get_settings
from app.db import get_db
from app.main import app
from app.models import DEFAULT_USER_ID, Title, WatchEvent

APP = Path(__file__).resolve().parent.parent / "app"
ROUTERS = APP / "api"


def called_name(node: ast.Call) -> str | None:
    """The bare name of whatever is being called, attribute access or not."""
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return getattr(node.func, "id", None)


def user_scoped_functions() -> set[str]:
    """Every function in the app that takes a keyword-only ``user_id``."""
    found: set[str] = set()
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and any(
                arg.arg == "user_id" for arg in node.args.kwonlyargs
            ):
                found.add(node.name)
    return found


def router_calls() -> list[tuple[str, int, str]]:
    """Every call under ``app/api/`` to a user-scoped function.

    Returns (file, line, name) so a failure names the site rather than only the
    count -- a list of eighteen numbers is not something anybody can act on.
    """
    wanted = user_scoped_functions()
    calls: list[tuple[str, int, str]] = []
    for path in sorted(ROUTERS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and called_name(node) in wanted:
                calls.append((path.name, node.lineno, called_name(node)))
    return calls


def unscoped_calls_in(paths: Iterable[Path]) -> list[str]:
    """Calls in ``paths`` to a user-scoped function that do not say whose data it is.

    Takes the files to read rather than finding them, so the finder itself can
    be pointed at a known-bad sample and shown to flag it. An offender-finder
    that quietly returned nothing would make the assertion that uses it pass
    while checking nothing, which is indistinguishable from success.
    """
    wanted = user_scoped_functions()
    offenders: list[str] = []
    for path in sorted(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and called_name(node) in wanted):
                continue
            if not any(keyword.arg == "user_id" for keyword in node.keywords):
                offenders.append(f"{path.name}:{node.lineno} {called_name(node)}()")
    return offenders


def unscoped_router_calls() -> list[str]:
    return unscoped_calls_in(ROUTERS.rglob("*.py"))


class TestTheEnumeratorSeesSomething:
    """An enumerator that quietly found nothing would make every assertion
    below it pass while checking nothing at all -- which is indistinguishable
    from success and is the worst failure available to a test like this."""

    def test_it_finds_the_user_scoped_functions(self):
        found = user_scoped_functions()

        assert len(found) > 20, found
        # A spread across the modules, not one file's worth.
        assert {"overview", "recommend", "entries", "resolve_library"} <= found

    def test_it_finds_calls_to_them_in_the_routers(self):
        assert len(router_calls()) > 10

    def test_it_looks_inside_router_local_helpers_too(self):
        """`watchlist._where` calls the availability lookup on behalf of two
        routes. A check that only read the endpoint functions would miss it."""
        assert any(
            name == "watch_on_for" and file == "watchlist.py" for file, _, name in router_calls()
        )

    def test_it_flags_a_call_that_forgets(self, tmp_path: Path):
        """The load-bearing one. Everything below rests on this finder
        returning offenders when there are offenders, and a check that has
        never flagged anything has proved nothing about the day one appears."""
        sample = tmp_path / "forgetful_router.py"
        sample.write_text("def route(session):\n    return overview(session)\n", encoding="utf-8")

        assert unscoped_calls_in([sample]) == ["forgetful_router.py:2 overview()"]

    def test_it_leaves_a_call_that_remembers_alone(self, tmp_path: Path):
        """The other half: a finder that flagged everything would be just as
        useless, and would be noticed only as a mystifying failure."""
        sample = tmp_path / "careful_router.py"
        sample.write_text(
            "def route(session, user):\n    return overview(session, user_id=user)\n",
            encoding="utf-8",
        )

        assert unscoped_calls_in([sample]) == []


class TestEveryRouterSaysWhoseDataItIs:
    def test_no_router_relies_on_the_default_user(self):
        offenders = unscoped_router_calls()

        assert offenders == [], (
            "these calls take the default user instead of the current one:\n  "
            + "\n  ".join(offenders)
        )


class TestWhoIsAsking:
    def test_there_is_one_place_that_answers_it(self):
        """The point of the dependency: adding accounts is editing this
        function, not revisiting every route."""
        assert current_user() == DEFAULT_USER_ID

    def test_the_services_keep_their_default(self):
        """Deliberately not made required. Five hundred call sites, five hundred
        of them in tests and internal calls where the default is what keeps the
        code readable, to catch a mistake that can only be made in the eighteen
        places the test above already covers."""
        from app.services.stats import overview

        assert overview.__kwdefaults__["user_id"] == DEFAULT_USER_ID


@pytest.mark.parametrize("module", sorted(path.name for path in ROUTERS.rglob("*.py")))
def test_every_router_module_parses(module: str):
    """Guards the enumerators above: an unparseable module would silently
    contribute no calls rather than fail."""
    ast.parse((ROUTERS / module).read_text(encoding="utf-8"))


class TestTheDependencyIsWiredUpAndNotJustPresent:
    """The check above reads the code and sees that ``user_id`` is passed. It
    cannot see *what* is passed -- a route handing over the constant explicitly
    would satisfy it and be no better than the default it replaced.

    So these override who is asking and watch the answers change. Data stored
    for the default user must stop being visible the moment somebody else asks,
    which is the whole property, and the only test here that would notice if the
    dependency were wired to the wrong value.
    """

    SOMEBODY_ELSE = "another-account"

    @pytest.fixture
    def client(self, session: Session) -> Iterator[TestClient]:
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_settings] = lambda: Settings(jw_country="IN")
        with TestClient(app) as client:
            yield client
        app.dependency_overrides.clear()

    def as_somebody_else(self, client: TestClient) -> None:
        app.dependency_overrides[current_user] = lambda: self.SOMEBODY_ELSE

    def test_the_stats_of_one_user_are_not_the_stats_of_another(self, client: TestClient, watched):
        """Counted on the unresolved total rather than on `sessions`, which
        inner-joins the catalogue and so ignores a title nothing has matched
        yet. Both are user-scoped; only one of them counts a bare import."""
        watched("Inception")
        assert client.get("/api/stats").json()["unresolved_sessions"] == 1

        self.as_somebody_else(client)

        assert client.get("/api/stats").json()["unresolved_sessions"] == 0

    def test_one_users_watchlist_is_not_anothers(self, client: TestClient, session: Session):
        title = Title(jw_node_id="tm1", object_type="MOVIE", title="Inception")
        session.add(title)
        session.flush()
        assert client.post("/api/watchlist", json={"title_id": title.id}).status_code == 200
        assert len(client.get("/api/watchlist").json()) == 1

        self.as_somebody_else(client)

        assert client.get("/api/watchlist").json() == []

    def test_a_watchlist_entry_cannot_be_removed_by_somebody_else(
        self, client: TestClient, session: Session
    ):
        """The one that would be a write rather than a leak. A DELETE that
        ignored who was asking would take another account's row off the list."""
        title = Title(jw_node_id="tm1", object_type="MOVIE", title="Inception")
        session.add(title)
        session.flush()
        client.post("/api/watchlist", json={"title_id": title.id})

        self.as_somebody_else(client)

        assert client.delete(f"/api/watchlist/{title.id}").status_code == 404

    def test_an_import_is_stored_against_whoever_asked(self, client: TestClient, session: Session):
        self.as_somebody_else(client)

        response = client.post(
            "/api/imports/netflix",
            files={"file": ("history.csv", b"Title,Date\nInception,2024-01-01\n", "text/csv")},
        )

        assert response.status_code == 200
        assert response.json()["imported"] == 1
        stored = session.scalars(select(WatchEvent)).all()
        assert [event.user_id for event in stored] == [self.SOMEBODY_ELSE]

    def test_the_unresolved_queue_is_per_user(self, client: TestClient, watched):
        """Reading somebody else's unmatched titles would leak the raw exported
        strings, which are the rawest thing this app holds."""
        watched("Inception")
        app.dependency_overrides[get_catalogue] = lambda: _NothingFound()
        client.post("/api/titles/resolve")
        assert client.get("/api/titles/unresolved").json()["total"] == 1

        self.as_somebody_else(client)

        assert client.get("/api/titles/unresolved").json()["total"] == 0


class _NothingFound:
    """A catalogue that answers every search with nothing, so the pass refuses
    the title and leaves it in the queue this test is about."""

    country = "IN"

    def search(self, title: str, *, object_types=None) -> list:
        return []


class TestWhoIsAskingIsNotSomethingTheCallerSays:
    """The failure mode this whole arrangement would invert.

    ``UserDep`` is a dependency, so FastAPI resolves it and never looks at the
    request for it. Written as a plain annotated argument instead -- ``user: str
    = DEFAULT_USER_ID``, which is a one-character-different thing to type and
    reads almost identically -- it would become a **query parameter**, and every
    route would accept ``?user=somebody-else``. That turns a dependency meant to
    establish identity into a way for the caller to choose one, which is worse
    than the default it replaced: the default at least could not be aimed.

    Cheap to assert and impossible to notice by reading, so it is asserted.
    """

    def test_no_route_takes_a_user_parameter(self):
        offenders = [
            f"{verb.upper()} {path} ?{parameter['name']} (in {parameter['in']})"
            for path, methods in app.openapi()["paths"].items()
            for verb, spec in methods.items()
            for parameter in spec.get("parameters", [])
            if "user" in parameter["name"].lower()
        ]

        assert offenders == [], (
            "these routes let the caller choose whose data to read:\n  " + "\n  ".join(offenders)
        )

    def test_sending_one_anyway_changes_nothing(self, watched, session: Session):
        """Belt and braces, from the outside. An unknown query parameter is
        ignored by FastAPI rather than refused, so the only proof that it is not
        honoured is that the answer does not move."""
        app.dependency_overrides[get_db] = lambda: session
        try:
            with TestClient(app) as client:
                watched("Inception")
                honest = client.get("/api/stats").json()["unresolved_sessions"]
                spoofed = client.get(
                    "/api/stats", params={"user": "another-account", "user_id": "another-account"}
                ).json()["unresolved_sessions"]
        finally:
            app.dependency_overrides.clear()

        assert honest == 1
        assert spoofed == honest

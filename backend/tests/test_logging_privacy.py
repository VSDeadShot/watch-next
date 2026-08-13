"""Tests for what a log line is allowed to say about somebody's viewing history.

The input to this app is a person's viewing history, and the repository it lives
in is public. The database is the place that data is meant to be, and a log line
is not: on Render the platform holds it outside the database for as long as it
likes, and the natural move when something breaks -- pasting a warning into an
issue -- publishes whatever was in it. A resolve pass walks a whole library, so
one JustWatch outage used to write a good fraction of a viewing history into
those logs, verbatim, one line per title.

So the rule is that **a log line names a row, not its content**: an id somebody
with the database can look up, rather than the title itself. It costs nothing
diagnostically here, because these failures are network failures -- the
exception says how it failed, the summary counts say how many, and the title is
one query away for whoever is entitled to run it.

One file for a rule that spans three modules, rather than a test in each, for
the same reason ``test_api_security.py`` and ``test_schema_images.py`` are one
file each: the rule is the thing being described, and split across three modules
it would be three local observations nobody reads together. The sweep at the
bottom is the part that applies to log lines nobody has written yet.

``exc_info=True`` stays on the lines that had it, and is not a leak. The
exceptions come from the client library, and the search term travels as a
GraphQL variable in a POST body rather than in the URL -- so the message names
the endpoint and the status and nothing else, which
``TestTheExceptionDetailIsSafeToKeep`` pins down rather than assumes.
"""

import ast
import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from simplejustwatchapi.exceptions import JustWatchHttpError
from sqlalchemy.orm import Session

from app.models import Title
from app.services.justwatch_client import _usable
from app.services.offers import refresh_stale_offers
from app.services.resolver import resolve_library

#: A title with nothing else like it in the suite, so a leak cannot be mistaken
#: for some other string that happened to be in the message.
PRIVATE = "A Show Nobody Should Learn About"

#: Passed in rather than read off the clock, the way everything else in this
#: suite does it. A title with no availability yet is stale at any moment.
NOW = datetime(2026, 8, 13, tzinfo=UTC)

#: The application package, walked by the sweep at the bottom.
APP = Path(__file__).resolve().parents[1] / "app"


class RefusingCatalogue:
    """A catalogue where every request fails, which is the case that logs.

    Local rather than imported from ``test_resolver.py``: what these tests need
    from a fake is that it raises, and a fake that only raises is clearer read
    here than a scripted one whose script is empty.
    """

    country = "IN"

    def search(self, title: str, *, object_types=None):
        raise JustWatchHttpError("timed out")

    def details(self, node_id: str):
        raise JustWatchHttpError("timed out")


def warnings_from(caplog: pytest.LogCaptureFixture) -> str:
    """Everything logged, as one string -- which is how a person reads a log."""
    return "\n".join(record.getMessage() for record in caplog.records)


class TestALogLineNamesARowRatherThanItsContent:
    def test_a_failed_search_does_not_write_the_title_down(
        self, session: Session, watched, caplog: pytest.LogCaptureFixture
    ):
        watched(PRIVATE)

        with caplog.at_level(logging.WARNING):
            resolve_library(session, RefusingCatalogue())

        assert PRIVATE not in warnings_from(caplog)

    def test_it_says_which_rows_were_waiting_on_the_answer(
        self, session: Session, watched, caplog: pytest.LogCaptureFixture
    ):
        # The replacement has to be worth having: an id is only better than a
        # title if it is actually there to look up.
        event = watched(PRIVATE)

        with caplog.at_level(logging.WARNING):
            resolve_library(session, RefusingCatalogue())

        # The phrase rather than the bare number: on a fresh database the id is
        # 1, and "1 appears somewhere in the message" is satisfied by any message
        # with a digit in it.
        logged = warnings_from(caplog)
        assert f"one of them {event.id}" in logged
        # The count reads correctly for one row as well as for forty. Pinned
        # because a log line nobody has proofread is a log line nobody trusts.
        assert "1 watch event," in logged

    def test_a_long_series_does_not_empty_its_episode_list_into_the_log(
        self, session: Session, watched, caplog: pytest.LogCaptureFixture
    ):
        # Every episode row of a series waits on one question, so the first
        # version of this line printed all of their ids: a 212-episode show made
        # a 999-character log line, once per show, for as long as the outage
        # lasted. One id is all anybody needs to find the row.
        for episode in range(40):
            watched(PRIVATE, kind="episode", episode_number=episode)

        with caplog.at_level(logging.WARNING):
            resolve_library(session, RefusingCatalogue())

        [record] = caplog.records
        assert "40 watch events" in record.getMessage()
        assert len(record.getMessage()) < 120

    def test_the_failure_itself_is_still_reported(
        self, session: Session, watched, caplog: pytest.LogCaptureFixture
    ):
        # Redacting a line into uselessness would be its own defect. The reason
        # for the warning, and the exception behind it, both survive.
        watched(PRIVATE)

        with caplog.at_level(logging.WARNING):
            resolve_library(session, RefusingCatalogue())

        [record] = caplog.records
        assert "could not search" in record.getMessage()
        assert record.exc_info is not None

    def test_a_failed_availability_refresh_does_not_name_the_catalogue_id(
        self, session: Session, caplog: pytest.LogCaptureFixture
    ):
        # Weaker than a title -- the id is JustWatch's, and discovery puts rows
        # in this table nobody watched -- but it still says what is in somebody's
        # catalogue, and our own primary key says nothing at all outside the
        # database.
        session.add(Title(jw_node_id="tm-private", object_type="MOVIE", title=PRIVATE))
        session.flush()

        with caplog.at_level(logging.WARNING):
            refresh_stale_offers(session, RefusingCatalogue(), now=NOW)

        logged = warnings_from(caplog)
        assert PRIVATE not in logged
        assert "tm-private" not in logged

    def test_it_names_our_own_row_instead(self, session: Session, caplog: pytest.LogCaptureFixture):
        title = Title(jw_node_id="tm-private", object_type="MOVIE", title=PRIVATE)
        session.add(title)
        session.flush()

        with caplog.at_level(logging.WARNING):
            refresh_stale_offers(session, RefusingCatalogue(), now=NOW)

        # Named, for the same reason as above: a bare "1" would be satisfied by
        # any message with a digit in it.
        assert f"title {title.id}" in warnings_from(caplog)

    def test_an_unusable_result_is_reported_without_the_search_that_found_it(
        self, caplog: pytest.LogCaptureFixture
    ):
        # The query here is either a title out of the history or something the
        # user typed, and the result's own title is JustWatch's answer to it --
        # which for a search is generally the same string back again.
        entry = unusable_entry(title=PRIVATE)

        with caplog.at_level(logging.WARNING):
            assert _usable([entry], PRIVATE) == []

        assert PRIVATE not in warnings_from(caplog)

    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            (lambda: unusable_entry(title=PRIVATE), "id"),
            (lambda: unusable_entry(entry_id="tm1", title=" "), "title"),
        ],
    )
    def test_it_still_says_what_was_wrong_with_the_row(
        self, entry, expected: str, caplog: pytest.LogCaptureFixture
    ):
        # Which defect it was is the whole diagnostic value of the line: it says
        # what shape of fixture to write. Neither defect names anybody.
        with caplog.at_level(logging.WARNING):
            _usable([entry()], PRIVATE)

        assert expected in warnings_from(caplog)


def unusable_entry(*, entry_id=None, title=None):
    """A library result missing what makes a result usable.

    Built from the library's own named tuple with every field emptied, so the
    two this rule cares about are the only two the reader has to look at -- and
    a field the library renames breaks this instead of quietly passing.
    """
    from simplejustwatchapi.query import MediaEntry

    blank = MediaEntry(**dict.fromkeys(MediaEntry._fields, None))
    return blank._replace(entry_id=entry_id, title=title)


class TestTheExceptionDetailIsSafeToKeep:
    """``exc_info=True`` is the useful half of these lines, so it is worth being
    sure it does not carry back in what the message no longer says."""

    def test_a_failed_request_names_the_endpoint_and_not_the_search_term(self):
        # Built the way the library builds it: `justwatch.py` wraps the httpx
        # error as `JustWatchHttpError(str(error), response.text)`. The term is a
        # GraphQL variable in the POST body, and httpx puts the URL in the
        # message rather than the body.
        request = httpx.Request(
            "POST",
            "https://apis.justwatch.com/graphql",
            json={"query": "query GetSearch($searchTitle: String!)", "variables": {"q": PRIVATE}},
        )
        response = httpx.Response(400, request=request, text='{"errors":[{"message":"no"}]}')
        cause = httpx.HTTPStatusError("boom", request=request, response=response)
        error = JustWatchHttpError(str(cause), response.text)

        assert PRIVATE not in str(error)
        assert PRIVATE not in (error.response or "")


#: Names that hold a person's data rather than a reference to it. A log call
#: passing any of them -- as a bare name, an attribute, or inside an f-string --
#: is writing content where it should be writing an id.
#:
#: Deliberately a small list of specific names rather than anything clever. It
#: has to be readable by whoever trips it, and a heuristic that guessed would
#: either be argued with or quietly widened until it caught nothing.
PERSONAL_NAMES = frozenset(
    {
        "display_title",
        "raw_title",
        "query_title",
        "query",
        "title",
        "key",
        "note",
        "jw_node_id",
    }
)

#: Every way this app logs. `_log` is the module-level logger in each service;
#: `logging` covers a call made straight through the module.
_LOGGERS = frozenset({"_log", "logging"})
_LEVELS = frozenset({"debug", "info", "warning", "error", "exception", "critical", "log"})


def log_calls(source: str) -> list[ast.Call]:
    """Every logging call in ``source``."""
    return [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _LEVELS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in _LOGGERS
    ]


def personal_names_logged(source: str, where: str) -> list[str]:
    """Where ``source`` logs something from :data:`PERSONAL_NAMES`.

    Walks each argument rather than reading only its top level, so a name
    reached through an f-string or a call is found too.

    An attribute is judged by the attribute and not by what it was read from:
    ``title.id`` is a reference and is exactly the form this rule is asking for,
    while ``title.title`` would not be. So the name an attribute is taken off is
    not itself an offence -- but a name logged on its own, with no attribute
    read from it, is the whole object and is.
    """
    found = []
    for call in log_calls(source):
        for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
            owners = {
                node.value
                for node in ast.walk(argument)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            }
            for node in ast.walk(argument):
                name = None
                if isinstance(node, ast.Attribute):
                    name = node.attr
                elif isinstance(node, ast.Name) and node not in owners:
                    name = node.id
                if name in PERSONAL_NAMES:
                    found.append(f"{where}:{call.lineno} logs {name}")
    return found


class TestNoOtherLogLineSaysMore:
    """The three tests above describe the three lines that exist. This describes
    the ones nobody has written yet, which is the only version of this rule that
    survives the next feature.

    Scoped to ``app/``, which is what runs on the platform. ``scripts/`` is
    deliberately outside it: ``smoke_justwatch.py`` prints the titles it was
    asked about on the command line, to the terminal of the person who typed
    them, and is never part of a deployed process. ``alembic/`` configures
    logging but has no data of anybody's to log.
    """

    def test_the_sweep_finds_the_log_calls_that_are_there(self):
        # A sweep that quietly matched nothing would pass the test below forever
        # while checking nothing at all -- the worst failure available to a test
        # like this, because it looks exactly like success.
        modules = {
            path.name for path in APP.rglob("*.py") if log_calls(path.read_text(encoding="utf-8"))
        }
        assert modules == {
            "main.py",
            "discovery.py",
            "justwatch_client.py",
            "offers.py",
            "providers.py",
            "resolver.py",
        }

    def test_it_would_notice_a_line_that_named_a_title(self):
        # The sweep run against the code this commit removed, so its teeth are
        # demonstrated rather than assumed.
        source = '_log.warning("could not search for %r", question.display_title, exc_info=True)'

        assert personal_names_logged(source, "somewhere.py") == [
            "somewhere.py:1 logs display_title"
        ]

    def test_it_looks_inside_an_f_string_too(self):
        # The obvious way round a check that only read the top level of an
        # argument, and the way somebody would reach for it without thinking.
        source = '_log.warning(f"could not search for {question.display_title}")'

        assert personal_names_logged(source, "somewhere.py") != []

    def test_it_ignores_a_line_that_names_a_row(self):
        source = '_log.warning("could not search for %r", question.event_ids, exc_info=True)'

        assert personal_names_logged(source, "somewhere.py") == []

    def test_a_reference_read_off_a_personal_row_is_the_point_and_not_an_offence(self):
        # `title.id` is the form this rule exists to ask for. A sweep that
        # objected to it would be one nobody could satisfy, which is how a rule
        # like this gets deleted rather than followed.
        source = '_log.warning("could not refresh availability for title %r", title.id)'

        assert personal_names_logged(source, "somewhere.py") == []

    def test_the_row_itself_is_still_an_offence(self):
        # The other side of that: no attribute read means the whole object is
        # being formatted, and `%r` of a model prints whatever __repr__ says.
        source = '_log.warning("could not refresh availability for %r", title)'

        assert personal_names_logged(source, "somewhere.py") == ["somewhere.py:1 logs title"]

    def test_nothing_in_the_app_logs_anything_personal(self):
        offenders = []
        for path in sorted(APP.rglob("*.py")):
            offenders += personal_names_logged(
                path.read_text(encoding="utf-8"), str(path.relative_to(APP.parent))
            )
        assert offenders == []

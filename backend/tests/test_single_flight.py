"""Tests for refusing a second pass rather than queueing it.

Resolution and offer refresh both spend one budget -- the JustWatch client
paces every request at one a second behind a process-wide lock -- so a second
concurrent pass never makes anything finish sooner. What it does cost is a
worker thread held for the whole wait, out of the forty every route in the app
shares, and that is what takes the health check down with it.
"""

import threading

import pytest
from simplejustwatchapi.exceptions import JustWatchHttpError
from sqlalchemy.orm import Session

from app.models import Title
from app.services.offers import refresh_stale_offers
from app.services.resolver import resolve_library
from app.services.single_flight import PassAlreadyRunning, SingleFlight, one_at_a_time


class TestClaimingTheBudget:
    def test_a_first_claim_is_granted(self):
        guard = SingleFlight()

        with guard.claim("resolve"):
            pass  # granted, or the context manager would have raised

    def test_a_second_claim_while_the_first_holds_is_refused(self):
        guard = SingleFlight()

        with guard.claim("resolve"), pytest.raises(PassAlreadyRunning), guard.claim("refresh"):
            pass

    def test_the_refusal_names_the_pass_already_running(self):
        """So the message can say what to wait for rather than only that
        something is in the way."""
        guard = SingleFlight()

        with (
            guard.claim("resolve"),
            pytest.raises(PassAlreadyRunning) as excinfo,
            guard.claim("refresh"),
        ):
            pass

        assert excinfo.value.holder == "resolve"

    def test_a_claim_after_the_first_finished_is_granted(self):
        guard = SingleFlight()

        with guard.claim("resolve"):
            pass

        with guard.claim("refresh"):
            pass

    def test_the_claim_is_released_when_the_body_raises(self):
        """The failure that would otherwise need a restart to clear: one
        exception inside a pass and the endpoint refuses every caller forever."""
        guard = SingleFlight()

        with pytest.raises(ZeroDivisionError), guard.claim("resolve"):
            raise ZeroDivisionError

        with guard.claim("refresh"):
            pass

    def test_a_refused_claim_does_not_release_the_holder(self):
        """A refusal must not free a lock it never took. Otherwise two callers
        arriving together leave the guard open for a third."""
        guard = SingleFlight()

        with guard.claim("resolve"):
            with pytest.raises(PassAlreadyRunning), guard.claim("refresh"):
                pass

            # Still held by the first, which has not finished.
            with pytest.raises(PassAlreadyRunning), guard.claim("another"):
                pass

    def test_it_is_held_against_another_thread(self):
        """The case it exists for. Every concurrent pass arrives on its own
        worker thread, so a guard that only stopped re-entry on one thread
        would stop nothing that matters."""
        guard = SingleFlight()
        refused = threading.Event()
        release = threading.Event()

        def second() -> None:
            try:
                with guard.claim("refresh"):
                    pass
            except PassAlreadyRunning:
                refused.set()

        with guard.claim("resolve"):
            thread = threading.Thread(target=second, daemon=True)
            thread.start()
            thread.join(timeout=5.0)
            release.set()

        assert refused.is_set(), "the second thread was let through, or it blocked"

    def test_it_does_not_block_waiting_for_its_turn(self):
        """Refused, not queued. Waiting is the whole failure: a queued caller
        holds its worker thread for as long as the pass ahead of it runs, which
        is what exhausts the pool."""
        guard = SingleFlight()
        outcome: list[str] = []

        def second() -> None:
            try:
                with guard.claim("refresh"):
                    outcome.append("granted")
            except PassAlreadyRunning:
                outcome.append("refused")

        with guard.claim("resolve"):
            thread = threading.Thread(target=second, daemon=True)
            thread.start()
            thread.join(timeout=2.0)

            assert not thread.is_alive(), "the second claim blocked instead of being refused"

        assert outcome == ["refused"]


class TestTheDecorator:
    """`one_at_a_time` puts the rule at the definition site rather than in a
    block wrapped around each pass body."""

    def call_from_another_thread(self, function) -> str:
        outcome: list[str] = []

        def attempt() -> None:
            try:
                function()
                outcome.append("granted")
            except PassAlreadyRunning:
                outcome.append("refused")

        thread = threading.Thread(target=attempt, daemon=True)
        thread.start()
        thread.join(timeout=5.0)
        assert not thread.is_alive(), "the call blocked instead of returning"
        return outcome[0]

    def test_a_decorated_pass_runs_normally_on_its_own(self):
        @one_at_a_time("resolve")
        def pass_() -> str:
            return "done"

        assert pass_() == "done"

    def test_arguments_and_the_return_value_survive_the_wrapping(self):
        @one_at_a_time("resolve")
        def pass_(a: int, *, b: int) -> int:
            return a + b

        assert pass_(1, b=2) == 3

    def test_a_second_caller_is_refused_while_the_first_runs(self):
        started = threading.Event()
        finish = threading.Event()

        @one_at_a_time("resolve")
        def slow() -> None:
            started.set()
            finish.wait(timeout=5.0)

        @one_at_a_time("resolve")
        def second() -> None:
            pass

        thread = threading.Thread(target=slow, daemon=True)
        thread.start()
        assert started.wait(timeout=5.0)
        try:
            assert self.call_from_another_thread(second) == "refused"
        finally:
            finish.set()
            thread.join(timeout=5.0)

    def test_two_different_passes_share_one_budget(self):
        """Resolution and refresh contend for the same one-a-second budget, so
        guarding them separately would allow exactly the pair that hurts."""
        started = threading.Event()
        finish = threading.Event()

        @one_at_a_time("resolve")
        def resolving() -> None:
            started.set()
            finish.wait(timeout=5.0)

        @one_at_a_time("refresh")
        def refreshing() -> None:
            pass

        thread = threading.Thread(target=resolving, daemon=True)
        thread.start()
        assert started.wait(timeout=5.0)
        try:
            assert self.call_from_another_thread(refreshing) == "refused"
        finally:
            finish.set()
            thread.join(timeout=5.0)

    def test_the_budget_is_free_again_afterwards(self):
        @one_at_a_time("resolve")
        def pass_() -> None:
            pass

        pass_()

        assert self.call_from_another_thread(pass_) == "granted"

    def test_the_wrapped_function_keeps_its_name(self):
        @one_at_a_time("resolve")
        def resolve_library() -> None:
            pass

        assert resolve_library.__name__ == "resolve_library"


class _Blocking:
    """A catalogue that stops mid-pass until it is let go.

    Signals that the pass is genuinely inside the guard before the test looks,
    so nothing here depends on a sleep being long enough.
    """

    country = "IN"

    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self._started = started
        self._release = release

    def _block(self) -> None:
        self._started.set()
        self._release.wait(timeout=5.0)

    def search(self, title: str, *, object_types=None) -> list:
        self._block()
        return []

    def details(self, node_id: str):
        self._block()
        # Contained and counted by the pass, so it finishes rather than raising
        # out of the thread and taking the assertion's context with it.
        raise JustWatchHttpError("stopped here on purpose")


class TestTheRealPassesNameThemselves:
    """The name in the refusal is what tells somebody which chore to wait for.

    Asserted against the real passes rather than only against stubs, because a
    decorator applied with the wrong label is invisible to every other test:
    the guard still guards, and only the sentence is wrong.
    """

    def held_by(self, running, blocked) -> str:
        started, release = threading.Event(), threading.Event()
        catalogue = _Blocking(started, release)

        thread = threading.Thread(target=lambda: running(catalogue), daemon=True)
        thread.start()
        assert started.wait(timeout=5.0), "the first pass never reached the catalogue"
        try:
            with pytest.raises(PassAlreadyRunning) as excinfo:
                blocked(catalogue)
            return excinfo.value.holder
        finally:
            release.set()
            thread.join(timeout=5.0)

    def test_a_running_refresh_is_named_refresh(self, session: Session):
        session.add(Title(jw_node_id="tm1", object_type="MOVIE", title="Inception"))
        session.flush()

        holder = self.held_by(
            running=lambda catalogue: refresh_stale_offers(session, catalogue),
            blocked=lambda catalogue: resolve_library(session, catalogue),
        )

        assert holder == "refresh"

    def test_a_running_resolve_is_named_resolve(self, session: Session, watched):
        watched("Inception")

        holder = self.held_by(
            running=lambda catalogue: resolve_library(session, catalogue),
            blocked=lambda catalogue: refresh_stale_offers(session, catalogue),
        )

        assert holder == "resolve"

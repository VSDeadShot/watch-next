"""One JustWatch pass at a time, for the whole process.

Resolution and offer refresh spend the same budget. :class:`JustWatchClient`
paces every request at one a second, and it does that by sleeping while holding
a process-wide lock -- deliberately, so the limit applies to what this app sends
JustWatch in total rather than per worker. The consequence is that two passes
running at once do not go twice as fast. They take turns, and each takes about
twice as long as it would have alone.

What a second pass does buy is a second held thread. Both endpoints are plain
``def``, which FastAPI runs in a worker thread from a pool of forty that every
route in this app shares -- ``/health`` included, which is what Render polls to
decide whether the service is alive. A pass waiting its turn holds its thread
for the whole wait, so forty concurrent passes hold all forty threads and the
app stops answering anything at all.

Measured on the real app under uvicorn, with the catalogue stubbed to sleep the
way the real client does: ``/health`` went from 34 ms to 4.7 seconds and
``/api/stats`` did not answer inside twenty. Capping how much work a single
request may do changed neither number -- forty requests of twenty-five searches
starve the pool exactly as forty unlimited ones do, because it is the same total
work arriving on the same number of threads. That is why this module exists and
a ``le=`` on the ``limit`` parameter does not replace it.

So a second pass is refused outright rather than queued. Refusing is honest:
queueing would hold the thread that is the actual scarce resource, in order to
wait for a turn that was never going to make anything finish sooner.

Recommendation deliberately does not claim this. ``/api/recommend`` tops the
pool up from the same budget, but it is the one thing this product exists to do,
and a chore running in another tab must not be able to answer it with a refusal.
"""

import functools
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import ParamSpec, TypeVar

_P = ParamSpec("_P")
_R = TypeVar("_R")


class PassAlreadyRunning(RuntimeError):
    """A pass of this kind is already running, and a second was refused."""

    def __init__(self, holder: str) -> None:
        # The whole sentence lives here rather than in each router, so the two
        # endpoints cannot drift into explaining the same refusal differently
        # -- and so the message can name the pass actually in the way.
        super().__init__(
            f"a {holder} pass is already running. Wait for it to finish: both "
            "passes spend the same one-request-a-second budget, so running two "
            "at once does not make either of them finish sooner."
        )
        #: Which pass holds it, so a caller can say what to wait for.
        self.holder = holder


class SingleFlight:
    """Admits one holder at a time and refuses the rest immediately."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holder: str | None = None

    @contextmanager
    def claim(self, name: str) -> Iterator[None]:
        """Hold the budget for the duration of the block, or refuse.

        Raises:
            PassAlreadyRunning: if another pass holds it. Never waits --
                ``blocking=False`` is the entire point, since a caller made to
                wait keeps the worker thread this is trying to protect.
        """
        if not self._lock.acquire(blocking=False):
            # Read after the failed acquire, so it can race with a holder on
            # its way out. That costs at worst a message naming the wrong pass,
            # never a wrong decision: the refusal is the lock's answer, not
            # this line's.
            raise PassAlreadyRunning(self._holder or "another")

        self._holder = name
        try:
            yield
        finally:
            # Cleared before the release, so nothing can observe the guard free
            # while it still names a holder.
            self._holder = None
            self._lock.release()


#: Shared by resolution and offer refresh, because they spend one budget between
#: them. Two passes of different kinds contend exactly as two of the same kind
#: do, so guarding them separately would refuse the harmless case and allow the
#: one that hurts.
budget = SingleFlight()


def one_at_a_time(name: str) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Refuse a second concurrent call to the decorated pass.

    A decorator rather than a `with` around each body, so the rule reads at the
    definition site instead of being a block somebody has to scroll past -- and
    so that adding a third pass later is one line rather than an indent.
    """

    def decorate(function: Callable[_P, _R]) -> Callable[_P, _R]:
        @functools.wraps(function)
        def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with budget.claim(name):
                return function(*args, **kwargs)

        return guarded

    return decorate

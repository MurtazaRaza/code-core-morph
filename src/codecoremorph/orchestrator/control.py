"""Stopping a run (FR-14.4): Ctrl-C and ``codecoremorph abort``.

- The first Ctrl-C asks the engine to stop at the next safe point: the unit in progress rolls
  back and the run parks, to be resumed (``codecoremorph resume``) or aborted later. A second
  Ctrl-C stops at once (``KeyboardInterrupt``); the unit still rolls back on the way out.
- ``codecoremorph abort RUN`` writes an abort request into the store (an ``approvals`` row with
  gate ``run``, D-27). The engine finds it at the next safe point, rolls the unit back and ends
  the run as ``aborted``, with its report.

Safe points are the run graph's node boundaries, every agent attempt and model call, and the
waits at gates.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from types import FrameType

from codecoremorph.core.errors import CodeCoreMorphError, ExitCode
from codecoremorph.core.models import ApprovalDecision, ApprovalGate, ApprovalRecord
from codecoremorph.store.db import Store

log = logging.getLogger(__name__)


class StopRequestedError(CodeCoreMorphError):
    """The run must stop at this safe point: ``abort`` ends it, otherwise it parks."""

    exit_code = ExitCode.ABORTED

    def __init__(self, message: str, *, abort: bool, request: ApprovalRecord | None = None):
        super().__init__(message)
        self.abort = abort
        self.request = request


class StopControl:
    """The run's stop requests, checked at safe points. One per engine process."""

    def __init__(
        self,
        store: Store,
        run_id: str,
        *,
        poll_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._store = store
        self.run_id = run_id
        self._poll_s = poll_s
        self._clock = clock
        self._last_poll = float("-inf")
        self._abort: ApprovalRecord | None = None
        self.interrupted = False

    def abort_request(self, *, force: bool = False) -> ApprovalRecord | None:
        """A pending ``codecoremorph abort`` for this run (polled at most every ``poll_s``)."""
        if self._abort is not None:
            return self._abort
        now = self._clock()
        if not force and now - self._last_poll < self._poll_s:
            return None
        self._last_poll = now
        for request in self._store.approvals(self.run_id, pending=True):
            if request.gate is ApprovalGate.RUN and request.decision is ApprovalDecision.ABORT:
                self._abort = request
                return request
        return None

    def check(self) -> None:
        """Raise :class:`StopRequestedError` when the run must stop here."""
        request = self.abort_request()
        if request is not None:
            raise StopRequestedError(
                "the run was aborted" + (f": {request.reason}" if request.reason else ""),
                abort=True,
                request=request,
            )
        if self.interrupted:
            raise StopRequestedError("stopped by Ctrl-C", abort=False)

    @contextmanager
    def catching_ctrl_c(self, on_first: Callable[[], None]) -> Generator[None]:
        """Make the first Ctrl-C a request to stop at the next safe point; the second one
        raises ``KeyboardInterrupt`` as usual. Only in the main thread."""
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        previous = signal.getsignal(signal.SIGINT)

        def handler(signum: int, frame: FrameType | None) -> None:
            if self.interrupted:
                signal.signal(signal.SIGINT, signal.default_int_handler)
                raise KeyboardInterrupt
            self.interrupted = True
            on_first()

        signal.signal(signal.SIGINT, handler)
        try:
            yield
        finally:
            signal.signal(signal.SIGINT, previous)

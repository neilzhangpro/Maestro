"""Exponential backoff retry queue (SPEC §8.4)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from maestro.orchestrator.state import OrchestratorState, RetryEntry

log = logging.getLogger(__name__)


class RetryQueue:
    """Schedule and manage retry timers for failed or continued issues."""

    def __init__(
        self,
        state: OrchestratorState,
        on_fire: Callable[[str], None],
        *,
        max_retry_backoff_ms: int = 300_000,
    ) -> None:
        self._state = state
        self._on_fire = on_fire
        self.max_retry_backoff_ms = max_retry_backoff_ms

    def schedule(
        self,
        issue_id: str,
        identifier: str,
        attempt: int,
        error: str | None,
        *,
        continuation: bool = False,
    ) -> None:
        if continuation:
            delay_ms = 1_000
        else:
            delay_ms = min(
                10_000 * (2 ** max(attempt - 1, 0)),
                self.max_retry_backoff_ms,
            )

        due_at = time.monotonic() * 1000 + delay_ms
        timer = threading.Timer(delay_ms / 1000, self._timer_fired, args=[issue_id])
        timer.daemon = True

        entry = RetryEntry(
            issue_id=issue_id,
            identifier=identifier,
            attempt=attempt,
            due_at_ms=due_at,
            timer=timer,
            error=error,
        )
        self._state.set_retry(entry)
        timer.start()

        log.info(
            "Retry scheduled: %s attempt=%d delay=%dms (continuation=%s) error=%s",
            identifier, attempt, delay_ms, continuation, error,
        )

    def cancel(self, issue_id: str) -> None:
        entry = self._state.pop_retry(issue_id)
        if entry and entry.timer:
            entry.timer.cancel()

    def _timer_fired(self, issue_id: str) -> None:
        log.info("Retry timer fired for %s", issue_id)
        self._on_fire(issue_id)

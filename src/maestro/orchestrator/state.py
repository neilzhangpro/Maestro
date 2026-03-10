"""Orchestrator runtime state — the single source of truth for scheduling."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class RunningEntry:
    """Tracked state for a currently-executing worker."""

    issue_id: str
    identifier: str
    issue_state: str
    worker_thread: threading.Thread | None = None
    session_id: str | None = None
    turn_count: int = 0
    last_event: str | None = None
    last_event_at: datetime | None = None
    last_message: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    retry_attempt: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "issue_identifier": self.identifier,
            "state": self.issue_state,
            "session_id": self.session_id,
            "turn_count": self.turn_count,
            "last_event": self.last_event,
            "last_message": self.last_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
        }


@dataclass
class RetryEntry:
    """Scheduled retry for an issue."""

    issue_id: str
    identifier: str
    attempt: int
    due_at_ms: float
    timer: threading.Timer | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "issue_identifier": self.identifier,
            "attempt": self.attempt,
            "error": self.error,
        }


@dataclass
class AgentTotals:
    """Aggregate runtime metrics."""

    seconds_running: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"seconds_running": round(self.seconds_running, 1)}


class OrchestratorState:
    """Thread-safe in-memory state owned by the orchestrator.

    All mutations go through methods that hold ``_lock``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running: dict[str, RunningEntry] = {}
        self.claimed: set[str] = set()
        self.retry_attempts: dict[str, RetryEntry] = {}
        self.completed: set[str] = set()
        self.totals = AgentTotals()

    def add_running(self, entry: RunningEntry) -> None:
        with self._lock:
            self.running[entry.issue_id] = entry
            self.claimed.add(entry.issue_id)
            self.retry_attempts.pop(entry.issue_id, None)

    def remove_running(self, issue_id: str) -> RunningEntry | None:
        with self._lock:
            return self.running.pop(issue_id, None)

    def is_claimed(self, issue_id: str) -> bool:
        with self._lock:
            return issue_id in self.claimed

    def release_claim(self, issue_id: str) -> None:
        with self._lock:
            self.claimed.discard(issue_id)
            self.retry_attempts.pop(issue_id, None)

    def set_retry(self, entry: RetryEntry) -> None:
        with self._lock:
            old = self.retry_attempts.get(entry.issue_id)
            if old and old.timer:
                old.timer.cancel()
            self.retry_attempts[entry.issue_id] = entry

    def pop_retry(self, issue_id: str) -> RetryEntry | None:
        with self._lock:
            return self.retry_attempts.pop(issue_id, None)

    def update_running_event(
        self,
        issue_id: str,
        *,
        event: str,
        message: str = "",
        session_id: str | None = None,
    ) -> None:
        with self._lock:
            entry = self.running.get(issue_id)
            if not entry:
                return
            entry.last_event = event
            entry.last_event_at = datetime.now(timezone.utc)
            if message:
                entry.last_message = message
            if session_id:
                entry.session_id = session_id
            if event == "turn_completed":
                entry.turn_count += 1

    def add_runtime_seconds(self, seconds: float) -> None:
        with self._lock:
            self.totals.seconds_running += seconds

    def running_count(self) -> int:
        with self._lock:
            return len(self.running)

    def running_ids(self) -> list[str]:
        with self._lock:
            return list(self.running.keys())

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of the current state."""
        with self._lock:
            now = datetime.now(timezone.utc)
            active_seconds = sum(
                (now - e.started_at).total_seconds()
                for e in self.running.values()
            )
            totals = self.totals.to_dict()
            totals["seconds_running"] = round(
                self.totals.seconds_running + active_seconds, 1,
            )
            return {
                "generated_at": now.isoformat(),
                "counts": {
                    "running": len(self.running),
                    "retrying": len(self.retry_attempts),
                },
                "running": [e.to_dict() for e in self.running.values()],
                "retrying": [e.to_dict() for e in self.retry_attempts.values()],
                "totals": totals,
            }

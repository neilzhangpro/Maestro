"""Orchestrator runtime state — the single source of truth for scheduling."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_EVENT_HISTORY = 80
MAX_RECENT_EXITS = 20


@dataclass
class EventEntry:
    """One recent worker event for TUI/debug visibility."""

    timestamp: datetime
    event: str
    message: str = ""
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "event": self.event,
            "message": self.message,
            "session_id": self.session_id,
        }


@dataclass
class ExitEntry:
    """Recently finished worker retained for post-mortem visibility."""

    issue_id: str
    identifier: str
    issue_state: str
    reason: str
    error: str | None
    started_at: datetime
    ended_at: datetime
    turn_count: int = 0
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        runtime_s = max((self.ended_at - self.started_at).total_seconds(), 0.0)
        return {
            "issue_id": self.issue_id,
            "issue_identifier": self.identifier,
            "state": self.issue_state,
            "reason": self.reason,
            "error": self.error,
            "session_id": self.session_id,
            "turn_count": self.turn_count,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "runtime_seconds": round(runtime_s, 1),
        }


@dataclass
class RunningEntry:
    """Tracked state for a currently-executing worker."""

    issue_id: str
    identifier: str
    issue_state: str
    worker_thread: threading.Thread | None = None
    worker_ref: Any | None = None
    session_id: str | None = None
    turn_count: int = 0
    last_event: str | None = None
    last_event_at: datetime | None = None
    last_message: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    retry_attempt: int | None = None
    event_history: list[EventEntry] = field(default_factory=list)

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
            "event_history": [e.to_dict() for e in self.event_history],
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


COOLDOWN_SECONDS = 600


class OrchestratorState:
    """Thread-safe in-memory state owned by the orchestrator.

    All mutations go through methods that hold ``_lock``.
    """

    def __init__(self, persist_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._persist_path = persist_path
        self.running: dict[str, RunningEntry] = {}
        self.claimed: set[str] = set()
        self.retry_attempts: dict[str, RetryEntry] = {}
        self.completed: set[str] = set()
        self._cooldowns: dict[str, datetime] = {}
        self.totals = AgentTotals()
        self.recent_exits: list[ExitEntry] = []
        self._load_state()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        """Restore cooldown state from disk (called once during __init__)."""
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            with open(self._persist_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            now = datetime.now(timezone.utc)
            loaded = 0
            for issue_id, ts_str in data.get("cooldowns", {}).items():
                ts = datetime.fromisoformat(ts_str)
                elapsed = (now - ts).total_seconds()
                if elapsed < COOLDOWN_SECONDS:
                    self._cooldowns[issue_id] = ts
                    self.completed.add(issue_id)
                    loaded += 1
            log.info(
                "OrchestratorState: loaded %d active cooldown(s) from %s",
                loaded, self._persist_path,
            )
        except Exception:
            log.warning(
                "OrchestratorState: failed to load persisted state — starting fresh.",
                exc_info=True,
            )

    def _persist_state(self) -> None:
        """Write cooldowns to disk atomically. Must be called while holding _lock."""
        if self._persist_path is None:
            return
        data = {
            "cooldowns": {k: v.isoformat() for k, v in self._cooldowns.items()},
            "completed": list(self.completed),
            "persisted_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = self._persist_path.with_suffix(".tmp")
        try:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self._persist_path)
        except Exception:
            log.warning(
                "OrchestratorState: could not persist state to %s.",
                self._persist_path, exc_info=True,
            )

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

    def mark_completed(self, issue_id: str) -> None:
        """Release claim and enter cooldown so the issue is not re-dispatched immediately."""
        with self._lock:
            self.claimed.discard(issue_id)
            self.retry_attempts.pop(issue_id, None)
            self.completed.add(issue_id)
            self._cooldowns[issue_id] = datetime.now(timezone.utc)
            self._persist_state()

    def in_cooldown(self, issue_id: str) -> bool:
        with self._lock:
            ts = self._cooldowns.get(issue_id)
            if ts is None:
                return False
            elapsed = (datetime.now(timezone.utc) - ts).total_seconds()
            if elapsed >= COOLDOWN_SECONDS:
                self._cooldowns.pop(issue_id, None)
                self.completed.discard(issue_id)
                return False
            return True

    def clear_cooldown(self, issue_id: str) -> None:
        with self._lock:
            self._cooldowns.pop(issue_id, None)
            self.completed.discard(issue_id)

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
            entry.event_history.append(EventEntry(
                timestamp=entry.last_event_at,
                event=event,
                message=message,
                session_id=session_id or entry.session_id,
            ))
            if len(entry.event_history) > MAX_EVENT_HISTORY:
                entry.event_history = entry.event_history[-MAX_EVENT_HISTORY:]
            if event == "turn_completed":
                entry.turn_count += 1

    def record_exit(self, entry: RunningEntry, *, reason: str, error: str | None) -> None:
        with self._lock:
            self.recent_exits.insert(0, ExitEntry(
                issue_id=entry.issue_id,
                identifier=entry.identifier,
                issue_state=entry.issue_state,
                reason=reason,
                error=error,
                started_at=entry.started_at,
                ended_at=datetime.now(timezone.utc),
                turn_count=entry.turn_count,
                session_id=entry.session_id,
            ))
            if len(self.recent_exits) > MAX_RECENT_EXITS:
                self.recent_exits = self.recent_exits[:MAX_RECENT_EXITS]

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
                "recent_exits": [e.to_dict() for e in self.recent_exits],
                "totals": totals,
            }

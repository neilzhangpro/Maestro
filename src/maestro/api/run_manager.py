"""In-memory run manager for tracking graph executions."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Run:
    id: str
    issue_id: str
    status: str = "pending"
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    current_node: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "issue_id": self.issue_id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "current_node": self.current_node,
            "result": self.result,
            "error": self.error,
            "logs": self.logs[-50:],
        }


class RunManager:
    """Track graph runs in memory and notify WebSocket subscribers."""

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

    def create_run(self, issue_id: str) -> Run:
        run = Run(
            id=str(uuid.uuid4()),
            issue_id=issue_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._runs[run.id] = run
        self._broadcast({"type": "run_created", "run": run.to_dict()})
        return run

    def update_run(self, run_id: str, **kwargs: Any) -> Run | None:
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return None
            for key, value in kwargs.items():
                if hasattr(run, key):
                    setattr(run, key, value)
        self._broadcast({"type": "run_updated", "run": run.to_dict()})
        return run

    def append_log(self, run_id: str, message: str) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run:
                run.logs.append(message)
        self._broadcast({"type": "run_log", "run_id": run_id, "message": message})

    def get_run(self, run_id: str) -> Run | None:
        with self._lock:
            return self._runs.get(run_id)

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [r.to_dict() for r in sorted(
                self._runs.values(),
                key=lambda r: r.created_at,
                reverse=True,
            )]

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _broadcast(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

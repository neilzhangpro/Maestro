"""Flow recorder — full tool-call-chain capture for Skill evolution.

Each completed issue run produces one :class:`FlowRecord` that captures every
tool invocation in order.  These records feed the :class:`FlowDistiller` which
clusters repeated workflows into new-Skill candidates.

Storage: ``{workspace_root}/.maestro/flow_history.jsonl`` (append-only).
"""

from __future__ import annotations

import fcntl
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_FLOW_FILE = "flow_history.jsonl"


@dataclass(frozen=True)
class FlowStep:
    """One tool invocation within an issue run."""

    turn: int
    """Agent turn number (1-based)."""

    seq: int
    """Position within the full run (0-based, monotonically increasing)."""

    tool_name: str
    """Normalised tool name (e.g. ``readToolCall``, ``Bash``)."""

    tool_path: str
    """File path or command snippet operated on (may be empty)."""

    duration_ms: int
    """Approximate wall-clock time for the step (0 if unknown)."""

    event_type: str
    """Source event type (``tool_start``, ``tool_end``, etc.)."""


@dataclass
class FlowRecord:
    """Full tool-call chain for a single completed issue run.

    A :class:`FlowRecord` is appended to ``flow_history.jsonl`` once per
    issue — after all turns are finished and the worker is about to exit.
    The ``steps`` list is ordered chronologically across all turns.
    """

    issue_identifier: str
    session_id: str
    timestamp_utc: str
    total_turns: int
    success: bool
    labels: list[str] = field(default_factory=list)
    steps: list[FlowStep] = field(default_factory=list)


class FlowRecorder:
    """Append-only JSONL store for :class:`FlowRecord` objects."""

    def __init__(self, store_dir: Path) -> None:
        self._store_dir = store_dir
        self._flow_path = store_dir / _FLOW_FILE

    def record(self, rec: FlowRecord) -> None:
        """Append *rec* as one JSON line (file-lock protected)."""
        self._store_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "issue_identifier": rec.issue_identifier,
            "session_id": rec.session_id,
            "timestamp_utc": rec.timestamp_utc,
            "total_turns": rec.total_turns,
            "success": rec.success,
            "labels": rec.labels,
            "steps": [asdict(s) for s in rec.steps],
        }
        line = json.dumps(data, ensure_ascii=False) + "\n"
        with open(self._flow_path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(line)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def load_recent(self, limit: int = 200) -> list[FlowRecord]:
        """Return the last *limit* :class:`FlowRecord` objects."""
        if not self._flow_path.exists():
            return []
        records: list[FlowRecord] = []
        try:
            with open(self._flow_path, encoding="utf-8") as fh:
                for raw_line in fh:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        data = json.loads(raw_line)
                        steps = [
                            FlowStep(**s) for s in data.get("steps", [])
                        ]
                        records.append(FlowRecord(
                            issue_identifier=data.get("issue_identifier", ""),
                            session_id=data.get("session_id", ""),
                            timestamp_utc=data.get("timestamp_utc", ""),
                            total_turns=data.get("total_turns", 0),
                            success=data.get("success", False),
                            labels=data.get("labels", []),
                            steps=steps,
                        ))
                    except (json.JSONDecodeError, TypeError, KeyError):
                        log.debug("Skipping malformed flow line: %s", raw_line[:80])
        except OSError:
            log.warning("Could not read flow history at %s", self._flow_path, exc_info=True)
            return []
        return records[-limit:]

    def load_successful(self, limit: int = 200) -> list[FlowRecord]:
        """Return only successful records (agent completed the issue)."""
        return [r for r in self.load_recent(limit) if r.success]

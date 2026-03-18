"""Execution history recorder — JSONL-based cross-run experience store.

Persists per-turn outcomes to a shared log that survives workspace
deletion.  The accumulated history is summarised and injected into
agent prompts so future runs can learn from past failures.
"""

from __future__ import annotations

import fcntl
import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_HISTORY_FILE = "run_history.jsonl"


@dataclass(frozen=True)
class RunRecord:
    """One turn's outcome — serialised as a single JSON line.

    Fields added in v2 (tool_sequence, files_changed, skill_refs, labels) are
    optional with empty-list defaults so that records written by older versions
    can still be loaded without error.
    """

    issue_identifier: str
    timestamp_utc: str
    turn: int
    attempt: int | None
    success: bool
    error: str | None
    duration_ms: int
    tools_used: list[str] = field(default_factory=list)
    output_summary: str = ""

    # v2 fields — richer telemetry for Skill evolution analysis
    tool_sequence: list[dict] = field(default_factory=list)
    """Ordered tool-call chain: [{"tool": "readToolCall", "path": "...", "ms": 0}, ...]"""

    files_changed: list[str] = field(default_factory=list)
    """Paths of files written/edited during this turn (from write/edit events)."""

    skill_refs: list[str] = field(default_factory=list)
    """Names of SKILL.md files the agent read during this turn."""

    labels: list[str] = field(default_factory=list)
    """Linear labels on the issue (for clustering analysis)."""

    session_id: str = ""
    """Agent session ID — links turn-level RunRecords to the corresponding FlowRecord."""

    rtk_stats: dict = field(default_factory=dict)
    """Optional RTK gain snapshot captured after the turn."""


class RunRecorder:
    """Append-only JSONL store under ``{store_dir}/run_history.jsonl``."""

    def __init__(self, store_dir: Path) -> None:
        self._store_dir = store_dir
        self._history_path = store_dir / _HISTORY_FILE

    def record(self, rec: RunRecord) -> None:
        """Append *rec* as one JSON line (file-lock protected)."""
        self._store_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(rec), ensure_ascii=False) + "\n"
        with open(self._history_path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(line)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def load_recent(self, limit: int = 50) -> list[RunRecord]:
        """Read the last *limit* records from the history file.

        Old records missing v2 fields are accepted; the dataclass defaults fill
        in empty lists so callers can treat all records uniformly.
        """
        if not self._history_path.exists():
            return []
        records: list[RunRecord] = []
        _v2_fields = {
            "tool_sequence", "files_changed", "skill_refs", "labels", "session_id", "rtk_stats",
        }
        try:
            with open(self._history_path, encoding="utf-8") as fh:
                for raw_line in fh:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        data = json.loads(raw_line)
                        # Back-fill missing v2 fields with their defaults
                        for f in _v2_fields:
                            if f == "session_id":
                                data.setdefault(f, "")
                            elif f == "rtk_stats":
                                data.setdefault(f, {})
                            else:
                                data.setdefault(f, [])
                        records.append(RunRecord(**data))
                    except (json.JSONDecodeError, TypeError):
                        log.debug("Skipping malformed history line: %s", raw_line[:80])
        except OSError:
            log.warning("Could not read history at %s", self._history_path, exc_info=True)
            return []
        return records[-limit:]

    def build_learning_context(self, limit: int = 30) -> str:
        """Summarise recent history into a compact Markdown block.

        Returns an empty string when no history exists.
        """
        records = self.load_recent(limit)
        if not records:
            return ""

        total = len(records)
        successes = sum(1 for r in records if r.success)
        failures = total - successes

        lines: list[str] = []
        lines.append(f"**Run overview** (last {total} turns): "
                      f"{successes} succeeded, {failures} failed "
                      f"({_pct(successes, total)} success rate)")

        if failures:
            error_counts: Counter[str] = Counter()
            for r in records:
                if not r.success and r.error:
                    error_counts[r.error] += 1
            if error_counts:
                lines.append("")
                lines.append("**Top failure patterns:**")
                for err, cnt in error_counts.most_common(5):
                    lines.append(f"- `{err}` — {cnt} occurrence(s)")

        tool_counts: Counter[str] = Counter()
        for r in records:
            for t in r.tools_used:
                tool_counts[t] += 1
        if tool_counts:
            top_tools = [name for name, _ in tool_counts.most_common(8)]
            lines.append("")
            lines.append(f"**Most-used tools:** {', '.join(top_tools)}")

        failed_with_output = [
            r for r in records
            if not r.success and r.output_summary
        ]
        if failed_with_output:
            last_fail = failed_with_output[-1]
            lines.append("")
            lines.append(
                f"**Last failure excerpt** ({last_fail.issue_identifier}, "
                f"turn {last_fail.turn}): {last_fail.output_summary[:200]}"
            )

        return "\n".join(lines)


def _pct(part: int, whole: int) -> str:
    if whole == 0:
        return "N/A"
    return f"{part * 100 // whole}%"

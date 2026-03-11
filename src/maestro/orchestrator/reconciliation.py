"""Active-run reconciliation: stall detection + tracker state refresh (SPEC §8.5)."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from maestro.linear.client import LinearClient
from maestro.orchestrator.state import OrchestratorState

if TYPE_CHECKING:
    from maestro.workflow.config import ServiceConfig

log = logging.getLogger(__name__)


class Reconciler:
    """Check running issues for stalls and tracker state changes."""

    def __init__(
        self,
        state: OrchestratorState,
        config: "ServiceConfig",
        linear: LinearClient,
        *,
        on_terminate: callable = None,
    ) -> None:
        self._state = state
        self.config = config
        self._linear = linear
        self._on_terminate = on_terminate

    def reconcile(self) -> None:
        self._check_stalls()
        self._refresh_tracker_states()

    def _check_stalls(self) -> None:
        stall_ms = self.config.cursor.stall_timeout_ms
        if stall_ms <= 0:
            return

        now = datetime.now(timezone.utc)
        for issue_id in self._state.running_ids():
            entry = self._state.running.get(issue_id)
            if not entry:
                continue

            ref_time = entry.last_event_at or entry.started_at
            elapsed_ms = (now - ref_time).total_seconds() * 1000

            if elapsed_ms > stall_ms:
                log.warning(
                    "Stalled: %s (%.0fms since last activity)", entry.identifier, elapsed_ms,
                )
                if self._on_terminate:
                    self._on_terminate(issue_id, "stalled", cleanup=False)

    def _refresh_tracker_states(self) -> None:
        running_ids = self._state.running_ids()
        if not running_ids:
            return

        try:
            refreshed = self._linear.fetch_issue_states_by_ids(running_ids)
        except Exception:
            log.warning("Tracker state refresh failed — keeping workers.", exc_info=True)
            return

        state_map = {i.id: i.state for i in refreshed}
        terminal = {s.strip().lower() for s in self.config.tracker.terminal_states}
        active = {s.strip().lower() for s in self.config.tracker.active_states}
        handoff = {s.strip().lower() for s in self.config.tracker.handoff_states}

        for issue_id in running_ids:
            current_state = state_map.get(issue_id)
            if current_state is None:
                continue

            normalized = current_state.strip().lower()
            entry = self._state.running.get(issue_id)

            if normalized in terminal:
                log.info(
                    "Issue %s moved to terminal state '%s' — terminating.",
                    entry.identifier if entry else issue_id, current_state,
                )
                if self._on_terminate:
                    self._on_terminate(issue_id, "terminal", cleanup=True)
            elif normalized in handoff:
                log.info(
                    "Issue %s moved to handoff state '%s' — stopping worker, keeping workspace.",
                    entry.identifier if entry else issue_id, current_state,
                )
                if self._on_terminate:
                    self._on_terminate(issue_id, "handoff", cleanup=False)
            elif normalized in active:
                if entry:
                    entry.issue_state = current_state
            else:
                log.info(
                    "Issue %s moved to non-active state '%s' — terminating (no cleanup).",
                    entry.identifier if entry else issue_id, current_state,
                )
                if self._on_terminate:
                    self._on_terminate(issue_id, "non_active", cleanup=False)

    def cleanup_workspace(self, identifier: str) -> None:
        """Remove a workspace directory for a terminal issue."""
        root = self.config.workspace.root
        from maestro.workspace.manager import sanitize_workspace_key
        key = sanitize_workspace_key(identifier)
        ws_path = root / key
        if ws_path.exists():
            log.info("Cleaning up workspace: %s", ws_path)
            shutil.rmtree(ws_path, ignore_errors=True)

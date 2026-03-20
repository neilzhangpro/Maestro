"""Active-run reconciliation: stall detection + tracker state refresh (SPEC §8.5)."""

from __future__ import annotations

import logging
import os
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from maestro.linear.client import LinearClient
from maestro.orchestrator.state import OrchestratorState
from maestro.workspace.manager import sanitize_workspace_key

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
        if self.config.backend == "claude_code" and self.config.claude_code:
            stall_ms = self.config.claude_code.stall_timeout_ms
        else:
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
                if entry and normalized == "human review":
                    self._create_handoff_comment(issue_id, entry.identifier)
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

    def ensure_handoff_for_issue(self, issue_id: str, identifier: str) -> None:
        """Move an active issue to the first configured handoff state and add a handoff comment."""
        try:
            issue = self._linear.fetch_issue(issue_id)
        except Exception:
            log.warning("Failed to fetch issue for handoff: %s", identifier, exc_info=True)
            return

        active = {s.strip().lower() for s in self.config.tracker.active_states}
        current_state = issue.state.strip().lower()
        if current_state not in active:
            return

        target_state = next(iter(self.config.tracker.handoff_states), "Human Review")
        if not issue.team_key:
            log.warning("Issue %s has no team key; cannot move to handoff.", identifier)
            return

        state_id = self._linear.find_state_id(issue.team_key, target_state)
        if not state_id:
            log.warning("State '%s' not found for team %s.", target_state, issue.team_key)
            return

        try:
            self._linear.update_issue_state(issue.id, state_id)
        except Exception:
            log.warning("Failed to move %s to handoff state %s", identifier, target_state, exc_info=True)
            return

        if target_state.strip().lower() == "human review":
            self._create_handoff_comment(issue.id, identifier)

    def cleanup_workspace(self, identifier: str) -> None:
        """Remove a workspace directory for a terminal issue."""
        root = self.config.workspace.root
        key = sanitize_workspace_key(identifier)
        ws_path = root / key
        if ws_path.exists():
            log.info("Cleaning up workspace: %s", ws_path)
            shutil.rmtree(ws_path, ignore_errors=True)

    def _create_handoff_comment(self, issue_id: str, identifier: str) -> None:
        """Leave a deterministic Human Review handoff comment on Linear."""
        try:
            body = self._build_handoff_comment(identifier)
            self._linear.create_comment(issue_id, body)
        except Exception:
            log.warning("Failed to create Human Review handoff comment for %s", identifier, exc_info=True)

    def _build_handoff_comment(self, identifier: str) -> str:
        key = sanitize_workspace_key(identifier)
        container_path = (Path(self.config.workspace.root) / key).resolve()
        host_root = os.environ.get("MAESTRO_HOST_WORKSPACE_ROOT", "").strip()
        if host_root:
            host_path = (Path(host_root).expanduser() / key).resolve()
        else:
            host_path = container_path

        commands = self._detect_review_commands(host_path)
        command_lines = "\n".join(f"- `{cmd}`" for cmd in commands)

        return (
            "**Human Review Handoff**\n\n"
            f"- **Workspace**: `{host_path}`\n"
            f"- **Issue**: `{identifier}`\n"
            "- **Status**: Code remains uncommitted for manual review.\n\n"
            "**Suggested review flow**\n"
            "- Open the workspace locally.\n"
            "- Review `git status` and `git diff` before running the app.\n"
            "- Run the project locally for manual verification / E2E.\n\n"
            "**Suggested commands**\n"
            f"{command_lines}\n"
        )

    def _detect_review_commands(self, workspace_path: Path) -> list[str]:
        commands = [
            f"cd {workspace_path}",
            "git status",
            "git diff --stat",
            "git diff",
        ]

        package_json = workspace_path / "package.json"
        if package_json.exists():
            pm = "pnpm" if (workspace_path / "pnpm-lock.yaml").exists() else (
                "yarn" if (workspace_path / "yarn.lock").exists() else "npm"
            )
            scripts: dict[str, object] = {}
            try:
                scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {}) or {}
            except Exception:
                scripts = {}

            install_cmd = {"pnpm": "pnpm install", "yarn": "yarn install", "npm": "npm install"}[pm]
            commands.append(install_cmd)
            for script in ("dev", "start", "test", "test:e2e", "e2e", "playwright"):
                if script in scripts:
                    if pm == "yarn":
                        commands.append(f"yarn {script}")
                    elif pm == "pnpm":
                        commands.append(f"pnpm {script}")
                    else:
                        commands.append(f"npm run {script}")

        pyproject = workspace_path / "pyproject.toml"
        if pyproject.exists():
            if (workspace_path / "uv.lock").exists():
                commands.append("uv sync")
            elif (workspace_path / "requirements.txt").exists():
                commands.append("pip install -r requirements.txt")

            makefile = workspace_path / "Makefile"
            if makefile.exists():
                try:
                    text = makefile.read_text(encoding="utf-8")
                except Exception:
                    text = ""
                for target in ("dev", "test", "e2e"):
                    if f"{target}:" in text:
                        commands.append(f"make {target}")

        deduped: list[str] = []
        seen: set[str] = set()
        for cmd in commands:
            if cmd not in seen:
                deduped.append(cmd)
                seen.add(cmd)
        return deduped[:10]

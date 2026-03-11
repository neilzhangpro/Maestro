"""Worker — workspace + prompt + multi-turn agent execution for a single issue.

A Worker runs in its own thread, managed by the Orchestrator.  It:
1. Prepares the workspace and runs hooks.
2. Loops through up to ``max_turns`` agent sessions.
3. Between turns, re-checks the issue state via the tracker.
4. Reports events and exit status back to the Orchestrator.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from maestro.agent.events import AgentEvent
from maestro.agent.headless import HeadlessRunner
from maestro.linear.client import LinearClient
from maestro.linear.models import Issue
from maestro.workflow.config import ServiceConfig, TrackerConfig
from maestro.workflow.template import TemplateRenderError, render_prompt
from maestro.workspace.hooks import ShellHooks
from maestro.workspace.manager import WorkspaceManager

log = logging.getLogger(__name__)


class WorkerError(RuntimeError):
    """Raised when a worker fails fatally."""


class Worker:
    """Execute one issue through the agent lifecycle."""

    def __init__(
        self,
        config: ServiceConfig,
        issue: Issue,
        attempt: int | None,
        on_event: Callable[[str, AgentEvent], None],
        on_exit: Callable[[str, str, str | None], None],
    ) -> None:
        self.config = config
        self.issue = issue
        self.attempt = attempt
        self._on_event = on_event
        self._on_exit = on_exit

        hooks = ShellHooks(
            after_create_script=config.hooks.after_create,
            before_run_script=config.hooks.before_run,
            after_run_script=config.hooks.after_run,
            before_remove_script=config.hooks.before_remove,
            timeout_ms=config.hooks.timeout_ms,
        )
        self._workspace_mgr = WorkspaceManager(config.workspace.root, hooks=hooks)
        self._runner = HeadlessRunner(config.cursor)

    def run(self) -> None:
        """Main entry point — runs in a dedicated thread."""
        issue_id = self.issue.id
        identifier = self.issue.identifier

        try:
            workspace = self._workspace_mgr.prepare_workspace(identifier)
            self._workspace_mgr.run_before(workspace)

            session_id: str | None = None
            current_issue = self.issue
            max_turns = self.config.agent.max_turns

            for turn in range(1, max_turns + 1):
                log.info(
                    "Worker %s: turn %d/%d (session=%s)",
                    identifier, turn, max_turns, session_id or "new",
                )

                prompt = self._build_prompt(current_issue, self.attempt, turn, max_turns)

                result = self._runner.run_turn(
                    workspace=workspace.path,
                    prompt=prompt,
                    resume_session_id=session_id,
                    on_event=lambda e, iid=issue_id: self._on_event(iid, e),
                )

                if not session_id:
                    session_id = result.session_id

                if not result.success:
                    log.warning(
                        "Worker %s: turn %d failed: %s",
                        identifier, turn, result.error,
                    )
                    self._run_after_hook(workspace.path)
                    self._on_exit(issue_id, "abnormal", result.error)
                    return

                log.info(
                    "Worker %s: turn %d completed (%dms)",
                    identifier, turn, result.duration_ms,
                )

                if turn >= max_turns:
                    break

                refreshed = self._refresh_issue_state(issue_id)
                if refreshed is None:
                    self._run_after_hook(workspace.path)
                    self._on_exit(issue_id, "abnormal", "issue_state_refresh_failed")
                    return

                current_issue = refreshed
                active = {s.strip().lower() for s in self.config.tracker.active_states}
                if current_issue.state.strip().lower() not in active:
                    log.info(
                        "Worker %s: issue moved to '%s' — stopping.",
                        identifier, current_issue.state,
                    )
                    break

            self._run_after_hook(workspace.path)
            self._on_exit(issue_id, "normal", None)

        except Exception as exc:
            log.exception("Worker %s: unexpected error", self.issue.identifier)
            self._on_exit(issue_id, "abnormal", str(exc))

    def _build_prompt(
        self, issue: Issue, attempt: int | None, turn: int, max_turns: int,
    ) -> str:
        if turn == 1:
            try:
                return render_prompt(
                    self.config.prompt_template,
                    issue=issue.to_template_dict(),
                    attempt=attempt,
                )
            except TemplateRenderError:
                log.exception("Template rendering failed — using fallback prompt.")
                return (
                    f"You are working on issue {issue.identifier}: {issue.title}\n\n"
                    f"{issue.description or '(no description)'}"
                )
        return (
            f"Continue working on {issue.identifier}: {issue.title}. "
            f"The issue is still in '{issue.state}' state. "
            f"Turn {turn}/{max_turns}. "
            f"Review your prior work and complete any remaining tasks."
        )

    def _refresh_issue_state(self, issue_id: str) -> Issue | None:
        try:
            tracker_cfg = self.config.tracker
            with LinearClient(_to_linear_config(tracker_cfg)) as client:
                states = client.fetch_issue_states_by_ids([issue_id])
                if not states:
                    return None
                mini = states[0]
                return Issue(
                    id=mini.id,
                    identifier=mini.identifier or self.issue.identifier,
                    title=self.issue.title,
                    description=self.issue.description,
                    state=mini.state,
                    state_id=mini.state_id,
                    team_key=self.issue.team_key,
                    priority=self.issue.priority,
                    labels=self.issue.labels,
                    url=self.issue.url,
                )
        except Exception:
            log.warning("Failed to refresh issue state for %s", issue_id, exc_info=True)
            return None

    def _run_after_hook(self, workspace_path: Path) -> None:
        try:
            from maestro.workspace.manager import Workspace
            ws = Workspace(path=workspace_path, created_now=False, key=workspace_path.name)
            self._workspace_mgr.run_after(ws)
        except Exception:
            log.warning("after_run hook failed", exc_info=True)


def _to_linear_config(t: TrackerConfig):
    """Adapt TrackerConfig → legacy LinearConfig for the existing client."""
    from maestro.config import LinearConfig
    return LinearConfig(
        api_key=t.api_key,
        api_url=t.endpoint,
        project_slug=t.project_slug or None,
        team_id=t.team_id,
        assignee=t.assignee,
        active_states=t.active_states,
        terminal_states=t.terminal_states,
        timeout_s=t.timeout_s,
    )

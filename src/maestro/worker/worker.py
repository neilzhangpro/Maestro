"""Worker — workspace + prompt + multi-turn agent execution for a single issue.

A Worker runs in its own thread, managed by the Orchestrator.  It:
1. Prepares the workspace and runs hooks.
2. Loops through up to ``max_turns`` agent sessions.
3. Between turns, re-checks the issue state via the tracker.
4. Reports events and exit status back to the Orchestrator.
"""

from __future__ import annotations

import logging
import json
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from maestro.agent.events import AgentEvent
from maestro.agent.headless import HeadlessRunner
from maestro.learning.flow_recorder import FlowRecord, FlowRecorder, FlowStep
from maestro.learning.recorder import RunRecord, RunRecorder
from maestro.linear.client import LinearClient
from maestro.linear.models import Issue
from maestro.workflow.config import ServiceConfig, TrackerConfig
from maestro.workflow.template import TemplateRenderError, compose_agent_prompt, render_prompt
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
        self._cancel_event = threading.Event()

        resolved = config.resolved_hooks()
        hooks = ShellHooks(
            after_create_script=resolved.after_create,
            before_run_script=resolved.before_run,
            after_run_script=resolved.after_run,
            before_remove_script=resolved.before_remove,
            timeout_ms=resolved.timeout_ms,
        )
        self._workspace_mgr = WorkspaceManager(config.workspace.root, hooks=hooks)

        if config.backend == "claude_code" and config.claude_code:
            from maestro.agent.claude_code import ClaudeCodeRunner
            self._runner = ClaudeCodeRunner(config.claude_code)
        else:
            self._runner = HeadlessRunner(config.cursor)
        _store = config.workspace.root / ".maestro"
        self._recorder = RunRecorder(_store)
        self._flow_recorder = FlowRecorder(_store)
        # Accumulated tool-call steps for the current run (reset each run)
        self._flow_steps: list[FlowStep] = []
        self._flow_seq: int = 0
        self._linear = LinearClient.from_tracker_config(config.tracker)

    def cancel(self) -> None:
        """Request cancellation — kills the agent subprocess and stops the run loop."""
        log.info("Worker %s: cancel requested.", self.issue.identifier)
        self._cancel_event.set()
        self._runner.kill_current_process()

    def run(self) -> None:
        """Main entry point — runs in a dedicated thread."""
        issue_id = self.issue.id
        identifier = self.issue.identifier

        # Reset per-run flow tracking state
        self._flow_steps = []
        self._flow_seq = 0

        exit_reason = "abnormal"
        try:
            workspace = self._workspace_mgr.prepare_workspace(identifier)
            self._workspace_mgr.run_before(workspace)

            session_id: str | None = None
            current_issue = self.issue
            max_turns = self.config.agent.max_turns
            last_turn = 0

            for turn in range(1, max_turns + 1):
                last_turn = turn
                if self._cancel_event.is_set():
                    log.info("Worker %s: cancelled before turn %d.", identifier, turn)
                    self._run_after_hook(workspace.path)
                    self._record_flow(identifier, session_id, turn, success=False)
                    self._on_exit(issue_id, "abnormal", "cancelled_by_user")
                    return

                log.info(
                    "Worker %s: turn %d/%d (session=%s)",
                    identifier, turn, max_turns, session_id or "new",
                )

                prompt = self._build_prompt(current_issue, self.attempt, turn, max_turns)

                turn_tools: list[str] = []
                turn_seq: list[dict] = []
                files_changed_set: set[str] = set()
                skill_refs: list[str] = []

                current_turn = turn  # capture for closure

                def _on_event_wrapper(
                    e: AgentEvent,
                    iid: str = issue_id,
                    _turn: int = current_turn,
                ) -> None:
                    if e.tool_name:
                        turn_tools.append(e.tool_name)
                        step = FlowStep(
                            turn=_turn,
                            seq=self._flow_seq,
                            tool_name=e.tool_name,
                            tool_path=e.tool_path,
                            duration_ms=e.duration_ms,
                            event_type=e.event,
                        )
                        self._flow_steps.append(step)
                        self._flow_seq += 1
                        # Include event type so trajectories distinguish
                        # initiated vs. completed tool calls
                        turn_seq.append({
                            "tool": e.tool_name,
                            "path": e.tool_path,
                            "ms": e.duration_ms,
                            "event": e.event,
                        })
                        # Track confirmed writes (tool_end = write completed)
                        _write_tools = {
                            "writeToolCall", "Write",
                            "EditToolCall", "Edit", "MultiEdit",
                            "StrReplace",
                        }
                        if (
                            e.tool_path
                            and e.event in ("tool_end", "tool_start")
                            and e.tool_name in _write_tools
                        ):
                            files_changed_set.add(e.tool_path)
                        # Track SKILL.md references (any event touching the file)
                        if e.tool_path and "SKILL.md" in e.tool_path:
                            skill_name = _extract_skill_name(e.tool_path)
                            if skill_name and skill_name not in skill_refs:
                                skill_refs.append(skill_name)
                    self._on_event(iid, e)

                if self.config.backend == "claude_code" and self.config.claude_code:
                    plan_model = self.config.claude_code.plan_model
                else:
                    plan_model = self.config.cursor.plan_model
                model_override = plan_model if (turn == 1 and plan_model) else None
                result = self._runner.run_turn(
                    workspace=workspace.path,
                    prompt=prompt,
                    resume_session_id=session_id,
                    on_event=_on_event_wrapper,
                    model_override=model_override,
                    cancel_event=self._cancel_event,
                )

                self._record_turn(
                    identifier, turn, result, turn_tools,
                    session_id=session_id or result.session_id or "",
                    tool_sequence=turn_seq,
                    files_changed=sorted(files_changed_set),
                    skill_refs=skill_refs,
                    labels=current_issue.labels,
                    rtk_stats=self._collect_rtk_stats(workspace.path),
                )

                if not session_id:
                    session_id = result.session_id

                if not result.success:
                    log.warning(
                        "Worker %s: turn %d failed: %s",
                        identifier, turn, result.error,
                    )
                    self._run_after_hook(workspace.path)
                    self._record_flow(identifier, session_id, turn, success=False)
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
                    self._record_flow(identifier, session_id, turn, success=False)
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
            self._record_flow(identifier, session_id, last_turn, success=True)
            self._on_exit(issue_id, "normal", None)

        except Exception as exc:
            log.exception("Worker %s: unexpected error", self.issue.identifier)
            self._on_exit(issue_id, "abnormal", str(exc))
        finally:
            self._linear.close()

    def _build_prompt(
        self, issue: Issue, attempt: int | None, turn: int, max_turns: int,
    ) -> str:
        learning_context = self._recorder.build_learning_context()

        if turn == 1:
            try:
                return compose_agent_prompt(
                    render_prompt(
                        self.config.prompt_template,
                        issue=issue.to_template_dict(),
                        attempt=attempt,
                        learning_context=learning_context or None,
                        backend=self.config.backend,
                    )
                )
            except TemplateRenderError:
                log.exception("Template rendering failed — using fallback prompt.")
                return compose_agent_prompt(
                    f"You are working on issue {issue.identifier}: {issue.title}\n\n"
                    f"{issue.description or '(no description)'}"
                )

        continuation = (
            f"Continue working on {issue.identifier}: {issue.title}. "
            f"The issue is still in '{issue.state}' state. "
            f"Turn {turn}/{max_turns}. "
            f"Review your prior work and complete any remaining tasks."
        )
        if learning_context:
            continuation += f"\n\n## Execution History Insights\n{learning_context}"
        return compose_agent_prompt(continuation)

    def _record_turn(
        self,
        identifier: str,
        turn: int,
        result: "TurnResult",
        turn_tools: list[str],
        *,
        session_id: str = "",
        tool_sequence: list[dict] | None = None,
        files_changed: list[str] | None = None,
        skill_refs: list[str] | None = None,
        labels: list[str] | None = None,
        rtk_stats: dict | None = None,
    ) -> None:
        """Persist one turn's outcome to the shared execution history."""
        try:
            self._recorder.record(RunRecord(
                issue_identifier=identifier,
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                turn=turn,
                attempt=self.attempt,
                success=result.success,
                error=result.error,
                duration_ms=result.duration_ms,
                tools_used=sorted(set(turn_tools)),
                output_summary=(result.output_text or "")[:300],
                session_id=session_id,
                tool_sequence=tool_sequence or [],
                files_changed=files_changed or [],
                skill_refs=skill_refs or [],
                labels=labels or [],
                rtk_stats=rtk_stats or {},
            ))
        except Exception:
            log.warning("Failed to record turn %d for %s", turn, identifier, exc_info=True)

    def _record_flow(
        self,
        identifier: str,
        session_id: str | None,
        total_turns: int,
        *,
        success: bool,
    ) -> None:
        """Persist the full tool-call chain for this run as a FlowRecord."""
        try:
            self._flow_recorder.record(FlowRecord(
                issue_identifier=identifier,
                session_id=session_id or "",
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                total_turns=total_turns,
                success=success,
                labels=self.issue.labels,
                steps=list(self._flow_steps),
            ))
        except Exception:
            log.warning("Failed to record flow for %s", identifier, exc_info=True)

    def _refresh_issue_state(self, issue_id: str) -> Issue | None:
        try:
            states = self._linear.fetch_issue_states_by_ids([issue_id])
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

    def _collect_rtk_stats(self, workspace_path: Path) -> dict:
        """Return an RTK gain snapshot when enabled and available."""
        rtk_cfg = getattr(self.config, "rtk", None)
        if not rtk_cfg or not rtk_cfg.enabled:
            return {}
        if self.config.backend != "claude_code":
            return {}
        binary = shutil.which(rtk_cfg.binary)
        if not binary:
            return {}
        try:
            result = subprocess.run(
                [binary, "gain", "--all", "--format", "json"],
                cwd=str(workspace_path),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            log.debug("Failed to collect RTK stats", exc_info=True)
            return {}

        if result.returncode != 0 or not result.stdout.strip():
            return {}

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            log.debug("RTK gain output was not valid JSON: %r", result.stdout[:120])
            return {}

        if not isinstance(payload, dict):
            return {}

        return payload


def _extract_skill_name(tool_path: str) -> str | None:
    """Return the skill directory name from a path containing SKILL.md.

    E.g. ``.cursor/skills/git-branch-sync/SKILL.md`` → ``git-branch-sync``.
    """
    parts = tool_path.replace("\\", "/").split("/")
    try:
        idx = next(i for i, p in enumerate(parts) if p == "SKILL.md")
        if idx > 0:
            return parts[idx - 1]
    except StopIteration:
        pass
    return None



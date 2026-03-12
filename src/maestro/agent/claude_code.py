"""Claude Code CLI runner — alternative agent execution backend.

Launches ``claude -p <prompt> --output-format stream-json`` as a subprocess,
reads the NDJSON event stream, and returns a structured :class:`TurnResult`.
Supports multi-turn via ``--resume <session_id>``.

Stream-json format differences from Cursor:
- Tool invocations are content blocks inside ``assistant`` messages:
  ``{"type": "assistant", "message": {"content": [{"type": "tool_use", ...}]}}``.
  (Cursor uses a top-level ``{"type": "tool_call", ...}`` instead.)
- Result ``subtype`` may be ``"success"`` or ``"completion"`` (both treated as success).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from maestro.agent.events import AgentEvent, normalize_events, _is_user_input_required
from maestro.agent.headless import TurnResult
from maestro.workflow.config import ClaudeCodeConfig

log = logging.getLogger(__name__)


class ClaudeCodeRunner:
    """Execute a single turn via the Claude Code CLI (``claude -p``)."""

    def __init__(self, config: ClaudeCodeConfig) -> None:
        self.config = config
        self._process: subprocess.Popen[str] | None = None
        self._process_lock = threading.Lock()

    def run_turn(
        self,
        *,
        workspace: Path,
        prompt: str,
        resume_session_id: str | None = None,
        on_event: Callable[[AgentEvent], None] | None = None,
        model_override: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> TurnResult:
        cmd = self._build_command(workspace, prompt, resume_session_id, model_override)
        env = self._build_env()

        log.info("Launching Claude Code agent in %s", workspace)
        log.debug("Command: %s", " ".join(cmd))

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            cwd=str(workspace),
        )
        with self._process_lock:
            self._process = process

        try:
            return self._stream_until_done(process, on_event, cancel_event)
        finally:
            with self._process_lock:
                self._process = None

    def kill_current_process(self) -> None:
        """Kill the currently running agent subprocess (if any)."""
        with self._process_lock:
            proc = self._process
        if proc and proc.poll() is None:
            log.info("Killing Claude Code subprocess pid=%d", proc.pid)
            proc.kill()

    # ------------------------------------------------------------------
    # Command construction
    # ------------------------------------------------------------------

    def _build_command(
        self,
        workspace: Path,
        prompt: str,
        resume_id: str | None,
        model_override: str | None = None,
    ) -> list[str]:
        executable = self._resolve_executable()

        cmd = [executable, "-p", prompt]
        cmd.extend(["--output-format", "stream-json"])
        cmd.extend(["--verbose", "--include-partial-messages"])

        effective_model = model_override or self.config.model
        if effective_model:
            cmd.extend(["--model", effective_model])

        if self.config.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        elif self.config.allowed_tools:
            cmd.extend(["--allowedTools", ",".join(self.config.allowed_tools)])

        if self.config.max_turns_per_invocation > 0:
            cmd.extend(["--max-turns", str(self.config.max_turns_per_invocation)])

        if self.config.max_budget_usd > 0:
            cmd.extend(["--max-budget-usd", f"{self.config.max_budget_usd:.2f}"])

        if self.config.append_system_prompt:
            cmd.extend(["--append-system-prompt", self.config.append_system_prompt])

        if resume_id:
            cmd.extend(["--resume", resume_id])

        return cmd

    def _resolve_executable(self) -> str:
        cmd = self.config.command
        resolved = shutil.which(cmd)
        if resolved:
            return resolved
        raise FileNotFoundError(
            f"Could not find executable: {cmd!r}. "
            "Install Claude Code with: npm install -g @anthropic-ai/claude-code"
        )

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.config.api_key:
            env["ANTHROPIC_API_KEY"] = self.config.api_key
        return env

    # ------------------------------------------------------------------
    # NDJSON stream processing
    # ------------------------------------------------------------------

    def _stream_until_done(
        self,
        process: subprocess.Popen[str],
        on_event: Callable[[AgentEvent], None] | None,
        cancel_event: threading.Event | None = None,
    ) -> TurnResult:
        import json

        session_id = ""
        last_activity = time.monotonic()
        start_time = time.monotonic()
        stall_timeout_s = self.config.stall_timeout_ms / 1000
        turn_timeout_s = self.config.turn_timeout_ms / 1000
        output_parts: list[str] = []

        assert process.stdout is not None

        for line in process.stdout:
            stripped = line.strip()
            if not stripped:
                continue

            last_activity = time.monotonic()

            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue

            self._forward_event(event, on_event)

            etype = event.get("type")

            if etype == "system" and event.get("subtype") == "init":
                session_id = event.get("session_id", "")
                log.info(
                    "Claude Code session started: sid=%s model=%s",
                    session_id,
                    event.get("model", "?"),
                )

            elif etype == "assistant":
                for c in event.get("message", {}).get("content", []):
                    if c.get("type") == "text":
                        output_parts.append(c["text"])

            elif etype == "result":
                self._wait_process(process)
                return TurnResult(
                    session_id=event.get("session_id", session_id),
                    duration_ms=event.get("duration_ms", 0),
                    success=(event.get("subtype") in ("success", "completion")),
                    output_text="".join(output_parts),
                )

            if _is_user_input_required(event):
                log.warning("Claude Code requested user input — killing process (hard failure).")
                process.kill()
                return TurnResult(
                    session_id=session_id,
                    duration_ms=0,
                    success=False,
                    output_text="".join(output_parts),
                    error="turn_input_required",
                )

            if _check_stall(last_activity, stall_timeout_s):
                log.warning("Claude Code stalled — killing process.")
                process.kill()
                return TurnResult(
                    session_id=session_id,
                    duration_ms=0,
                    success=False,
                    output_text="".join(output_parts),
                    error="stall_timeout",
                )

            if _check_turn_timeout(start_time, turn_timeout_s):
                log.warning("Claude Code turn timeout — killing process.")
                process.kill()
                return TurnResult(
                    session_id=session_id,
                    duration_ms=0,
                    success=False,
                    output_text="".join(output_parts),
                    error="turn_timeout",
                )

            if cancel_event and cancel_event.is_set():
                log.warning("Cancel requested — killing Claude Code process.")
                process.kill()
                return TurnResult(
                    session_id=session_id,
                    duration_ms=0,
                    success=False,
                    output_text="".join(output_parts),
                    error="cancelled_by_user",
                )

        self._wait_process(process)
        stderr_tail = _read_stderr(process)
        return TurnResult(
            session_id=session_id,
            duration_ms=int((time.monotonic() - start_time) * 1000),
            success=(process.returncode == 0),
            output_text="".join(output_parts),
            error=(
                f"process_exit({process.returncode}): {stderr_tail}"
                if process.returncode
                else None
            ),
        )

    @staticmethod
    def _forward_event(
        raw: dict,
        on_event: Callable[[AgentEvent], None] | None,
    ) -> None:
        if on_event is None:
            return
        for evt in normalize_events(raw):
            on_event(evt)

    @staticmethod
    def _wait_process(process: subprocess.Popen[str]) -> None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def _check_stall(last_activity: float, timeout_s: float) -> bool:
    if timeout_s <= 0:
        return False
    return (time.monotonic() - last_activity) > timeout_s


def _check_turn_timeout(start: float, timeout_s: float) -> bool:
    if timeout_s <= 0:
        return False
    return (time.monotonic() - start) > timeout_s


def _read_stderr(process: subprocess.Popen[str]) -> str:
    if process.stderr is None:
        return ""
    try:
        return process.stderr.read().strip()[-500:]
    except Exception:
        return ""

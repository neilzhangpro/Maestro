"""Claude Code CLI runner — alternative agent execution backend.

Launches ``claude -p --output-format stream-json`` as a subprocess,
reads the NDJSON event stream, and returns a structured :class:`TurnResult`.
Supports multi-turn via ``--resume <session_id>``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from maestro.agent.events import AgentEvent, normalize_events, _is_user_input_required
from maestro.agent.headless import TurnResult
from maestro.workflow.config import ClaudeCodeConfig

log = logging.getLogger(__name__)


class ClaudeCodeRunner:
    """Execute a single turn via the Claude Code CLI."""

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
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            cwd=str(workspace),
        )
        with self._process_lock:
            self._process = process

        # Feed prompt via stdin for robustness with long/complex prompts,
        # then close stdin so the CLI knows input is complete.
        assert process.stdin is not None
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except BrokenPipeError:
            log.warning("Claude Code process closed stdin early.")

        try:
            return self._stream_until_done(process, on_event, cancel_event)
        finally:
            with self._process_lock:
                self._process = None

    def kill_current_process(self) -> None:
        """Kill the currently running Claude Code subprocess (if any)."""
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
        cmd = [executable, "-p", "--verbose"]

        cmd.extend(["--output-format", "stream-json"])

        effective_model = model_override or self.config.model
        if effective_model:
            cmd.extend(["--model", effective_model])

        if self.config.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        elif self.config.allowed_tools:
            for tool in self.config.allowed_tools:
                cmd.extend(["--allowedTools", tool])

        if self.config.max_turns_per_invocation > 0:
            cmd.extend(["--max-turns", str(self.config.max_turns_per_invocation)])

        if self.config.max_budget_usd > 0:
            cmd.extend(["--max-budget-usd", f"{self.config.max_budget_usd:.2f}"])

        if self.config.append_system_prompt:
            cmd.extend(["--append-system-prompt", self.config.append_system_prompt])

        if resume_id:
            cmd.extend(["--resume", resume_id])

        # Prompt is passed via stdin (not as a positional arg) to handle
        # long/complex prompts reliably. See run_turn().
        return cmd

    def _resolve_executable(self) -> str:
        cmd = self.config.command
        if shutil.which(cmd):
            return cmd
        for candidate in (
            "/usr/local/bin/claude",
            "/opt/homebrew/bin/claude",
        ):
            if Path(candidate).exists():
                return candidate
        raise FileNotFoundError(
            f"Could not find Claude Code executable: {cmd}. "
            "Install with: npm install -g @anthropic-ai/claude-code"
        )

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        api_key = self.config.api_key or env.get("ANTHROPIC_API_KEY")
        if api_key:
            env["ANTHROPIC_API_KEY"] = api_key
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
        session_id = ""
        # Use a list so the watchdog closure can see updates from the reader loop.
        activity = [time.monotonic()]
        start_time = time.monotonic()
        stall_timeout_s = self.config.stall_timeout_ms / 1000
        turn_timeout_s = self.config.turn_timeout_ms / 1000
        output_parts: list[str] = []
        stop_watchdog = threading.Event()

        # Compute watchdog poll interval: ~¼ of the smallest active timeout,
        # clamped to [0.5, 5.0] seconds.
        _active_timeouts = [t for t in (stall_timeout_s, turn_timeout_s) if t > 0]
        check_interval = max(0.5, min((min(_active_timeouts) / 4 if _active_timeouts else 5.0), 5.0))

        def _watchdog() -> None:
            """Daemon thread: kills the process on stall / turn-timeout / cancel.

            This runs independently of the NDJSON reader loop, so timeouts fire
            even when the subprocess produces no output at all.
            """
            while not stop_watchdog.wait(timeout=check_interval):
                if cancel_event and cancel_event.is_set():
                    log.warning("Watchdog: cancel requested — killing Claude Code process.")
                    process.kill()
                    return
                if stall_timeout_s > 0 and (time.monotonic() - activity[0]) > stall_timeout_s:
                    log.warning("Watchdog: Claude Code stalled — killing process.")
                    process.kill()
                    return
                if turn_timeout_s > 0 and (time.monotonic() - start_time) > turn_timeout_s:
                    log.warning("Watchdog: Claude Code turn timeout — killing process.")
                    process.kill()
                    return

        watchdog_thread = threading.Thread(
            target=_watchdog, daemon=True, name="agent-watchdog",
        )
        watchdog_thread.start()

        assert process.stdout is not None

        try:
            for line in process.stdout:
                stripped = line.strip()
                if not stripped:
                    continue

                activity[0] = time.monotonic()
                event = self._try_parse_json(stripped)
                if event is None:
                    log.debug("Skipping non-JSON line: %.100s", stripped)
                    continue

                log.debug("Raw event: type=%s subtype=%s", event.get("type"), event.get("subtype"))
                self._forward_events(event, on_event)

                etype = event.get("type")

                if etype == "system" and event.get("subtype") == "init":
                    session_id = event.get("session_id", "")
                    log.info(
                        "Claude Code session started: sid=%s model=%s",
                        session_id, event.get("model", "?"),
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
                        success=event.get("subtype") in ("success", "completion"),
                        output_text="".join(output_parts),
                    )

                if _is_user_input_required(event):
                    log.warning("Claude Code requested user input — killing process.")
                    process.kill()
                    return TurnResult(
                        session_id=session_id, duration_ms=0,
                        success=False, output_text="".join(output_parts),
                        error="turn_input_required",
                    )
        finally:
            stop_watchdog.set()

        self._wait_process(process)
        stderr_tail = self._read_stderr(process)
        # Determine whether the process was killed by the watchdog.
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        if process.returncode is not None and process.returncode < 0:
            idle_s = time.monotonic() - activity[0]
            error = "stall_timeout" if (stall_timeout_s > 0 and idle_s >= stall_timeout_s) else "turn_timeout"
            return TurnResult(
                session_id=session_id, duration_ms=elapsed_ms,
                success=False, output_text="".join(output_parts), error=error,
            )
        return TurnResult(
            session_id=session_id,
            duration_ms=elapsed_ms,
            success=(process.returncode == 0),
            output_text="".join(output_parts),
            error=f"process_exit({process.returncode}): {stderr_tail}" if process.returncode else None,
        )

    @staticmethod
    def _try_parse_json(line: str) -> dict[str, Any] | None:
        try:
            obj = json.loads(line)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _forward_events(
        raw: dict[str, Any],
        on_event: Callable[[AgentEvent], None] | None,
    ) -> None:
        if on_event is None:
            return
        for evt in normalize_events(raw):
            on_event(evt)

    @staticmethod
    def _check_stall(last_activity: float, timeout_s: float) -> bool:
        if timeout_s <= 0:
            return False
        return (time.monotonic() - last_activity) > timeout_s

    @staticmethod
    def _check_turn_timeout(start: float, timeout_s: float) -> bool:
        if timeout_s <= 0:
            return False
        return (time.monotonic() - start) > timeout_s

    @staticmethod
    def _wait_process(process: subprocess.Popen[str]) -> None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    @staticmethod
    def _read_stderr(process: subprocess.Popen[str]) -> str:
        if process.stderr is None:
            return ""
        try:
            return process.stderr.read().strip()[-500:]
        except Exception:
            return ""

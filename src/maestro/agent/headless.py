"""Cursor Headless CLI runner — the primary agent execution backend.

Launches ``agent -p --force --output-format stream-json`` as a subprocess,
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from maestro.agent.events import AgentEvent, normalize_event, _is_user_input_required
from maestro.workflow.config import CursorConfig

log = logging.getLogger(__name__)


class HeadlessError(RuntimeError):
    """Raised when the headless agent fails critically."""


@dataclass(frozen=True)
class TurnResult:
    session_id: str
    duration_ms: int
    success: bool
    output_text: str
    error: str | None = None


class HeadlessRunner:
    """Execute a single turn via Cursor's headless CLI mode."""

    def __init__(self, config: CursorConfig) -> None:
        self.config = config

    def run_turn(
        self,
        *,
        workspace: Path,
        prompt: str,
        resume_session_id: str | None = None,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> TurnResult:
        cmd = self._build_command(workspace, prompt, resume_session_id)
        env = self._build_env()

        log.info("Launching headless agent in %s", workspace)
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

        return self._stream_until_done(process, on_event)

    # ------------------------------------------------------------------
    # Command construction
    # ------------------------------------------------------------------

    def _build_command(
        self,
        workspace: Path,
        prompt: str,
        resume_id: str | None,
    ) -> list[str]:
        executable = self._resolve_executable()
        cmd = [executable, "-p"]

        if self.config.force:
            cmd.append("--force")

        cmd.extend(["--output-format", "stream-json"])
        cmd.extend(["--workspace", str(workspace)])

        if self.config.trust:
            cmd.append("--trust")
        if self.config.approve_mcps:
            cmd.append("--approve-mcps")
        if self.config.model:
            cmd.extend(["--model", self.config.model])
        if self.config.sandbox:
            cmd.extend(["--sandbox", self.config.sandbox])
        if resume_id:
            cmd.extend(["--resume", resume_id])

        cmd.append(prompt)
        return cmd

    def _resolve_executable(self) -> str:
        cmd = self.config.command
        if shutil.which(cmd):
            return cmd
        if cmd == "agent":
            for candidate in (
                "cursor",
                "/Applications/Cursor.app/Contents/Resources/app/bin/cursor",
            ):
                path = shutil.which(candidate) or candidate
                if path and Path(path).exists():
                    return path
        raise FileNotFoundError(f"Could not find executable: {cmd}")

    def _build_env(self) -> dict[str, str]:
        """Set CURSOR_AUTH_TOKEN and remove CURSOR_API_KEY to bypass macOS Keychain entirely."""
        env = os.environ.copy()

        if env.get("CURSOR_AUTH_TOKEN"):
            env.pop("CURSOR_API_KEY", None)
            return env

        api_key = self.config.api_key or env.get("CURSOR_API_KEY")
        if api_key:
            token = self._exchange_api_key(api_key)
            if token:
                env["CURSOR_AUTH_TOKEN"] = token
                env.pop("CURSOR_API_KEY", None)

        return env

    _token_cache: str | None = None
    _token_lock = threading.Lock()

    @classmethod
    def _exchange_api_key(cls, api_key: str) -> str | None:
        """Exchange CURSOR_API_KEY for a short-lived access token (cached)."""
        with cls._token_lock:
            if cls._token_cache:
                return cls._token_cache
            try:
                resp = httpx.post(
                    "https://api2.cursor.sh/auth/exchange_user_api_key",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    },
                    json={},
                    timeout=15,
                )
                resp.raise_for_status()
                token = resp.json().get("accessToken")
                if token:
                    cls._token_cache = token
                    log.debug("Obtained CURSOR_AUTH_TOKEN via token exchange.")
                    return token
            except Exception:
                log.warning("Token exchange failed; falling back to CURSOR_API_KEY.", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # NDJSON stream processing
    # ------------------------------------------------------------------

    def _stream_until_done(
        self,
        process: subprocess.Popen[str],
        on_event: Callable[[AgentEvent], None] | None,
    ) -> TurnResult:
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
            event = self._try_parse_json(stripped)
            if event is None:
                continue

            self._forward_event(event, on_event)

            etype = event.get("type")

            if etype == "system" and event.get("subtype") == "init":
                session_id = event.get("session_id", "")
                log.info(
                    "Agent session started: sid=%s model=%s",
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
                    success=(event.get("subtype") == "success"),
                    output_text="".join(output_parts),
                )

            if _is_user_input_required(event):
                log.warning("Agent requested user input — killing process (hard failure).")
                process.kill()
                return TurnResult(
                    session_id=session_id, duration_ms=0,
                    success=False, output_text="".join(output_parts),
                    error="turn_input_required",
                )

            if self._check_stall(last_activity, stall_timeout_s):
                log.warning("Agent stalled — killing process.")
                process.kill()
                return TurnResult(
                    session_id=session_id, duration_ms=0,
                    success=False, output_text="".join(output_parts),
                    error="stall_timeout",
                )

            if self._check_turn_timeout(start_time, turn_timeout_s):
                log.warning("Turn timeout — killing process.")
                process.kill()
                return TurnResult(
                    session_id=session_id, duration_ms=0,
                    success=False, output_text="".join(output_parts),
                    error="turn_timeout",
                )

        self._wait_process(process)
        stderr_tail = self._read_stderr(process)
        return TurnResult(
            session_id=session_id,
            duration_ms=int((time.monotonic() - start_time) * 1000),
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
    def _forward_event(
        raw: dict[str, Any],
        on_event: Callable[[AgentEvent], None] | None,
    ) -> None:
        if on_event is None:
            return
        normalized = normalize_event(raw)
        if normalized:
            on_event(normalized)

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

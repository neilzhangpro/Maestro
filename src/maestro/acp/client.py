"""Cursor ACP client implementation.

On macOS the Cursor CLI stores auth tokens in the macOS Keychain.
When Maestro runs inside a sandboxed environment (e.g. a Cursor IDE
subprocess) the Keychain is not accessible, causing ``agent acp`` to
fail with ``[unauthenticated]``.

To work around this, ACPClient exchanges the ``CURSOR_API_KEY`` for a
short-lived access token via the Cursor token-exchange endpoint and
passes it to the ``agent acp`` subprocess through the
``CURSOR_AUTH_TOKEN`` environment variable, which the CLI accepts as a
pre-authentication method.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
import queue
import shutil
import shlex
import subprocess
import threading
import time
from typing import Any, Callable

import httpx

from maestro.config import AcpConfig
from maestro.acp.permissions import build_permission_result
from maestro.acp.protocol import (
    AcpProtocolError,
    build_error_response,
    build_request,
    build_response,
    parse_message,
)

log = logging.getLogger(__name__)


class AcpError(RuntimeError):
    """Base class for ACP client failures."""


class AcpTimeoutError(AcpError):
    """Raised when the ACP server does not answer in time."""


class AcpProcessError(AcpError):
    """Raised when the ACP process exits unexpectedly."""


@dataclass(slots=True, frozen=True)
class AcpPromptResult:
    session_id: str
    stop_reason: str | None
    output_text: str
    result: dict[str, Any]


def _exchange_api_key(api_key: str, exchange_url: str) -> str:
    """Exchange a CURSOR_API_KEY for a short-lived access token."""
    resp = httpx.post(
        exchange_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("accessToken")
    if not token:
        raise AcpError("Token exchange succeeded but response contained no accessToken.")
    return token


class ACPClient:
    """Run a single prompt through ``agent acp``."""

    def __init__(self, config: AcpConfig) -> None:
        self.config = config

    def run_prompt(
        self,
        *,
        cwd: str | Path,
        prompt: str,
        mcp_servers: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> AcpPromptResult:
        return self._run_acp_prompt(
            cwd=cwd, prompt=prompt, mcp_servers=mcp_servers, on_chunk=on_chunk,
        )

    def _build_subprocess_env(self) -> dict[str, str]:
        """Return env dict with CURSOR_AUTH_TOKEN set for the agent subprocess."""
        env = os.environ.copy()

        if env.get("CURSOR_AUTH_TOKEN"):
            return env

        api_key = self.config.cursor_api_key or env.get("CURSOR_API_KEY")
        if not api_key:
            return env

        try:
            token = _exchange_api_key(api_key, self.config.token_exchange_url)
            env["CURSOR_AUTH_TOKEN"] = token
            log.debug("Obtained CURSOR_AUTH_TOKEN via token exchange.")
        except Exception:
            log.warning("Token exchange failed; agent acp may fail to authenticate.", exc_info=True)

        return env

    def _run_acp_prompt(
        self,
        *,
        cwd: str | Path,
        prompt: str,
        mcp_servers: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> AcpPromptResult:
        bootstrap_timeout_ms = min(self.config.turn_timeout_ms, 15_000)
        command = self._resolve_command()
        env = self._build_subprocess_env()
        process = subprocess.Popen(
            command,
            cwd=str(Path(cwd)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        state = _AcpRuntimeState(
            process=process,
            policy=self.config.permission_policy,
            on_chunk=on_chunk,
        )
        state.start()
        try:
            self._send_request(
                state,
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False},
                        "terminal": False,
                    },
                    "clientInfo": {
                        "name": self.config.client_name,
                        "version": self.config.client_version,
                    },
                },
                timeout_ms=bootstrap_timeout_ms,
            )
            self._send_request(
                state,
                "authenticate",
                {"methodId": "cursor_login"},
                timeout_ms=bootstrap_timeout_ms,
            )
            session = self._send_request(
                state,
                "session/new",
                {"cwd": str(Path(cwd).resolve()), "mcpServers": mcp_servers or []},
                timeout_ms=bootstrap_timeout_ms,
            )
            session_id = session["sessionId"]
            result = self._send_request(
                state,
                "session/prompt",
                {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": prompt}],
                },
            )
            return AcpPromptResult(
                session_id=session_id,
                stop_reason=result.get("stopReason"),
                output_text="".join(state.output_chunks),
                result=result,
            )
        finally:
            state.close()

    def _run_headless_prompt(
        self,
        *,
        cwd: str | Path,
        prompt: str,
        on_chunk: Callable[[str], None] | None = None,
    ) -> AcpPromptResult:
        executable = shutil.which("agent") or "agent"
        command = [executable, "-f", "-p", "--output-format", "text", prompt]
        process = subprocess.run(
            command,
            cwd=str(Path(cwd)),
            capture_output=True,
            text=True,
            timeout=self.config.turn_timeout_ms / 1000,
            check=False,
        )
        if process.returncode != 0:
            stderr = process.stderr.strip() or process.stdout.strip()
            raise AcpProcessError(f"Headless agent execution failed: {stderr}")
        output_text = process.stdout
        if on_chunk and output_text:
            on_chunk(output_text)
        return AcpPromptResult(
            session_id="headless",
            stop_reason="headless_fallback",
            output_text=output_text,
            result={"mode": "headless_fallback"},
        )

    def _send_request(
        self,
        state: "_AcpRuntimeState",
        method: str,
        params: dict[str, Any],
        *,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        message_id = state.next_message_id()
        state.send_line(build_request(message_id, method, params))
        response = state.wait_for_response(
            message_id,
            timeout_ms=timeout_ms if timeout_ms is not None else self.config.turn_timeout_ms,
        )
        if "error" in response:
            error = response["error"]
            raise AcpError(error.get("message", f"ACP request failed for {method}."))
        result = response.get("result")
        if not isinstance(result, dict):
            raise AcpProtocolError(f"ACP response for {method} did not include an object result.")
        return result

    def _resolve_command(self) -> list[str]:
        command = shlex.split(self.config.command)
        executable = command[0]
        if shutil.which(executable):
            return command

        if executable == "agent":
            cursor_candidates = [
                "cursor",
                "/Applications/Cursor.app/Contents/Resources/app/bin/cursor",
            ]
            for candidate in cursor_candidates:
                candidate_path = shutil.which(candidate) or candidate
                if candidate_path and Path(candidate_path).exists():
                    return [candidate_path, "agent", *command[1:]]

        raise FileNotFoundError(f"Could not find ACP executable '{executable}'.")


class _AcpRuntimeState:
    def __init__(
        self,
        *,
        process: subprocess.Popen[str],
        policy: str,
        on_chunk: Callable[[str], None] | None,
    ) -> None:
        self.process = process
        self.policy = policy
        self.on_chunk = on_chunk
        self._next_id = 1
        self._lock = threading.Lock()
        self._responses: dict[int, queue.Queue[dict[str, Any]]] = {}
        self.output_chunks: list[str] = []
        self.stderr_lines: list[str] = []
        self._closed = False
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)

    def start(self) -> None:
        self._stdout_thread.start()
        self._stderr_thread.start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=1)

    def next_message_id(self) -> int:
        with self._lock:
            message_id = self._next_id
            self._next_id += 1
            self._responses[message_id] = queue.Queue(maxsize=1)
            return message_id

    def send_line(self, payload: str) -> None:
        if not self.process.stdin:
            raise AcpProcessError("ACP stdin is not available.")
        try:
            self.process.stdin.write(payload + "\n")
            self.process.stdin.flush()
        except BrokenPipeError as exc:
            raise AcpProcessError(self._exit_message("ACP process closed its stdin.")) from exc

    def wait_for_response(self, message_id: int, *, timeout_ms: int) -> dict[str, Any]:
        response_queue = self._responses[message_id]
        timeout_s = timeout_ms / 1000
        deadline = time.monotonic() + timeout_s
        try:
            while True:
                if self.process.poll() is not None and response_queue.empty():
                    raise AcpProcessError(self._exit_message("ACP process exited before responding."))
                remaining = max(0.0, deadline - time.monotonic())
                if remaining == 0:
                    self.process.kill()
                    raise AcpTimeoutError(f"Timed out waiting for ACP response to message {message_id}.")
                try:
                    return response_queue.get(timeout=remaining)
                except queue.Empty:
                    continue
        finally:
            self._responses.pop(message_id, None)

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            stripped = line.strip()
            if not stripped:
                continue
            message = parse_message(stripped)
            if "id" in message and ("result" in message or "error" in message):
                response_queue = self._responses.get(message["id"])
                if response_queue is not None:
                    response_queue.put(message)
                continue
            self._handle_server_message(message)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr_lines.append(line.rstrip())

    def _handle_server_message(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method == "session/update":
            update = message.get("params", {}).get("update", {})
            if (
                update.get("sessionUpdate") == "agent_message_chunk"
                and isinstance(update.get("content"), dict)
                and isinstance(update["content"].get("text"), str)
            ):
                chunk = update["content"]["text"]
                self.output_chunks.append(chunk)
                if self.on_chunk:
                    self.on_chunk(chunk)
            return
        if method == "session/request_permission":
            if "id" not in message:
                raise AcpProtocolError("Permission request is missing an id.")
            self.send_line(build_response(message["id"], build_permission_result(self.policy)))
            return
        if "id" in message:
            self.send_line(build_error_response(message["id"], -32601, f"Unsupported method: {method}"))

    def _exit_message(self, prefix: str) -> str:
        extra = ""
        if self.stderr_lines:
            extra = f" stderr: {' | '.join(self.stderr_lines[-5:])}"
        return prefix + extra

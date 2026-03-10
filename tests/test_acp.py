from __future__ import annotations

import json
import queue
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from maestro.acp.client import (
    ACPClient,
    AcpProcessError,
    AcpPromptResult,
    AcpTimeoutError,
    _exchange_api_key,
)
from maestro.acp.permissions import build_permission_result
from maestro.acp.protocol import build_request, parse_message
from maestro.config import AcpConfig


class FakeReadable:
    def __init__(self) -> None:
        self._queue: queue.Queue[str | None] = queue.Queue()

    def push(self, line: str) -> None:
        self._queue.put(line)

    def close(self) -> None:
        self._queue.put(None)

    def __iter__(self) -> "FakeReadable":
        return self

    def __next__(self) -> str:
        item = self._queue.get(timeout=1)
        if item is None:
            raise StopIteration
        return item


class FakeWritable:
    def __init__(self, on_line) -> None:
        self._buffer = ""
        self._on_line = on_line
        self.closed = False

    def write(self, chunk: str) -> int:
        self._buffer += chunk
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line:
                self._on_line(line)
        return len(chunk)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self) -> None:
        self.stdout = FakeReadable()
        self.stderr = FakeReadable()
        self.returncode: int | None = None
        self._prompt_request_id: int | None = None
        self.stdin = FakeWritable(self._handle_stdin_line)

    def _handle_stdin_line(self, line: str) -> None:
        message = json.loads(line)
        if "method" in message:
            method = message["method"]
            if method in {"initialize", "authenticate"}:
                self.stdout.push(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {}}) + "\n")
            elif method == "session/new":
                self.stdout.push(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": {"sessionId": "session-1"},
                        }
                    )
                    + "\n"
                )
            elif method == "session/prompt":
                self._prompt_request_id = message["id"]
                self.stdout.push(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 900,
                            "method": "session/request_permission",
                            "params": {"tool": "shell"},
                        }
                    )
                    + "\n"
                )
        elif message.get("id") == 900:
            assert message["result"] == build_permission_result("allow-once")
            self.stdout.push(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"text": "hello from agent"},
                            }
                        },
                    }
                )
                + "\n"
            )
            self.stdout.push(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": self._prompt_request_id,
                        "result": {"stopReason": "end_turn"},
                    }
                )
                + "\n"
            )

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        self.stdout.close()
        self.stderr.close()
        return 0

    def kill(self) -> None:
        self.returncode = -9
        self.stdout.close()
        self.stderr.close()


def test_protocol_request_round_trip() -> None:
    message = build_request(1, "initialize", {"protocolVersion": 1})
    parsed = parse_message(message)

    assert parsed["method"] == "initialize"
    assert parsed["params"] == {"protocolVersion": 1}


def test_acp_client_runs_minimal_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: FakeProcess())

    chunks: list[str] = []
    client = ACPClient(AcpConfig(command="agent acp"))

    result = client.run_prompt(
        cwd=tmp_path,
        prompt="Say hello",
        on_chunk=chunks.append,
    )

    assert result.session_id == "session-1"
    assert result.stop_reason == "end_turn"
    assert result.output_text == "hello from agent"
    assert chunks == ["hello from agent"]


def test_build_subprocess_env_sets_cursor_auth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)

    config = AcpConfig(cursor_api_key="test-api-key")
    client = ACPClient(config)

    with patch(
        "maestro.acp.client._exchange_api_key",
        return_value="exchanged-token",
    ) as mock_exchange:
        env = client._build_subprocess_env()

    assert env["CURSOR_AUTH_TOKEN"] == "exchanged-token"
    mock_exchange.assert_called_once_with("test-api-key", config.token_exchange_url)


def test_build_subprocess_env_skips_exchange_when_auth_token_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURSOR_AUTH_TOKEN", "already-present")

    client = ACPClient(AcpConfig(cursor_api_key="test-api-key"))

    with patch("maestro.acp.client._exchange_api_key") as mock_exchange:
        env = client._build_subprocess_env()

    assert env["CURSOR_AUTH_TOKEN"] == "already-present"
    mock_exchange.assert_not_called()


def test_build_subprocess_env_falls_back_to_env_cursor_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURSOR_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("CURSOR_API_KEY", "env-api-key")

    client = ACPClient(AcpConfig())

    with patch(
        "maestro.acp.client._exchange_api_key",
        return_value="exchanged-from-env",
    ) as mock_exchange:
        env = client._build_subprocess_env()

    assert env["CURSOR_AUTH_TOKEN"] == "exchanged-from-env"
    mock_exchange.assert_called_once_with("env-api-key", AcpConfig().token_exchange_url)


def test_build_subprocess_env_survives_exchange_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURSOR_AUTH_TOKEN", raising=False)

    client = ACPClient(AcpConfig(cursor_api_key="bad-key"))

    with patch(
        "maestro.acp.client._exchange_api_key",
        side_effect=httpx.HTTPStatusError("401", request=None, response=None),
    ):
        env = client._build_subprocess_env()

    assert "CURSOR_AUTH_TOKEN" not in env

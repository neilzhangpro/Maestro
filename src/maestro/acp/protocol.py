"""Helpers for ACP JSON-RPC messages."""

from __future__ import annotations

import json
from typing import Any


class AcpProtocolError(ValueError):
    """Raised when an ACP message is malformed."""


def build_request(message_id: int, method: str, params: dict[str, Any]) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params},
        separators=(",", ":"),
    )


def build_response(message_id: int, result: dict[str, Any]) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": message_id, "result": result},
        separators=(",", ":"),
    )


def build_error_response(message_id: int, code: int, message: str) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {"code": code, "message": message},
        },
        separators=(",", ":"),
    )


def parse_message(line: str) -> dict[str, Any]:
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise AcpProtocolError("Received invalid JSON from ACP transport.") from exc
    if not isinstance(message, dict):
        raise AcpProtocolError("ACP message must decode into an object.")
    if message.get("jsonrpc") != "2.0":
        raise AcpProtocolError("ACP message must declare jsonrpc=2.0.")
    return message

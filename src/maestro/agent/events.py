"""Normalized event model for agent execution telemetry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class AgentEvent:
    event: str
    timestamp: datetime
    session_id: str = ""
    message: str = ""
    model: str = ""
    tool_name: str = ""
    tool_path: str = ""
    call_id: str = ""
    duration_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def normalize_event(raw: dict[str, Any]) -> AgentEvent | None:
    """Convert a Cursor ``stream-json`` event into an :class:`AgentEvent`.

    Returns ``None`` for event types that don't need forwarding.
    """
    etype = raw.get("type")
    now = datetime.now(timezone.utc)
    sid = raw.get("session_id", "")

    if etype == "system" and raw.get("subtype") == "init":
        return AgentEvent(
            event="session_started",
            timestamp=now,
            session_id=sid,
            model=raw.get("model", ""),
        )

    if etype == "tool_call":
        tc = raw.get("tool_call", {})
        tool_name = next(iter(tc.keys()), "unknown")
        tool_path = ""
        if "readToolCall" in tc:
            tool_path = tc["readToolCall"].get("args", {}).get("path", "")
        elif "writeToolCall" in tc:
            args = tc["writeToolCall"].get("args", {})
            tool_path = args.get("path", "")
        return AgentEvent(
            event=f"tool_{raw.get('subtype', 'unknown')}",
            timestamp=now,
            session_id=sid,
            tool_name=tool_name,
            tool_path=tool_path,
            call_id=raw.get("call_id", ""),
        )

    if etype == "assistant":
        content = raw.get("message", {}).get("content", [])
        text = content[0].get("text", "") if content else ""
        return AgentEvent(
            event="notification",
            timestamp=now,
            session_id=sid,
            message=text[:200],
        )

    if etype == "result":
        success = raw.get("subtype") == "success"
        return AgentEvent(
            event="turn_completed" if success else "turn_failed",
            timestamp=now,
            session_id=sid,
            duration_ms=raw.get("duration_ms", 0),
        )

    return None

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


def normalize_events(raw: dict[str, Any]) -> list[AgentEvent]:
    """Convert a raw stream-json event into one or more :class:`AgentEvent` objects.

    Claude Code assistant messages may contain multiple content blocks
    (text + tool_use), each producing a separate AgentEvent.  Cursor events
    always produce at most one.
    """
    etype = raw.get("type")
    now = datetime.now(timezone.utc)
    sid = raw.get("session_id", "")

    if etype == "system" and raw.get("subtype") == "init":
        return [AgentEvent(
            event="session_started",
            timestamp=now,
            session_id=sid,
            model=raw.get("model", ""),
        )]

    if etype == "tool_call":
        tc = raw.get("tool_call", {})
        tool_name = next(iter(tc.keys()), "unknown")
        tool_path = ""
        if "readToolCall" in tc:
            tool_path = tc["readToolCall"].get("args", {}).get("path", "")
        elif "writeToolCall" in tc:
            args = tc["writeToolCall"].get("args", {})
            tool_path = args.get("path", "")
        return [AgentEvent(
            event=f"tool_{raw.get('subtype', 'unknown')}",
            timestamp=now,
            session_id=sid,
            tool_name=tool_name,
            tool_path=tool_path,
            call_id=raw.get("call_id", ""),
        )]

    if etype == "assistant":
        content = raw.get("message", {}).get("content", [])
        events: list[AgentEvent] = []
        for block in content:
            btype = block.get("type")
            if btype == "text":
                events.append(AgentEvent(
                    event="notification",
                    timestamp=now,
                    session_id=sid,
                    message=block.get("text", "")[:200],
                ))
            elif btype == "tool_use":
                input_data = block.get("input", {})
                tool_path = (
                    input_data.get("command", "")
                    or input_data.get("path", "")
                )
                events.append(AgentEvent(
                    event="tool_start",
                    timestamp=now,
                    session_id=sid,
                    tool_name=block.get("name", "unknown"),
                    tool_path=tool_path,
                    call_id=block.get("id", ""),
                ))
        if not events:
            events.append(AgentEvent(
                event="notification",
                timestamp=now,
                session_id=sid,
                message="(agent processing)",
            ))
        return events

    if etype == "result":
        success = raw.get("subtype") in ("success", "completion")
        return [AgentEvent(
            event="turn_completed" if success else "turn_failed",
            timestamp=now,
            session_id=sid,
            duration_ms=raw.get("duration_ms", 0),
        )]

    if _is_user_input_required(raw):
        return [AgentEvent(
            event="turn_input_required",
            timestamp=now,
            session_id=sid,
            message="Agent requested user input — hard failure per policy.",
        )]

    return []


def normalize_event(raw: dict[str, Any]) -> AgentEvent | None:
    """Convert a raw event dict into the first :class:`AgentEvent`, or ``None``."""
    events = normalize_events(raw)
    return events[0] if events else None


def _is_user_input_required(raw: dict[str, Any]) -> bool:
    """Detect any signal that the agent is waiting for human input.

    Symphony SPEC §10.5: user-input-required events must not leave a run
    stalled indefinitely.  Implementations should fail the run immediately.
    """
    method = raw.get("method", "")
    if method in (
        "item/tool/requestUserInput",
        "session/request_user_input",
    ):
        return True

    etype = raw.get("type", "")
    subtype = raw.get("subtype", "")
    if etype == "input_required" or subtype == "input_required":
        return True
    if etype == "result" and subtype == "input_required":
        return True

    return False

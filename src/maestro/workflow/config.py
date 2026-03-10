"""Typed configuration layer derived from WORKFLOW.md front matter."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maestro.workflow.loader import WorkflowDefinition


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


_ENV_VAR = re.compile(r"\$(\w+)|\$\{([^}]+)\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_VAR.sub(
            lambda m: os.environ.get(m.group(1) or m.group(2), ""), value,
        )
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def _str(raw: Any, name: str, *, default: str | None = None) -> str:
    if raw is None:
        if default is not None:
            return default
        raise ConfigError(f"Missing required config: {name}")
    if not isinstance(raw, str):
        raise ConfigError(f"{name} must be a string.")
    return raw


def _int(raw: Any, name: str, *, default: int) -> int:
    if raw is None:
        return default
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(f"{name} must be an integer.")
    if isinstance(raw, int):
        return raw
    raise ConfigError(f"{name} must be an integer.")


def _bool(raw: Any, name: str, *, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes")
    raise ConfigError(f"{name} must be a boolean.")


def _str_list(raw: Any, name: str, *, default: list[str]) -> list[str]:
    if raw is None:
        return list(default)
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(raw, list):
        return [str(s) for s in raw]
    raise ConfigError(f"{name} must be a list or comma-separated string.")


def _opt_str(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


# ---------------------------------------------------------------------------
# Typed config dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TrackerConfig:
    kind: str
    api_key: str
    endpoint: str = "https://api.linear.app/graphql"
    project_slug: str = ""
    active_states: list[str] = field(default_factory=lambda: ["Todo", "In Progress"])
    terminal_states: list[str] = field(
        default_factory=lambda: ["Done", "Cancelled", "Closed"],
    )
    timeout_s: int = 30


@dataclass(frozen=True)
class PollingConfig:
    interval_ms: int = 30_000


@dataclass(frozen=True)
class WorkspaceConfig:
    root: Path = Path("~/maestro_workspaces")


@dataclass(frozen=True)
class HooksConfig:
    after_create: str | None = None
    before_run: str | None = None
    after_run: str | None = None
    before_remove: str | None = None
    timeout_ms: int = 60_000


@dataclass(frozen=True)
class CursorConfig:
    command: str = "agent"
    model: str = ""
    sandbox: str = "disabled"
    force: bool = True
    trust: bool = True
    approve_mcps: bool = True
    turn_timeout_ms: int = 3_600_000
    stall_timeout_ms: int = 300_000
    api_key: str | None = None


@dataclass(frozen=True)
class AgentConfig:
    max_concurrent_agents: int = 10
    max_turns: int = 3
    max_retry_backoff_ms: int = 300_000
    max_concurrent_agents_by_state: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ServerConfig:
    port: int | None = None


@dataclass(frozen=True)
class ServiceConfig:
    tracker: TrackerConfig
    polling: PollingConfig
    workspace: WorkspaceConfig
    hooks: HooksConfig
    cursor: CursorConfig
    agent: AgentConfig
    server: ServerConfig
    prompt_template: str
    workflow_path: Path

    @classmethod
    def from_workflow(cls, wd: WorkflowDefinition) -> "ServiceConfig":
        raw = _expand_env(wd.config)
        return cls(
            tracker=_parse_tracker(raw.get("tracker") or {}),
            polling=_parse_polling(raw.get("polling") or {}),
            workspace=_parse_workspace(raw.get("workspace") or {}),
            hooks=_parse_hooks(raw.get("hooks") or {}),
            cursor=_parse_cursor(raw.get("cursor") or {}),
            agent=_parse_agent(raw.get("agent") or {}),
            server=_parse_server(raw.get("server") or {}),
            prompt_template=wd.prompt_template,
            workflow_path=wd.source_path,
        )


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------

def _parse_tracker(raw: dict[str, Any]) -> TrackerConfig:
    kind = _str(raw.get("kind"), "tracker.kind", default="linear")
    api_key = _str(raw.get("api_key"), "tracker.api_key", default="")
    if not api_key:
        api_key = os.environ.get("LINEAR_API_KEY", "")
    return TrackerConfig(
        kind=kind,
        api_key=api_key,
        endpoint=_str(
            raw.get("endpoint"), "tracker.endpoint",
            default="https://api.linear.app/graphql",
        ),
        project_slug=_str(raw.get("project_slug"), "tracker.project_slug", default=""),
        active_states=_str_list(
            raw.get("active_states"), "tracker.active_states",
            default=["Todo", "In Progress"],
        ),
        terminal_states=_str_list(
            raw.get("terminal_states"), "tracker.terminal_states",
            default=["Done", "Cancelled", "Closed"],
        ),
        timeout_s=_int(raw.get("timeout_s"), "tracker.timeout_s", default=30),
    )


def _parse_polling(raw: dict[str, Any]) -> PollingConfig:
    return PollingConfig(
        interval_ms=_int(raw.get("interval_ms"), "polling.interval_ms", default=30_000),
    )


def _parse_workspace(raw: dict[str, Any]) -> WorkspaceConfig:
    root_str = _str(raw.get("root"), "workspace.root", default="~/maestro_workspaces")
    root = Path(root_str).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    return WorkspaceConfig(root=root)


def _parse_hooks(raw: dict[str, Any]) -> HooksConfig:
    return HooksConfig(
        after_create=_opt_str(raw.get("after_create")),
        before_run=_opt_str(raw.get("before_run")),
        after_run=_opt_str(raw.get("after_run")),
        before_remove=_opt_str(raw.get("before_remove")),
        timeout_ms=_int(raw.get("timeout_ms"), "hooks.timeout_ms", default=60_000),
    )


def _parse_cursor(raw: dict[str, Any]) -> CursorConfig:
    api_key = _opt_str(raw.get("api_key"))
    if not api_key:
        api_key = os.environ.get("CURSOR_API_KEY") or None
    return CursorConfig(
        command=_str(raw.get("command"), "cursor.command", default="agent"),
        model=_str(raw.get("model"), "cursor.model", default=""),
        sandbox=_str(raw.get("sandbox"), "cursor.sandbox", default="disabled"),
        force=_bool(raw.get("force"), "cursor.force", default=True),
        trust=_bool(raw.get("trust"), "cursor.trust", default=True),
        approve_mcps=_bool(raw.get("approve_mcps"), "cursor.approve_mcps", default=True),
        turn_timeout_ms=_int(
            raw.get("turn_timeout_ms"), "cursor.turn_timeout_ms", default=3_600_000,
        ),
        stall_timeout_ms=_int(
            raw.get("stall_timeout_ms"), "cursor.stall_timeout_ms", default=300_000,
        ),
        api_key=api_key,
    )


def _parse_agent(raw: dict[str, Any]) -> AgentConfig:
    by_state_raw = raw.get("max_concurrent_agents_by_state") or {}
    by_state: dict[str, int] = {}
    if isinstance(by_state_raw, dict):
        for k, v in by_state_raw.items():
            try:
                iv = int(v)
                if iv > 0:
                    by_state[str(k).strip().lower()] = iv
            except (TypeError, ValueError):
                pass

    return AgentConfig(
        max_concurrent_agents=_int(
            raw.get("max_concurrent_agents"), "agent.max_concurrent_agents", default=10,
        ),
        max_turns=_int(raw.get("max_turns"), "agent.max_turns", default=3),
        max_retry_backoff_ms=_int(
            raw.get("max_retry_backoff_ms"), "agent.max_retry_backoff_ms", default=300_000,
        ),
        max_concurrent_agents_by_state=by_state,
    )


def _parse_server(raw: dict[str, Any]) -> ServerConfig:
    port = raw.get("port")
    if port is None:
        return ServerConfig()
    return ServerConfig(port=_int(port, "server.port", default=0))


# ---------------------------------------------------------------------------
# Dispatch preflight validation (SPEC §6.3)
# ---------------------------------------------------------------------------

def validate_dispatch_config(config: ServiceConfig) -> None:
    if not config.tracker.kind:
        raise ConfigError("tracker.kind is required.")
    if config.tracker.kind != "linear":
        raise ConfigError(f"Unsupported tracker kind: {config.tracker.kind}")
    if not config.tracker.api_key:
        raise ConfigError("tracker.api_key is required (or set LINEAR_API_KEY).")
    if not config.cursor.command:
        raise ConfigError("cursor.command is required.")

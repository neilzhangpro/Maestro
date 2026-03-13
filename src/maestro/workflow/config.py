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


def _float(raw: Any, name: str, *, default: float) -> float:
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            raise ConfigError(f"{name} must be a number.")
    raise ConfigError(f"{name} must be a number.")


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
    team_id: str | None = None
    assignee: str | None = None
    active_states: list[str] = field(default_factory=lambda: ["Todo", "In Progress"])
    terminal_states: list[str] = field(
        default_factory=lambda: ["Done", "Cancelled", "Closed"],
    )
    handoff_states: list[str] = field(
        default_factory=lambda: ["Human Review"],
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
    claude_code_after_create: str | None = None
    claude_code_before_run: str | None = None
    claude_code_after_run: str | None = None
    claude_code_before_remove: str | None = None


@dataclass(frozen=True)
class CursorConfig:
    command: str = "agent"
    model: str = ""
    plan_model: str = ""
    sandbox: str = "disabled"
    force: bool = True
    trust: bool = True
    approve_mcps: bool = True
    turn_timeout_ms: int = 3_600_000
    stall_timeout_ms: int = 300_000
    api_key: str | None = None


_DEFAULT_CLAUDE_TOOLS = [
    "Bash", "Read", "Write", "Edit", "MultiEdit",
    "Glob", "Grep", "LS", "TodoRead", "TodoWrite",
]


@dataclass(frozen=True)
class ClaudeCodeConfig:
    command: str = "claude"
    model: str = ""
    plan_model: str = ""
    api_key: str | None = None
    skip_permissions: bool = False
    allowed_tools: list[str] = field(default_factory=lambda: list(_DEFAULT_CLAUDE_TOOLS))
    max_turns_per_invocation: int = 0
    max_budget_usd: float = 0.0
    append_system_prompt: str | None = None
    turn_timeout_ms: int = 3_600_000
    stall_timeout_ms: int = 300_000


@dataclass(frozen=True)
class GitHubConfig:
    token: str = ""
    owner: str = ""
    repo: str = ""
    ci_watch_states: list[str] = field(default_factory=list)
    ci_poll_interval_ms: int = 60_000
    ci_max_wait_ms: int = 1_800_000
    ci_pass_target_state: str = "Done"
    ci_fail_target_state: str = "In Progress"


@dataclass(frozen=True)
class AgentConfig:
    auto_dispatch: bool = False
    max_concurrent_agents: int = 10
    max_turns: int = 3
    max_retry_backoff_ms: int = 300_000
    max_concurrent_agents_by_state: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ServerConfig:
    port: int | None = None


_SUPPORTED_BACKENDS = {"cursor", "claude_code"}


@dataclass(frozen=True)
class ServiceConfig:
    tracker: TrackerConfig
    polling: PollingConfig
    workspace: WorkspaceConfig
    hooks: HooksConfig
    cursor: CursorConfig
    agent: AgentConfig
    server: ServerConfig
    github: GitHubConfig
    prompt_template: str
    workflow_path: Path
    backend: str = "cursor"
    claude_code: ClaudeCodeConfig | None = None

    @classmethod
    def from_workflow(cls, wd: WorkflowDefinition) -> "ServiceConfig":
        raw = _expand_env(wd.config)
        backend = _str(raw.get("backend"), "backend", default="cursor")
        if backend not in _SUPPORTED_BACKENDS:
            raise ConfigError(
                f"Unsupported backend: {backend!r}. "
                f"Must be one of {sorted(_SUPPORTED_BACKENDS)}"
            )

        claude_code: ClaudeCodeConfig | None = None
        if backend == "claude_code":
            claude_code = _parse_claude_code(raw.get("claude_code") or {})

        return cls(
            tracker=_parse_tracker(raw.get("tracker") or {}),
            polling=_parse_polling(raw.get("polling") or {}),
            workspace=_parse_workspace(raw.get("workspace") or {}),
            hooks=_parse_hooks(raw.get("hooks") or {}),
            cursor=_parse_cursor(raw.get("cursor") or {}),
            agent=_parse_agent(raw.get("agent") or {}),
            server=_parse_server(raw.get("server") or {}),
            github=_parse_github(raw.get("github") or {}),
            prompt_template=wd.prompt_template,
            workflow_path=wd.source_path,
            backend=backend,
            claude_code=claude_code,
        )

    def resolved_hooks(self) -> HooksConfig:
        """Return hooks with backend-specific overrides applied."""
        if self.backend != "claude_code":
            return self.hooks
        return HooksConfig(
            after_create=self.hooks.claude_code_after_create or self.hooks.after_create,
            before_run=self.hooks.claude_code_before_run or self.hooks.before_run,
            after_run=self.hooks.claude_code_after_run or self.hooks.after_run,
            before_remove=self.hooks.claude_code_before_remove or self.hooks.before_remove,
            timeout_ms=self.hooks.timeout_ms,
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
        team_id=_opt_str(raw.get("team_id")),
        assignee=_opt_str(raw.get("assignee")),
        active_states=_str_list(
            raw.get("active_states"), "tracker.active_states",
            default=["Todo", "In Progress"],
        ),
        terminal_states=_str_list(
            raw.get("terminal_states"), "tracker.terminal_states",
            default=["Done", "Cancelled", "Closed"],
        ),
        handoff_states=_str_list(
            raw.get("handoff_states"), "tracker.handoff_states",
            default=["Human Review"],
        ),
        timeout_s=_int(raw.get("timeout_s"), "tracker.timeout_s", default=30),
    )


def _parse_polling(raw: dict[str, Any]) -> PollingConfig:
    return PollingConfig(
        interval_ms=_int(raw.get("interval_ms"), "polling.interval_ms", default=30_000),
    )


def _parse_workspace(raw: dict[str, Any]) -> WorkspaceConfig:
    root_str = _str(raw.get("root"), "workspace.root", default="~/maestro_workspaces")
    if not root_str or root_str.isspace():
        root_str = "~/maestro_workspaces"
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
        claude_code_after_create=_opt_str(raw.get("claude_code_after_create")),
        claude_code_before_run=_opt_str(raw.get("claude_code_before_run")),
        claude_code_after_run=_opt_str(raw.get("claude_code_after_run")),
        claude_code_before_remove=_opt_str(raw.get("claude_code_before_remove")),
    )


def _parse_cursor(raw: dict[str, Any]) -> CursorConfig:
    api_key = _opt_str(raw.get("api_key"))
    if not api_key:
        api_key = os.environ.get("CURSOR_API_KEY") or None
    return CursorConfig(
        command=_str(raw.get("command"), "cursor.command", default="agent"),
        model=_str(raw.get("model"), "cursor.model", default=""),
        plan_model=_str(raw.get("plan_model"), "cursor.plan_model", default=""),
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

    auto_val = raw.get("auto_dispatch")
    auto_dispatch = bool(auto_val) if auto_val is not None else False

    return AgentConfig(
        auto_dispatch=auto_dispatch,
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


def _parse_claude_code(raw: dict[str, Any]) -> ClaudeCodeConfig:
    api_key = _opt_str(raw.get("api_key"))
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY") or None
    return ClaudeCodeConfig(
        command=_str(raw.get("command"), "claude_code.command", default="claude"),
        model=_str(raw.get("model"), "claude_code.model", default=""),
        plan_model=_str(raw.get("plan_model"), "claude_code.plan_model", default=""),
        api_key=api_key,
        skip_permissions=_bool(
            raw.get("skip_permissions"), "claude_code.skip_permissions", default=False,
        ),
        allowed_tools=_str_list(
            raw.get("allowed_tools"), "claude_code.allowed_tools",
            default=list(_DEFAULT_CLAUDE_TOOLS),
        ),
        max_turns_per_invocation=_int(
            raw.get("max_turns_per_invocation"),
            "claude_code.max_turns_per_invocation", default=0,
        ),
        max_budget_usd=_float(
            raw.get("max_budget_usd"), "claude_code.max_budget_usd", default=0.0,
        ),
        append_system_prompt=_opt_str(raw.get("append_system_prompt")),
        turn_timeout_ms=_int(
            raw.get("turn_timeout_ms"), "claude_code.turn_timeout_ms",
            default=3_600_000,
        ),
        stall_timeout_ms=_int(
            raw.get("stall_timeout_ms"), "claude_code.stall_timeout_ms",
            default=300_000,
        ),
    )


def _parse_github(raw: dict[str, Any]) -> GitHubConfig:
    token = _str(raw.get("token"), "github.token", default="")
    if not token:
        token = os.environ.get("GITHUB_TOKEN", "")
    return GitHubConfig(
        token=token,
        owner=_str(raw.get("owner"), "github.owner", default=""),
        repo=_str(raw.get("repo"), "github.repo", default=""),
        ci_watch_states=_str_list(
            raw.get("ci_watch_states"), "github.ci_watch_states", default=[],
        ),
        ci_poll_interval_ms=_int(
            raw.get("ci_poll_interval_ms"), "github.ci_poll_interval_ms", default=60_000,
        ),
        ci_max_wait_ms=_int(
            raw.get("ci_max_wait_ms"), "github.ci_max_wait_ms", default=1_800_000,
        ),
        ci_pass_target_state=_str(
            raw.get("ci_pass_target_state"), "github.ci_pass_target_state", default="Done",
        ),
        ci_fail_target_state=_str(
            raw.get("ci_fail_target_state"), "github.ci_fail_target_state", default="In Progress",
        ),
    )


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

    if config.backend == "cursor":
        if not config.cursor.command:
            raise ConfigError("cursor.command is required.")
    elif config.backend == "claude_code":
        if config.claude_code is None:
            raise ConfigError(
                "claude_code section is required when backend is 'claude_code'."
            )
        if not config.claude_code.api_key:
            raise ConfigError(
                "claude_code.api_key is required (or set ANTHROPIC_API_KEY)."
            )

"""Configuration loading for Maestro."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path("config/maestro.yaml")
_ENV_VAR_PATTERN = re.compile(r"\$(\w+)|\$\{([^}]+)\}")


class ConfigError(ValueError):
    """Raised when the Maestro configuration is invalid."""


def _expand_env_vars(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(
            lambda match: os.environ.get(match.group(1) or match.group(2), ""),
            value,
        )
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env_vars(item) for key, item in value.items()}
    return value


def _require_mapping(value: Any, section: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raise ConfigError(f"Expected '{section}' to be a mapping.")


def _require_string(value: Any, field_name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    raise ConfigError(f"Expected '{field_name}' to be a non-empty string.")


def _as_string_list(value: Any, field_name: str, *, default: list[str]) -> list[str]:
    if value is None:
        return default
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise ConfigError(f"Expected '{field_name}' to be a list of strings.")


def _as_int(value: Any, field_name: str, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    raise ConfigError(f"Expected '{field_name}' to be an integer.")


def _as_optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    raise ConfigError(f"Expected '{field_name}' to be a string if provided.")


@dataclass(slots=True, frozen=True)
class LinearConfig:
    api_key: str
    api_url: str = "https://api.linear.app/graphql"
    project_slug: str | None = None
    team_id: str | None = None
    assignee: str | None = None
    active_states: list[str] = None  # type: ignore[assignment]
    terminal_states: list[str] = None  # type: ignore[assignment]
    timeout_s: int = 30

    def __post_init__(self) -> None:
        object.__setattr__(self, "active_states", self.active_states or ["Todo", "In Progress"])
        object.__setattr__(
            self,
            "terminal_states",
            self.terminal_states or ["Done", "Cancelled", "Closed"],
        )


@dataclass(slots=True, frozen=True)
class WorkspaceConfig:
    root: Path


@dataclass(slots=True, frozen=True)
class AcpConfig:
    command: str = "agent acp"
    permission_policy: str = "allow-once"
    turn_timeout_ms: int = 3_600_000
    client_name: str = "maestro"
    client_version: str = "0.1.0"
    cursor_api_key: str | None = None
    token_exchange_url: str = "https://api2.cursor.sh/auth/exchange_user_api_key"


@dataclass(slots=True, frozen=True)
class MaestroConfig:
    linear: LinearConfig
    workspace: WorkspaceConfig
    acp: AcpConfig
    config_path: Path

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, config_path: Path) -> "MaestroConfig":
        linear_raw = _require_mapping(data.get("linear"), "linear")
        workspace_raw = _require_mapping(data.get("workspace"), "workspace")
        acp_raw = _require_mapping(data.get("acp"), "acp")

        linear = LinearConfig(
            api_key=_require_string(linear_raw.get("api_key"), "linear.api_key"),
            api_url=linear_raw.get("api_url", "https://api.linear.app/graphql"),
            project_slug=_as_optional_string(linear_raw.get("project_slug"), "linear.project_slug"),
            team_id=_as_optional_string(linear_raw.get("team_id"), "linear.team_id"),
            active_states=_as_string_list(
                linear_raw.get("active_states"),
                "linear.active_states",
                default=["Todo", "In Progress"],
            ),
            terminal_states=_as_string_list(
                linear_raw.get("terminal_states"),
                "linear.terminal_states",
                default=["Done", "Cancelled", "Closed"],
            ),
            timeout_s=_as_int(linear_raw.get("timeout_s"), "linear.timeout_s", default=30),
        )
        workspace = WorkspaceConfig(
            root=Path(_require_string(workspace_raw.get("root"), "workspace.root")).expanduser()
        )
        acp = AcpConfig(
            command=_require_string(acp_raw.get("command", "agent acp"), "acp.command"),
            permission_policy=_require_string(
                acp_raw.get("permission_policy", "allow-once"),
                "acp.permission_policy",
            ),
            turn_timeout_ms=_as_int(
                acp_raw.get("turn_timeout_ms"),
                "acp.turn_timeout_ms",
                default=3_600_000,
            ),
            client_name=_require_string(
                acp_raw.get("client_name", "maestro"),
                "acp.client_name",
            ),
            client_version=_require_string(
                acp_raw.get("client_version", "0.1.0"),
                "acp.client_version",
            ),
            cursor_api_key=_as_optional_string(
                acp_raw.get("cursor_api_key"), "acp.cursor_api_key"
            ),
            token_exchange_url=acp_raw.get(
                "token_exchange_url",
                "https://api2.cursor.sh/auth/exchange_user_api_key",
            ),
        )
        return cls(linear=linear, workspace=workspace, acp=acp, config_path=config_path)


def load_config(config_path: str | Path | None = None) -> MaestroConfig:
    """Load and validate Maestro configuration from YAML."""

    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    path = path.expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()

    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError("The top-level configuration must be a mapping.")

    expanded = _expand_env_vars(raw)
    config = MaestroConfig.from_dict(expanded, config_path=path)
    workspace_root = config.workspace.root
    if not workspace_root.is_absolute():
        workspace_root = (path.parent / workspace_root).resolve()

    return MaestroConfig(
        linear=config.linear,
        workspace=WorkspaceConfig(root=workspace_root),
        acp=config.acp,
        config_path=path,
    )

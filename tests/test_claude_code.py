"""Tests for Claude Code integration — config, events, runner command construction."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from maestro.agent.events import AgentEvent, normalize_event, normalize_events
from maestro.workflow.config import (
    ClaudeCodeConfig,
    ConfigError,
    RtkConfig,
    ServiceConfig,
    validate_dispatch_config,
)


# ---------------------------------------------------------------------------
# Event normalization
# ---------------------------------------------------------------------------

class TestNormalizeEvents:
    """Verify both Cursor and Claude Code event formats produce correct AgentEvents."""

    def test_cursor_tool_call(self):
        raw = {
            "type": "tool_call",
            "subtype": "start",
            "session_id": "sid-1",
            "call_id": "call-1",
            "tool_call": {
                "readToolCall": {"args": {"path": "/src/main.py"}},
            },
        }
        events = normalize_events(raw)
        assert len(events) == 1
        assert events[0].event == "tool_start"
        assert events[0].tool_name == "readToolCall"
        assert events[0].tool_path == "/src/main.py"

    def test_claude_code_assistant_with_tool_use_block(self):
        raw = {
            "type": "assistant",
            "session_id": "sid-2",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls -la"}},
                ],
            },
        }
        events = normalize_events(raw)
        assert len(events) == 1
        assert events[0].event == "tool_start"
        assert events[0].tool_name == "Bash"
        assert events[0].tool_path == "ls -la"
        assert events[0].call_id == "toolu_1"

    def test_claude_code_assistant_mixed_content_blocks(self):
        raw = {
            "type": "assistant",
            "session_id": "sid-3",
            "message": {
                "content": [
                    {"type": "text", "text": "Planning next steps."},
                    {"type": "tool_use", "id": "toolu_2", "name": "Read", "input": {"path": "/README.md"}},
                ],
            },
        }
        events = normalize_events(raw)
        assert len(events) == 2
        assert events[0].event == "notification"
        assert events[0].message == "Planning next steps."
        assert events[1].event == "tool_start"
        assert events[1].tool_name == "Read"
        assert events[1].tool_path == "/README.md"

    def test_assistant_text_only(self):
        raw = {
            "type": "assistant",
            "session_id": "sid-4",
            "message": {"content": [{"type": "text", "text": "Hello world"}]},
        }
        events = normalize_events(raw)
        assert len(events) == 1
        assert events[0].event == "notification"
        assert events[0].message == "Hello world"

    def test_assistant_empty_content_produces_placeholder(self):
        raw = {
            "type": "assistant",
            "session_id": "sid-5",
            "message": {"content": []},
        }
        events = normalize_events(raw)
        assert len(events) == 1
        assert events[0].event == "notification"

    def test_result_success(self):
        raw = {"type": "result", "subtype": "success", "session_id": "s", "duration_ms": 123}
        events = normalize_events(raw)
        assert len(events) == 1
        assert events[0].event == "turn_completed"
        assert events[0].duration_ms == 123

    def test_result_completion_treated_as_success(self):
        raw = {"type": "result", "subtype": "completion", "session_id": "s", "duration_ms": 456}
        events = normalize_events(raw)
        assert len(events) == 1
        assert events[0].event == "turn_completed"

    def test_result_error(self):
        raw = {"type": "result", "subtype": "error", "session_id": "s"}
        events = normalize_events(raw)
        assert len(events) == 1
        assert events[0].event == "turn_failed"

    def test_system_init(self):
        raw = {"type": "system", "subtype": "init", "session_id": "s", "model": "opus"}
        events = normalize_events(raw)
        assert len(events) == 1
        assert events[0].event == "session_started"
        assert events[0].model == "opus"

    def test_normalize_event_returns_first(self):
        raw = {
            "type": "assistant",
            "session_id": "sid",
            "message": {
                "content": [
                    {"type": "text", "text": "Hi"},
                    {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"path": "/a.py"}},
                ],
            },
        }
        evt = normalize_event(raw)
        assert evt is not None
        assert evt.event == "notification"

    def test_normalize_event_none_for_unknown_type(self):
        assert normalize_event({"type": "stream_event"}) is None

    def test_user_input_required(self):
        raw = {"type": "input_required", "session_id": "s"}
        events = normalize_events(raw)
        assert len(events) == 1
        assert events[0].event == "turn_input_required"


# ---------------------------------------------------------------------------
# ClaudeCodeConfig parsing
# ---------------------------------------------------------------------------

class TestClaudeCodeConfig:
    def test_defaults(self):
        cfg = ClaudeCodeConfig()
        assert cfg.command == "claude"
        assert cfg.skip_permissions is False
        assert cfg.max_turns_per_invocation == 0
        assert cfg.max_budget_usd == 0.0
        assert "Bash" in cfg.allowed_tools
        assert cfg.turn_timeout_ms == 3_600_000

    def test_from_workflow_backend_cursor_default(self, tmp_path, monkeypatch):
        """When no backend is specified, it defaults to 'cursor'."""
        wf = tmp_path / "WORKFLOW.md"
        wf.write_text("---\ntracker:\n  api_key: test\n---\nPrompt\n", encoding="utf-8")

        monkeypatch.setenv("LINEAR_API_KEY", "test")
        from maestro.workflow.loader import load_workflow
        wd = load_workflow(wf)
        config = ServiceConfig.from_workflow(wd)
        assert config.backend == "cursor"
        assert config.claude_code is None

    def test_from_workflow_backend_claude_code(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthro-key")
        monkeypatch.setenv("LINEAR_API_KEY", "linear-key")
        wf = tmp_path / "WORKFLOW.md"
        wf.write_text(
            "---\n"
            "backend: claude_code\n"
            "tracker:\n  api_key: $LINEAR_API_KEY\n"
            "claude_code:\n"
            "  model: opus\n"
            "  skip_permissions: true\n"
            "  max_budget_usd: 5.5\n"
            "---\nPrompt\n",
            encoding="utf-8",
        )

        from maestro.workflow.loader import load_workflow
        wd = load_workflow(wf)
        config = ServiceConfig.from_workflow(wd)
        assert config.backend == "claude_code"
        assert config.claude_code is not None
        assert config.claude_code.model == "opus"
        assert config.claude_code.skip_permissions is True
        assert config.claude_code.max_budget_usd == 5.5
        assert config.claude_code.api_key == "anthro-key"
        assert config.rtk.enabled is False

    def test_rtk_config_is_parsed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthro-key")
        monkeypatch.setenv("LINEAR_API_KEY", "linear-key")
        wf = tmp_path / "WORKFLOW.md"
        wf.write_text(
            "---\n"
            "backend: claude_code\n"
            "tracker:\n  api_key: $LINEAR_API_KEY\n"
            "rtk:\n"
            "  enabled: true\n"
            "  mode: hook\n"
            "  binary: /usr/local/bin/rtk\n"
            "claude_code:\n"
            "  model: opus\n"
            "---\nPrompt\n",
            encoding="utf-8",
        )

        from maestro.workflow.loader import load_workflow
        wd = load_workflow(wf)
        config = ServiceConfig.from_workflow(wd)
        assert config.rtk.enabled is True
        assert config.rtk.mode == "hook"
        assert config.rtk.binary == "/usr/local/bin/rtk"

    def test_unsupported_backend_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "x")
        wf = tmp_path / "WORKFLOW.md"
        wf.write_text("---\nbackend: gemini\ntracker:\n  api_key: x\n---\nP\n", encoding="utf-8")

        from maestro.workflow.loader import load_workflow
        wd = load_workflow(wf)
        with pytest.raises(ConfigError, match="Unsupported backend"):
            ServiceConfig.from_workflow(wd)


# ---------------------------------------------------------------------------
# validate_dispatch_config
# ---------------------------------------------------------------------------

class TestValidateDispatch:
    def _base_config(self, backend="cursor", **overrides):
        from maestro.workflow.config import (
            TrackerConfig, PollingConfig, WorkspaceConfig, HooksConfig,
            CursorConfig, AgentConfig, ServerConfig, GitHubConfig,
        )
        kwargs = dict(
            tracker=TrackerConfig(kind="linear", api_key="key"),
            polling=PollingConfig(),
            workspace=WorkspaceConfig(),
            hooks=HooksConfig(),
            cursor=CursorConfig(),
            claude_code=None,
            agent=AgentConfig(),
            server=ServerConfig(),
            github=GitHubConfig(),
            rtk=RtkConfig(),
            prompt_template="test",
            workflow_path=Path("."),
            backend=backend,
        )
        kwargs.update(overrides)
        return ServiceConfig(**kwargs)

    def test_cursor_backend_ok(self):
        validate_dispatch_config(self._base_config())

    def test_claude_code_backend_ok(self):
        cfg = self._base_config(
            backend="claude_code",
            claude_code=ClaudeCodeConfig(api_key="anthro-key"),
        )
        validate_dispatch_config(cfg)

    def test_claude_code_backend_missing_section(self):
        cfg = self._base_config(backend="claude_code", claude_code=None)
        with pytest.raises(ConfigError, match="claude_code section is required"):
            validate_dispatch_config(cfg)

    def test_claude_code_backend_missing_api_key(self):
        cfg = self._base_config(
            backend="claude_code",
            claude_code=ClaudeCodeConfig(api_key=None),
        )
        with pytest.raises(ConfigError, match="api_key is required"):
            validate_dispatch_config(cfg)


# ---------------------------------------------------------------------------
# resolved_hooks
# ---------------------------------------------------------------------------

class TestResolvedHooks:
    def _base_config(self, backend, hooks_kwargs):
        from maestro.workflow.config import (
            TrackerConfig, PollingConfig, WorkspaceConfig, HooksConfig,
            CursorConfig, AgentConfig, ServerConfig, GitHubConfig, RtkConfig,
        )
        return ServiceConfig(
            tracker=TrackerConfig(kind="linear", api_key="k"),
            polling=PollingConfig(),
            workspace=WorkspaceConfig(),
            hooks=HooksConfig(**hooks_kwargs),
            cursor=CursorConfig(),
            claude_code=ClaudeCodeConfig(api_key="k") if backend == "claude_code" else None,
            agent=AgentConfig(),
            server=ServerConfig(),
            github=GitHubConfig(),
            rtk=RtkConfig(),
            prompt_template="",
            workflow_path=Path("."),
            backend=backend,
        )

    def test_cursor_backend_returns_generic_hooks(self):
        cfg = self._base_config("cursor", {"after_create": "generic-script"})
        resolved = cfg.resolved_hooks()
        assert resolved.after_create == "generic-script"

    def test_claude_code_backend_overrides_hooks(self):
        cfg = self._base_config(
            "claude_code",
            {
                "after_create": "generic-create",
                "claude_code_after_create": "cc-create",
            },
        )
        resolved = cfg.resolved_hooks()
        assert resolved.after_create == "cc-create"

    def test_claude_code_backend_falls_back_to_generic(self):
        cfg = self._base_config(
            "claude_code",
            {"before_run": "generic-before"},
        )
        resolved = cfg.resolved_hooks()
        assert resolved.before_run == "generic-before"


# ---------------------------------------------------------------------------
# ClaudeCodeRunner._build_command
# ---------------------------------------------------------------------------

class TestClaudeCodeRunnerCommand:
    def test_basic_command(self):
        from maestro.agent.claude_code import ClaudeCodeRunner
        cfg = ClaudeCodeConfig(model="opus-4.6", api_key="key")
        runner = ClaudeCodeRunner(cfg)
        with patch.object(runner, "_resolve_executable", return_value="/usr/bin/claude"):
            cmd = runner._build_command(Path("/ws"), "do stuff", None)
        assert cmd[0] == "/usr/bin/claude"
        assert "-p" in cmd
        assert "--verbose" in cmd
        # Prompt is passed via stdin, not as a positional arg
        assert "do stuff" not in cmd
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "opus-4.6"
        assert "--dangerously-skip-permissions" not in cmd

    def test_skip_permissions(self):
        from maestro.agent.claude_code import ClaudeCodeRunner
        cfg = ClaudeCodeConfig(skip_permissions=True, api_key="key")
        runner = ClaudeCodeRunner(cfg)
        with patch.object(runner, "_resolve_executable", return_value="/usr/bin/claude"):
            cmd = runner._build_command(Path("/ws"), "prompt", None)
        assert "--dangerously-skip-permissions" in cmd
        assert "--allowedTools" not in cmd

    def test_allowed_tools_per_flag(self):
        from maestro.agent.claude_code import ClaudeCodeRunner
        cfg = ClaudeCodeConfig(
            skip_permissions=False,
            allowed_tools=["Bash", "Read", "Write"],
            api_key="key",
        )
        runner = ClaudeCodeRunner(cfg)
        with patch.object(runner, "_resolve_executable", return_value="/usr/bin/claude"):
            cmd = runner._build_command(Path("/ws"), "prompt", None)
        # Each tool gets its own --allowedTools flag
        tool_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "--allowedTools"]
        assert tool_args == ["Bash", "Read", "Write"]

    def test_max_budget_and_turns(self):
        from maestro.agent.claude_code import ClaudeCodeRunner
        cfg = ClaudeCodeConfig(
            max_budget_usd=10.0,
            max_turns_per_invocation=5,
            api_key="key",
        )
        runner = ClaudeCodeRunner(cfg)
        with patch.object(runner, "_resolve_executable", return_value="/usr/bin/claude"):
            cmd = runner._build_command(Path("/ws"), "prompt", None)
        assert "--max-budget-usd" in cmd
        assert cmd[cmd.index("--max-budget-usd") + 1] == "10.00"
        assert "--max-turns" in cmd
        assert cmd[cmd.index("--max-turns") + 1] == "5"

    def test_resume(self):
        from maestro.agent.claude_code import ClaudeCodeRunner
        cfg = ClaudeCodeConfig(api_key="key")
        runner = ClaudeCodeRunner(cfg)
        with patch.object(runner, "_resolve_executable", return_value="/usr/bin/claude"):
            cmd = runner._build_command(Path("/ws"), "prompt", "ses-123")
        assert "--resume" in cmd
        assert cmd[cmd.index("--resume") + 1] == "ses-123"

    def test_model_override(self):
        from maestro.agent.claude_code import ClaudeCodeRunner
        cfg = ClaudeCodeConfig(model="sonnet", api_key="key")
        runner = ClaudeCodeRunner(cfg)
        with patch.object(runner, "_resolve_executable", return_value="/usr/bin/claude"):
            cmd = runner._build_command(Path("/ws"), "prompt", None, model_override="opus")
        assert cmd[cmd.index("--model") + 1] == "opus"

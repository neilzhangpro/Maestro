from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from maestro.linear.models import Issue
from maestro.worker.worker import Worker
from maestro.workflow.config import (
    AgentConfig,
    ClaudeCodeConfig,
    CursorConfig,
    GitHubConfig,
    HooksConfig,
    PollingConfig,
    RtkConfig,
    ServerConfig,
    ServiceConfig,
    TrackerConfig,
    WorkspaceConfig,
)


def _make_config(tmp_path: Path, *, backend: str = "claude_code", rtk_enabled: bool = True) -> ServiceConfig:
    return ServiceConfig(
        tracker=TrackerConfig(kind="linear", api_key="linear-key"),
        polling=PollingConfig(),
        workspace=WorkspaceConfig(root=tmp_path),
        hooks=HooksConfig(),
        cursor=CursorConfig(),
        claude_code=ClaudeCodeConfig(api_key="anthropic-key"),
        agent=AgentConfig(),
        server=ServerConfig(),
        github=GitHubConfig(),
        rtk=RtkConfig(enabled=rtk_enabled, binary="rtk"),
        prompt_template="prompt",
        workflow_path=tmp_path / "WORKFLOW.md",
        backend=backend,
    )


def _make_issue() -> Issue:
    return Issue(
        id="issue-1",
        identifier="MAE-1",
        title="Test issue",
        description="desc",
        state="Todo",
    )


def test_collect_rtk_stats_returns_empty_when_disabled(tmp_path: Path) -> None:
    worker = Worker(
        config=_make_config(tmp_path, rtk_enabled=False),
        issue=_make_issue(),
        attempt=None,
        on_event=lambda *_args: None,
        on_exit=lambda *_args: None,
    )
    assert worker._collect_rtk_stats(tmp_path) == {}


def test_collect_rtk_stats_parses_json(monkeypatch, tmp_path: Path) -> None:
    worker = Worker(
        config=_make_config(tmp_path, rtk_enabled=True),
        issue=_make_issue(),
        attempt=None,
        on_event=lambda *_args: None,
        on_exit=lambda *_args: None,
    )

    monkeypatch.setattr("maestro.worker.worker.shutil.which", lambda _cmd: "/usr/local/bin/rtk")
    monkeypatch.setattr(
        "maestro.worker.worker.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='{"saved_tokens": 321}', stderr=""),
    )

    assert worker._collect_rtk_stats(tmp_path) == {"saved_tokens": 321}


def test_collect_rtk_stats_skips_non_claude_backends(tmp_path: Path) -> None:
    worker = Worker(
        config=_make_config(tmp_path, backend="cursor", rtk_enabled=True),
        issue=_make_issue(),
        attempt=None,
        on_event=lambda *_args: None,
        on_exit=lambda *_args: None,
    )
    assert worker._collect_rtk_stats(tmp_path) == {}

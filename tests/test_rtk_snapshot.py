from __future__ import annotations

from pathlib import Path

from maestro.learning.recorder import RunRecord
from maestro.orchestrator.scheduler import Scheduler, _extract_saved_tokens
from maestro.orchestrator.state import OrchestratorState
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


def _make_config(tmp_path: Path, *, enabled: bool = True) -> ServiceConfig:
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
        rtk=RtkConfig(enabled=enabled, binary="rtk"),
        prompt_template="prompt",
        workflow_path=tmp_path / "WORKFLOW.md",
        backend="claude_code",
    )


class _FakeRecorder:
    def __init__(self, records):
        self._records = records

    def load_recent(self, limit: int = 200):
        return self._records[-limit:]


def test_extract_saved_tokens_prefers_known_keys() -> None:
    payload = {"summary": {"saved_tokens": 123}, "noise": {"total": 99}}
    assert _extract_saved_tokens(payload) == 123


def test_scheduler_snapshot_includes_rtk_metrics(tmp_path: Path) -> None:
    scheduler = Scheduler.__new__(Scheduler)
    scheduler.config = _make_config(tmp_path, enabled=True)
    scheduler.state = OrchestratorState()
    scheduler._run_recorder = _FakeRecorder([
        RunRecord(
            issue_identifier="MAE-1",
            timestamp_utc="2026-03-19T00:00:00+00:00",
            turn=1,
            attempt=None,
            success=True,
            error=None,
            duration_ms=1000,
            rtk_stats={"summary": {"estimated_tokens_saved": 4567}},
        ),
    ])

    snapshot = scheduler.snapshot()
    assert snapshot["rtk"]["enabled"] is True
    assert snapshot["rtk"]["estimated_tokens_saved"] == 4567
    assert snapshot["rtk"]["last_snapshot_at"] == "2026-03-19T00:00:00+00:00"


def test_scheduler_snapshot_disables_rtk_metrics_when_off(tmp_path: Path) -> None:
    scheduler = Scheduler.__new__(Scheduler)
    scheduler.config = _make_config(tmp_path, enabled=False)
    scheduler.state = OrchestratorState()
    scheduler._run_recorder = _FakeRecorder([])

    snapshot = scheduler.snapshot()
    assert snapshot["rtk"]["enabled"] is False
    assert snapshot["rtk"]["estimated_tokens_saved"] == 0

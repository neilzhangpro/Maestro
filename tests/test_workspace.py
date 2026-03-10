from pathlib import Path

import pytest

from maestro.workspace.hooks import WorkspaceHooks
from maestro.workspace.manager import WorkspaceManager, WorkspaceError, sanitize_workspace_key


class RecordingHooks(WorkspaceHooks):
    def __init__(self) -> None:
        self.events: list[tuple[str, Path]] = []

    def after_create(self, workspace_path: Path) -> None:
        self.events.append(("after_create", workspace_path))

    def before_run(self, workspace_path: Path) -> None:
        self.events.append(("before_run", workspace_path))

    def after_run(self, workspace_path: Path) -> None:
        self.events.append(("after_run", workspace_path))


def test_sanitize_workspace_key_replaces_unsafe_characters() -> None:
    assert sanitize_workspace_key("ABC-123 / feature") == "ABC-123_feature"


def test_workspace_manager_creates_and_reuses_directories(tmp_path: Path) -> None:
    hooks = RecordingHooks()
    manager = WorkspaceManager(tmp_path / "workspaces", hooks=hooks)

    first = manager.prepare_workspace("ABC-123 / feature")
    second = manager.prepare_workspace("ABC-123 / feature")
    manager.run_before(first)
    manager.run_after(first)

    assert first.created_now is True
    assert second.created_now is False
    assert first.path.exists()
    assert first.path == second.path
    assert [event for event, _ in hooks.events] == ["after_create", "before_run", "after_run"]


def test_workspace_manager_rejects_empty_identifier(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")

    with pytest.raises(WorkspaceError):
        manager.workspace_path_for("...")

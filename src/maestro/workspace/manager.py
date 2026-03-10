"""Workspace lifecycle and safety helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from maestro.workspace.hooks import WorkspaceHooks


_INVALID_SEGMENT_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class WorkspaceError(ValueError):
    """Raised when a workspace cannot be created safely."""


@dataclass(slots=True, frozen=True)
class Workspace:
    path: Path
    created_now: bool
    key: str


def sanitize_workspace_key(identifier: str) -> str:
    cleaned = _INVALID_SEGMENT_CHARS.sub("_", identifier.strip())
    cleaned = cleaned.strip("._")
    if not cleaned:
        raise WorkspaceError("Workspace identifier is empty after sanitization.")
    return cleaned


class WorkspaceManager:
    """Create and reuse issue-specific workspace directories."""

    def __init__(self, workspace_root: str | Path, hooks: WorkspaceHooks | None = None) -> None:
        root = Path(workspace_root).expanduser()
        self.workspace_root = root if root.is_absolute() else (Path.cwd() / root).resolve()
        self.hooks = hooks or WorkspaceHooks()

    def workspace_path_for(self, issue_identifier: str) -> Path:
        key = sanitize_workspace_key(issue_identifier)
        path = (self.workspace_root / key).resolve()
        self._ensure_within_root(path)
        return path

    def prepare_workspace(self, issue_identifier: str) -> Workspace:
        path = self.workspace_path_for(issue_identifier)
        created_now = False
        if not path.exists():
            path.mkdir(parents=True, exist_ok=False)
            created_now = True
            self.hooks.after_create(path)
        return Workspace(path=path, created_now=created_now, key=path.name)

    def run_before(self, workspace: Workspace) -> None:
        self._ensure_within_root(workspace.path)
        self.hooks.before_run(workspace.path)

    def run_after(self, workspace: Workspace) -> None:
        self._ensure_within_root(workspace.path)
        self.hooks.after_run(workspace.path)

    def _ensure_within_root(self, path: Path) -> None:
        root = self.workspace_root.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError(f"Workspace path escapes root: {path}") from exc

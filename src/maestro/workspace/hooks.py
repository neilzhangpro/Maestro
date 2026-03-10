"""Lifecycle hooks for workspace operations.

Hooks are shell scripts defined in WORKFLOW.md that run at various points in the
workspace lifecycle.  They execute via ``sh -lc`` with the workspace as cwd.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class HookError(RuntimeError):
    """Raised when a fatal hook (after_create, before_run) fails."""


class WorkspaceHooks:
    """Extension points around workspace creation and execution."""

    def after_create(self, workspace_path: Path) -> None:
        """Run once after a new workspace directory is created."""

    def before_run(self, workspace_path: Path) -> None:
        """Run before a task starts in the workspace."""

    def after_run(self, workspace_path: Path) -> None:
        """Run after a task finishes in the workspace."""

    def before_remove(self, workspace_path: Path) -> None:
        """Run before workspace deletion if the directory exists."""


class ShellHooks(WorkspaceHooks):
    """Execute WORKFLOW.md hook scripts via ``sh -lc``."""

    def __init__(
        self,
        *,
        after_create_script: str | None = None,
        before_run_script: str | None = None,
        after_run_script: str | None = None,
        before_remove_script: str | None = None,
        timeout_ms: int = 60_000,
    ) -> None:
        self._after_create = after_create_script
        self._before_run = before_run_script
        self._after_run = after_run_script
        self._before_remove = before_remove_script
        self._timeout_s = max(timeout_ms, 1000) / 1000

    def after_create(self, workspace_path: Path) -> None:
        if self._after_create:
            self._exec(self._after_create, workspace_path, fatal=True, label="after_create")

    def before_run(self, workspace_path: Path) -> None:
        if self._before_run:
            self._exec(self._before_run, workspace_path, fatal=True, label="before_run")

    def after_run(self, workspace_path: Path) -> None:
        if self._after_run:
            self._exec(self._after_run, workspace_path, fatal=False, label="after_run")

    def before_remove(self, workspace_path: Path) -> None:
        if self._before_remove:
            self._exec(self._before_remove, workspace_path, fatal=False, label="before_remove")

    def _exec(
        self, script: str, cwd: Path, *, fatal: bool, label: str,
    ) -> None:
        log.info("Running hook %s in %s", label, cwd)
        try:
            result = subprocess.run(
                ["sh", "-lc", script],
                cwd=str(cwd),
                timeout=self._timeout_s,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                stderr_tail = (result.stderr or "").strip()[-300:]
                msg = f"Hook {label} exited {result.returncode}: {stderr_tail}"
                if fatal:
                    raise HookError(msg)
                log.warning(msg)
            else:
                log.debug("Hook %s succeeded.", label)
        except subprocess.TimeoutExpired as exc:
            msg = f"Hook {label} timed out after {self._timeout_s}s"
            if fatal:
                raise HookError(msg) from exc
            log.warning(msg)

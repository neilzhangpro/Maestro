"""State definition for the Maestro orchestration pipeline."""

from __future__ import annotations

from typing import Any, TypedDict


class MaestroState(TypedDict, total=False):
    issue_id: str
    issue: dict[str, Any] | None
    workspace_path: str | None
    prompt: str | None
    execute_result: dict[str, Any] | None
    linear_updated: bool
    error: str | None
    run_id: str | None
    status: str  # pending | running | completed | failed

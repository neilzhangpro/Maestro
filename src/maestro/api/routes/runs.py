"""Run routes — trigger single-issue dispatch, list runs from orchestrator state."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

if TYPE_CHECKING:
    from maestro.api.run_manager import RunManager
    from maestro.orchestrator.scheduler import Scheduler
    from maestro.workflow.config import ServiceConfig

router = APIRouter(prefix="/api/runs", tags=["runs"])
log = logging.getLogger(__name__)

_config: ServiceConfig | None = None
_scheduler: Scheduler | None = None
_run_manager: RunManager | None = None


def init(
    config: "ServiceConfig",
    scheduler: "Scheduler",
    run_manager: "RunManager",
) -> None:
    global _config, _scheduler, _run_manager
    _config = config
    _scheduler = scheduler
    _run_manager = run_manager


class TriggerRequest(BaseModel):
    issue_id: str


@router.post("")
def trigger_run(body: TriggerRequest) -> dict[str, Any]:
    """Manually dispatch a single issue through the scheduler."""
    if _scheduler is None or _config is None:
        raise HTTPException(500, "Server not initialised")

    from maestro.config import LinearConfig
    from maestro.linear.client import LinearClient, LinearError

    lc = LinearConfig(
        api_key=_config.tracker.api_key,
        api_url=_config.tracker.endpoint,
        project_slug=_config.tracker.project_slug or None,
        team_id=_config.tracker.team_id,
        assignee=_config.tracker.assignee,
        active_states=_config.tracker.active_states,
        terminal_states=_config.tracker.terminal_states,
        timeout_s=_config.tracker.timeout_s,
    )
    try:
        with LinearClient(lc) as client:
            issue = client.fetch_issue(body.issue_id)
    except LinearError as exc:
        raise HTTPException(404, str(exc)) from exc

    if _scheduler.state.is_claimed(issue.id):
        raise HTTPException(409, f"Issue {body.issue_id} is already running or queued.")

    _scheduler._dispatch_issue(issue, attempt=None)
    return {"status": "dispatched", "issue": body.issue_id}


@router.delete("/{issue_id}")
def cancel_run(issue_id: str) -> dict[str, Any]:
    """Cancel a running worker by issue ID or identifier (e.g. NOV-300)."""
    if _scheduler is None or _config is None:
        raise HTTPException(500, "Server not initialised")

    resolved_id = issue_id
    for entry in _scheduler.state.running.values():
        if entry.identifier == issue_id:
            resolved_id = entry.issue_id
            break

    if not _scheduler.cancel_worker(resolved_id):
        raise HTTPException(404, f"No running worker found for '{issue_id}'.")

    return {"status": "cancel_requested", "issue": issue_id}


@router.get("")
def list_runs() -> list[dict[str, Any]]:
    if _run_manager is None:
        raise HTTPException(500, "Server not initialised")
    return _run_manager.list_runs()


@router.get("/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    if _run_manager is None:
        raise HTTPException(500, "Server not initialised")
    run = _run_manager.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run.to_dict()

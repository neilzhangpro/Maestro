"""Issue routes — list, detail, state updates (adapted for ServiceConfig)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from maestro.config import LinearConfig
from maestro.linear.client import LinearClient, LinearError
from maestro.workflow.config import ServiceConfig

router = APIRouter(prefix="/api/issues", tags=["issues"])

_config: ServiceConfig | None = None


def init(config: ServiceConfig) -> None:
    global _config
    _config = config


def _make_linear_config() -> LinearConfig:
    if _config is None:
        raise HTTPException(500, "Server not initialised")
    return LinearConfig(
        api_key=_config.tracker.api_key,
        api_url=_config.tracker.endpoint,
        project_slug=_config.tracker.project_slug or None,
        team_id=_config.tracker.team_id,
        assignee=_config.tracker.assignee,
        active_states=_config.tracker.active_states,
        terminal_states=_config.tracker.terminal_states,
        timeout_s=_config.tracker.timeout_s,
    )


def _issue_dict(i) -> dict[str, Any]:
    return {
        "id": i.id,
        "identifier": i.identifier,
        "title": i.title,
        "description": i.description,
        "state": i.state,
        "state_id": i.state_id,
        "team_key": i.team_key,
        "priority": i.priority,
        "labels": i.labels,
        "url": i.url,
    }


class StateUpdate(BaseModel):
    state_name: str


@router.get("")
def list_issues(state: str | None = None) -> list[dict[str, Any]]:
    lc = _make_linear_config()
    states = [state] if state else None
    with LinearClient(lc) as client:
        issues = client.fetch_issues(state_names=states)
    return [_issue_dict(i) for i in issues]


@router.get("/all")
def list_all_issues() -> list[dict[str, Any]]:
    """List issues across all workflow states."""
    lc = _make_linear_config()
    assert _config is not None
    all_states = (
        _config.tracker.active_states
        + _config.tracker.terminal_states
        + ["Backlog"]
    )
    with LinearClient(lc) as client:
        issues = client.fetch_issues(state_names=all_states)
    return [_issue_dict(i) for i in issues]


@router.get("/{issue_ref}")
def get_issue(issue_ref: str) -> dict[str, Any]:
    lc = _make_linear_config()
    try:
        with LinearClient(lc) as client:
            issue = client.fetch_issue(issue_ref)
    except LinearError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _issue_dict(issue)


@router.patch("/{issue_ref}/state")
def update_issue_state(issue_ref: str, body: StateUpdate) -> dict[str, Any]:
    lc = _make_linear_config()
    try:
        with LinearClient(lc) as client:
            issue = client.fetch_issue(issue_ref)
            if not issue.team_key:
                raise HTTPException(400, "Issue has no team key")
            state_id = client.find_state_id(issue.team_key, body.state_name)
            if not state_id:
                raise HTTPException(404, f"State '{body.state_name}' not found")
            result = client.update_issue_state(issue.id, state_id)
    except LinearError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": result.success, "issue_id": result.issue_id}

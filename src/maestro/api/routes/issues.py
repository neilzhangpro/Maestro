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


class CommentCreate(BaseModel):
    body: str


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
    all_states = list(dict.fromkeys(
        _config.tracker.active_states
        + _config.tracker.handoff_states
        + ["Backlog"]
    ))
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


@router.post("/{issue_ref}/comment")
def create_issue_comment(issue_ref: str, body: CommentCreate) -> dict[str, Any]:
    lc = _make_linear_config()
    try:
        with LinearClient(lc) as client:
            issue = client.fetch_issue(issue_ref)
            comment_id = client.create_comment(issue.id, body.body)
    except LinearError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "comment_id": comment_id}


@router.post("/{issue_ref}/mark-pr-ready")
def mark_pr_ready(issue_ref: str) -> dict[str, Any]:
    """Find the draft PR for an issue and mark it as ready for review."""
    if _config is None:
        raise HTTPException(500, "Server not initialised")

    gh = _config.github
    if not gh.token or not gh.owner or not gh.repo:
        raise HTTPException(400, "GitHub configuration incomplete")

    lc = _make_linear_config()
    try:
        with LinearClient(lc) as client:
            issue = client.fetch_issue(issue_ref)
    except LinearError as exc:
        raise HTTPException(404, str(exc)) from exc

    from maestro.github.client import GitHubClient
    with GitHubClient(gh.token) as github:
        pr = None
        if issue.branch_name:
            pr = github.find_pr_for_branch(gh.owner, gh.repo, issue.branch_name)
        if pr is None:
            pr = github.find_pr_by_identifier(gh.owner, gh.repo, issue.identifier)
        if pr is None:
            raise HTTPException(404, f"No PR found for {issue_ref}")

        if pr.merged:
            return {"success": True, "pr_number": pr.number, "note": "PR already merged"}

        ok = github.mark_pr_ready_for_review(gh.owner, gh.repo, pr.number)
        if not ok:
            raise HTTPException(500, f"Failed to mark PR #{pr.number} as ready for review")

    return {"success": True, "pr_number": pr.number, "html_url": pr.html_url}

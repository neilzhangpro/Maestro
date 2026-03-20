"""Issue routes — list, detail, state updates (adapted for ServiceConfig)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from maestro.config import LinearConfig
from maestro.github.client import GitHubError
from maestro.linear.client import LinearClient, LinearError
from maestro.workflow.config import ServiceConfig
from maestro.workspace.manager import sanitize_workspace_key

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


class SubmitReview(BaseModel):
    e2e_summary: str | None = None


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


@router.post("/{issue_ref}/submit-review")
def submit_review(issue_ref: str, body: SubmitReview | None = None) -> dict[str, Any]:
    """Commit, push, and create/ready a PR after Human Review E2E passes."""
    if _config is None:
        raise HTTPException(500, "Server not initialised")

    gh = _config.github
    if not gh.token or not gh.owner or not gh.repo:
        raise HTTPException(400, "GitHub configuration incomplete")

    lc = _make_linear_config()
    try:
        with LinearClient(lc) as client:
            issue = client.fetch_issue(issue_ref)
            if not issue.team_key:
                raise HTTPException(400, "Issue has no team key")

            workspace = _workspace_path_for(issue.identifier)
            if not workspace.exists():
                raise HTTPException(400, f"Workspace not found: {workspace}")
            branch = _ensure_submit_workspace(workspace, issue, gh.owner, gh.repo)
            _git_remove_generated_support_files(workspace)

            has_changes = _git_has_changes(workspace)
            if has_changes:
                commit_message = f"{issue.identifier}: human-reviewed changes"
                _git_ensure_identity(workspace)
                _git(workspace, "add", "-A")
                _git(workspace, "commit", "-m", commit_message)
            elif not _git_has_commits(workspace):
                # Bootstrap case: ensure the branch has at least one commit before push.
                _git_ensure_identity(workspace)
                _git(workspace, "commit", "--allow-empty", "-m", f"{issue.identifier}: initialize branch")

            from maestro.github.client import GitHubClient
            bootstrap_reason: str | None = None
            with GitHubClient(gh.token) as github:
                base_branch = _ensure_mainline_branch(workspace, github, gh.owner, gh.repo, gh.token, branch)
                bootstrap_only = False
                if not _git_has_common_history(workspace, branch, base_branch):
                    _git_transplant_branch_onto(workspace, branch, base_branch)
                    bootstrap_reason = (
                        f"Transplanted `{branch}` onto normalized trunk `{base_branch}` to establish shared history."
                    )
                    _git_push_authenticated(
                        workspace, gh.owner, gh.repo, gh.token, branch, force_with_lease=True,
                    )
                else:
                    _git_push_authenticated(workspace, gh.owner, gh.repo, gh.token, branch)
                pr = github.find_pr_for_branch(gh.owner, gh.repo, branch)
                if pr is None:
                    pr = github.find_pr_by_identifier(gh.owner, gh.repo, issue.identifier)
                if pr is None:
                    pr = github.create_pull_request(
                        gh.owner,
                        gh.repo,
                        title=f"{issue.identifier}: {issue.title}",
                        body=_build_pr_body(issue, body.e2e_summary if body else None),
                        head=branch,
                        base=base_branch,
                        draft=False,
                    )
                elif pr is not None and pr.draft:
                    github.mark_pr_ready_for_review(gh.owner, gh.repo, pr.number)

                if gh.ci_watch_states:
                    review_state = gh.ci_watch_states[0]
                    review_state_id = client.find_state_id(issue.team_key, review_state)
                    if not review_state_id:
                        review_state = None
                    else:
                        client.update_issue_state(issue.id, review_state_id)
                        client.create_comment(
                            issue.id,
                            (
                                f"**E2E Test Passed** ✅\n\n"
                                f"Changes have been committed and pushed from `{workspace}`.\n\n"
                                f"PR: [#{pr.number}]({pr.html_url})\n\n"
                                f"Base branch: `{base_branch}`\n"
                                f"{bootstrap_reason + chr(10) if bootstrap_reason else ''}"
                                f"Moving to `{review_state}` and waiting for CI before merge."
                            ),
                        )
                        return {
                            "success": True,
                            "branch": branch,
                            "workspace": str(workspace),
                            "pr_number": pr.number,
                            "html_url": pr.html_url,
                            "committed": has_changes,
                            "bootstrap_only": False,
                            "bootstrap_reason": bootstrap_reason,
                            "awaiting_ci": True,
                            "state": review_state,
                        }

                if gh.ci_watch_states and not review_state_id:
                    bootstrap_reason = (
                        (bootstrap_reason + "\n") if bootstrap_reason else ""
                    ) + "Linear state `In Review` is not configured; merged immediately instead of waiting for CI."

                github.merge_pull_request(gh.owner, gh.repo, pr.number)
                done_id = client.find_state_id(issue.team_key, "Done")
                if not done_id:
                    raise HTTPException(404, "State 'Done' not found")
                client.update_issue_state(issue.id, done_id)
                client.create_comment(
                    issue.id,
                    (
                        f"**E2E Test Passed** ✅\n\n"
                        f"Changes have been committed and pushed from `{workspace}`.\n\n"
                        f"PR: [#{pr.number}]({pr.html_url})\n\n"
                        f"Base branch: `{base_branch}`\n"
                        f"{bootstrap_reason + chr(10) if bootstrap_reason else ''}"
                        "PR has been merged automatically."
                    ),
                )
    except GitHubError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LinearError as exc:
        raise HTTPException(400, str(exc)) from exc

    return {
        "success": True,
        "branch": branch,
        "workspace": str(workspace),
        "pr_number": pr.number if not bootstrap_only and pr else None,
        "html_url": pr.html_url if not bootstrap_only and pr else None,
        "committed": has_changes,
        "bootstrap_only": bootstrap_only,
        "bootstrap_reason": bootstrap_reason,
        "awaiting_ci": False,
        "state": "Done",
    }


def _workspace_path_for(issue_identifier: str) -> Path:
    if _config is None:
        raise HTTPException(500, "Server not initialised")
    return (Path(_config.workspace.root) / sanitize_workspace_key(issue_identifier)).resolve()


def _git(workspace: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=workspace,
            text=True,
            capture_output=True,
            check=True,
            env=os.environ.copy(),
        )
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "").strip() or f"git {' '.join(args)} failed"
        raise HTTPException(400, message) from exc
    return (proc.stdout or "").strip()


def _git_current_branch(workspace: Path) -> str:
    return _git(workspace, "rev-parse", "--abbrev-ref", "HEAD").strip()


def _git_has_changes(workspace: Path) -> bool:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=workspace,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    return bool((proc.stdout or "").strip())


def _is_git_repo(workspace: Path) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=workspace,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    return proc.returncode == 0


def _git_has_commits(workspace: Path) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=workspace,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    return proc.returncode == 0


def _ensure_submit_workspace(workspace: Path, issue: Any, owner: str, repo: str) -> str:
    branch = _desired_branch_name(issue)
    if not _is_git_repo(workspace):
        _git_init_repo(workspace, branch, owner, repo)
        return branch

    _git_set_remote(workspace, owner, repo)
    current_branch = _git_current_branch(workspace)
    if current_branch in ("HEAD", "", "main", "master"):
        _git_checkout_branch(workspace, branch)
        return branch
    return current_branch


def _git_init_repo(workspace: Path, branch: str, owner: str, repo: str) -> None:
    _git(workspace, "init")
    _git_set_remote(workspace, owner, repo)
    _git_checkout_branch(workspace, branch)


def _git_set_remote(workspace: Path, owner: str, repo: str) -> None:
    remote = f"https://github.com/{owner}/{repo}.git"
    proc = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=workspace,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    if proc.returncode == 0:
        _git(workspace, "remote", "set-url", "origin", remote)
    else:
        _git(workspace, "remote", "add", "origin", remote)


def _git_checkout_branch(workspace: Path, branch: str) -> None:
    proc = subprocess.run(
        ["git", "checkout", branch],
        cwd=workspace,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    if proc.returncode == 0:
        return
    _git(workspace, "checkout", "-b", branch)


def _git_ensure_identity(workspace: Path) -> None:
    name = subprocess.run(
        ["git", "config", "user.name"],
        cwd=workspace,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    ).stdout.strip()
    email = subprocess.run(
        ["git", "config", "user.email"],
        cwd=workspace,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    ).stdout.strip()
    if not name:
        _git(workspace, "config", "user.name", "Maestro Bot")
    if not email:
        _git(workspace, "config", "user.email", "maestro@example.local")


def _git_push_authenticated(
    workspace: Path,
    owner: str,
    repo: str,
    token: str,
    branch: str,
    *,
    force_with_lease: bool = False,
) -> None:
    remote = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
    args = ["push"]
    if force_with_lease:
        args.append("--force-with-lease")
    args.extend(["-u", remote, branch])
    try:
        _git(workspace, *args)
    except HTTPException as exc:
        detail = exc.detail.replace(token, "***") if isinstance(exc.detail, str) else exc.detail
        raise HTTPException(exc.status_code, detail) from exc


def _desired_branch_name(issue: Any) -> str:
    if getattr(issue, "branch_name", None):
        return str(issue.branch_name)
    title = str(getattr(issue, "title", "") or "")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = slug[:48] if slug else "work"
    return f"feat/{issue.identifier}-{slug}"


def _build_pr_body(issue: Any, e2e_summary: str | None) -> str:
    summary = (e2e_summary or "Manual E2E review passed.").strip()
    return (
        f"## Summary\n"
        f"- Linear issue: {issue.identifier}\n"
        f"- Manual review status: passed\n"
        f"- E2E summary: {summary}\n"
    )


def _branch_looks_like_feature(branch: str) -> bool:
    normalized = (branch or "").strip().lower()
    return (
        normalized.startswith(("feat/", "fix/", "chore/", "docs/", "refactor/", "test/"))
        or "/" in normalized
        or bool(re.search(r"[a-z]+-\d+", normalized))
    )


def _is_bootstrap_pr_error(exc: GitHubError, base_branch: str) -> bool:
    message = str(exc).lower()
    return (
        "no history in common" in message
        and _branch_looks_like_feature(base_branch)
    )


def _git_remote_heads(owner: str, repo: str, token: str) -> set[str]:
    remote = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
    proc = subprocess.run(
        ["git", "ls-remote", "--heads", remote],
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    if proc.returncode != 0:
        return set()
    heads: set[str] = set()
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        ref = parts[1].strip()
        prefix = "refs/heads/"
        if ref.startswith(prefix):
            heads.add(ref[len(prefix):])
    return heads


def _git_fetch_authenticated(workspace: Path, owner: str, repo: str, token: str, branch: str) -> None:
    remote = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
    _git(workspace, "fetch", remote, branch)


def _git_create_empty_main_branch(workspace: Path, branch: str) -> None:
    tree_proc = subprocess.run(
        ["git", "mktree"],
        cwd=workspace,
        text=True,
        input="",
        capture_output=True,
        env=os.environ.copy(),
        check=True,
    )
    tree_sha = (tree_proc.stdout or "").strip()
    _git_ensure_identity(workspace)
    commit_proc = subprocess.run(
        ["git", "commit-tree", tree_sha, "-m", "Initialize main branch"],
        cwd=workspace,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
        check=True,
    )
    commit_sha = (commit_proc.stdout or "").strip()
    _git(workspace, "branch", "-f", branch, commit_sha)


def _git_has_common_history(workspace: Path, branch: str, base_branch: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", branch, base_branch],
        cwd=workspace,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def _git_transplant_branch_onto(workspace: Path, branch: str, base_branch: str) -> None:
    """Replay an unrelated feature branch as a single commit on top of the trunk."""
    original_tip = _git(workspace, "rev-parse", branch).strip()
    _git(workspace, "checkout", "-B", branch, base_branch)

    tracked = [
        line.strip()
        for line in _git(workspace, "ls-files").splitlines()
        if line.strip()
    ]
    if tracked:
        _git(workspace, "rm", "-r", "--ignore-unmatch", "--", *tracked)

    proc = subprocess.run(
        ["git", "checkout", original_tip, "--", "."],
        cwd=workspace,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip() or (
            f"Failed to transplant {branch} onto {base_branch}"
        )
        raise HTTPException(400, message)

    _git_remove_generated_support_files(workspace)
    _git(workspace, "add", "-A")
    if not _git_has_changes(workspace):
        return

    _git_ensure_identity(workspace)
    _git(workspace, "commit", "-m", f"Transplant {branch} onto {base_branch}")


def _git_stash_all(workspace: Path) -> str | None:
    proc = subprocess.run(
        ["git", "stash", "push", "--all", "-m", "maestro-submit-review"],
        cwd=workspace,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    if proc.returncode != 0:
        return None
    output = (proc.stdout or "") + (proc.stderr or "")
    if "No local changes to save" in output:
        return None
    ref_proc = subprocess.run(
        ["git", "stash", "list", "--format=%gd", "-n", "1"],
        cwd=workspace,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    ref = (ref_proc.stdout or "").strip()
    return ref or None


def _git_restore_stash(workspace: Path, stash_ref: str) -> None:
    proc = subprocess.run(
        ["git", "stash", "pop", stash_ref],
        cwd=workspace,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    if proc.returncode == 0:
        return
    # If restore fails, keep the stash entry and surface the reason.
    message = (proc.stderr or proc.stdout or "").strip() or f"Failed to restore {stash_ref}"
    raise HTTPException(400, message)


def _git_restore_generated_support_files(workspace: Path) -> None:
    """Reset deterministic workspace support files that are regenerated by hooks."""
    candidates = [
        ".cursor/mcp.json",
        ".cursor/rules",
        ".cursor/skills",
        ".claude/mcp.json",
        "CLAUDE.md",
    ]
    for rel in candidates:
        path = workspace / rel
        if not path.exists():
            continue
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel],
            cwd=workspace,
            text=True,
            capture_output=True,
            env=os.environ.copy(),
        )
        if proc.returncode != 0:
            continue
        subprocess.run(
            ["git", "checkout", "--", rel],
            cwd=workspace,
            text=True,
            capture_output=True,
            env=os.environ.copy(),
        )


def _git_remove_generated_support_files(workspace: Path) -> None:
    """Ensure local agent support files are not carried into project history."""
    candidates = [
        ".cursor",
        ".claude",
        "CLAUDE.md",
        ".maestro",
    ]
    tracked: list[str] = []
    for rel in candidates:
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel],
            cwd=workspace,
            text=True,
            capture_output=True,
            env=os.environ.copy(),
        )
        if proc.returncode == 0:
            tracked.append(rel)
    if tracked:
        _git(workspace, "rm", "-r", "--cached", "--ignore-unmatch", *tracked)

    for rel in candidates:
        path = workspace / rel
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()


def _ensure_mainline_branch(
    workspace: Path,
    github: Any,
    owner: str,
    repo: str,
    token: str,
    branch: str,
) -> str:
    remote_heads = _git_remote_heads(owner, repo, token)
    target = "main"
    if "main" in remote_heads:
        github.set_default_branch(owner, repo, target)
        return target
    if "master" in remote_heads:
        _git_fetch_authenticated(workspace, owner, repo, token, "master")
        _git(workspace, "branch", "-f", target, "FETCH_HEAD")
        _git_push_authenticated(workspace, owner, repo, token, target)
        github.set_default_branch(owner, repo, target)
        return target

    default_branch = github.get_repo_default_branch(owner, repo)
    if default_branch and default_branch in remote_heads and default_branch != branch:
        _git_fetch_authenticated(workspace, owner, repo, token, default_branch)
        _git(workspace, "branch", "-f", target, "FETCH_HEAD")
    else:
        _git_create_empty_main_branch(workspace, target)
    _git_push_authenticated(workspace, owner, repo, token, target)
    github.set_default_branch(owner, repo, target)
    return target

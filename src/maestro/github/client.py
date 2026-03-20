"""GitHub REST API client for PR, merge, and CI status queries."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://api.github.com"


@dataclass(frozen=True)
class PRInfo:
    number: int
    title: str
    state: str
    merged: bool
    draft: bool
    head_sha: str
    html_url: str
    branch: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class CheckResult:
    total: int
    completed: int
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)

    @property
    def all_done(self) -> bool:
        return self.total > 0 and self.completed == self.total

    @property
    def all_passed(self) -> bool:
        return self.all_done and not self.failed

    @property
    def has_failures(self) -> bool:
        return len(self.failed) > 0


class GitHubError(RuntimeError):
    """Raised when a GitHub API call fails."""


class GitHubClient:
    """Thin wrapper around GitHub REST API for PR and CI queries."""

    def __init__(self, token: str, *, timeout_s: int = 15) -> None:
        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout_s,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def find_pr_for_branch(
        self, owner: str, repo: str, branch: str,
    ) -> PRInfo | None:
        try:
            resp = self._client.get(
                f"/repos/{owner}/{repo}/pulls",
                params={"head": f"{owner}:{branch}", "state": "all", "per_page": 1},
            )
            resp.raise_for_status()
            prs = resp.json()
            if not prs:
                return None
            return self._parse_pr(prs[0])
        except Exception:
            log.warning("Failed to find PR for branch %s", branch, exc_info=True)
            return None

    def find_pr_by_identifier(
        self, owner: str, repo: str, identifier: str,
    ) -> PRInfo | None:
        """Search for a PR whose title or branch contains the issue identifier."""
        try:
            resp = self._client.get(
                f"/repos/{owner}/{repo}/pulls",
                params={"state": "all", "per_page": 30, "sort": "updated", "direction": "desc"},
            )
            resp.raise_for_status()
            prs = resp.json()
            identifier_lower = identifier.lower()
            for pr in prs:
                title = (pr.get("title") or "").lower()
                branch = (pr.get("head", {}).get("ref") or "").lower()
                if identifier_lower in title or identifier_lower in branch:
                    return self._parse_pr(pr)
            return None
        except Exception:
            log.warning("Failed to search PRs for %s", identifier, exc_info=True)
            return None

    def get_check_status(
        self, owner: str, repo: str, ref: str,
    ) -> CheckResult:
        try:
            resp = self._client.get(
                f"/repos/{owner}/{repo}/commits/{ref}/check-runs",
                params={"per_page": 100},
            )
            resp.raise_for_status()
            data = resp.json()

            total = data.get("total_count", 0)
            runs = data.get("check_runs", [])

            passed = []
            failed = []
            pending = []
            completed = 0

            for run in runs:
                name = run.get("name", "unknown")
                status = run.get("status", "")
                conclusion = run.get("conclusion")

                if status == "completed":
                    completed += 1
                    if conclusion in ("failure", "timed_out", "cancelled"):
                        failed.append(name)
                    elif conclusion == "success":
                        passed.append(name)
                else:
                    pending.append(name)

            return CheckResult(
                total=total,
                completed=completed,
                passed=passed,
                failed=failed,
                pending=pending,
            )
        except Exception:
            log.warning("Failed to fetch check status for %s", ref, exc_info=True)
            return CheckResult(total=0, completed=0)

    def get_failed_job_logs_summary(
        self, owner: str, repo: str, ref: str,
    ) -> str:
        """Build a human-readable summary of failed CI check annotations."""
        try:
            resp = self._client.get(
                f"/repos/{owner}/{repo}/commits/{ref}/check-runs",
                params={"per_page": 100, "filter": "latest"},
            )
            resp.raise_for_status()
            data = resp.json()

            lines: list[str] = []
            for run in data.get("check_runs", []):
                if run.get("conclusion") not in ("failure", "timed_out"):
                    continue
                name = run.get("name", "unknown")
                output = run.get("output", {})
                title = output.get("title", "")
                summary = output.get("summary", "")
                text = output.get("text", "")
                lines.append(f"## {name}")
                if title:
                    lines.append(f"Title: {title}")
                if summary:
                    lines.append(summary[:500])
                if text:
                    lines.append(text[:1000])
                lines.append("")

            return "\n".join(lines) if lines else "No failure details available from GitHub."
        except Exception:
            log.warning("Failed to fetch failure logs for %s", ref, exc_info=True)
            return "Failed to retrieve CI failure logs."

    def mark_pr_ready_for_review(
        self, owner: str, repo: str, pr_number: int,
    ) -> bool:
        """Convert a draft PR to ready for review using the GraphQL API."""
        try:
            node_id = self._get_pr_node_id(owner, repo, pr_number)
            if not node_id:
                log.warning("Could not get node ID for PR #%d", pr_number)
                return False

            resp = self._client.post(
                "https://api.github.com/graphql",
                json={
                    "query": """
                        mutation MarkReady($id: ID!) {
                          markPullRequestReadyForReview(input: {pullRequestId: $id}) {
                            pullRequest { number state }
                          }
                        }
                    """,
                    "variables": {"id": node_id},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errors"):
                log.warning("GraphQL errors marking PR ready: %s", data["errors"])
                return False
            log.info("PR #%d marked as ready for review.", pr_number)
            return True
        except Exception:
            log.warning("Failed to mark PR #%d as ready for review", pr_number, exc_info=True)
            return False

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        draft: bool = False,
    ) -> PRInfo:
        """Create a new pull request via the REST API."""
        try:
            resp = self._client.post(
                f"/repos/{owner}/{repo}/pulls",
                json={
                    "title": title,
                    "body": body,
                    "head": head,
                    "base": base,
                    "draft": draft,
                },
            )
            resp.raise_for_status()
            return self._parse_pr(resp.json())
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                payload = exc.response.json()
                detail = payload.get("message", "")
                if payload.get("errors"):
                    detail = f"{detail} | {payload['errors']}"
            except Exception:
                detail = exc.response.text
            raise GitHubError(
                f"Failed to create pull request for branch '{head}': {detail or exc}"
            ) from exc
        except Exception as exc:
            raise GitHubError(f"Failed to create pull request for branch '{head}'.") from exc

    def get_repo_default_branch(self, owner: str, repo: str) -> str:
        """Return the repository default branch name."""
        try:
            resp = self._client.get(f"/repos/{owner}/{repo}")
            resp.raise_for_status()
            return resp.json().get("default_branch") or "main"
        except Exception as exc:
            raise GitHubError(f"Failed to read default branch for {owner}/{repo}.") from exc

    def set_default_branch(self, owner: str, repo: str, branch: str) -> None:
        try:
            resp = self._client.patch(
                f"/repos/{owner}/{repo}",
                json={"default_branch": branch},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GitHubError(
                f"Failed to set default branch for {owner}/{repo} to {branch}: {exc.response.text}"
            ) from exc
        except Exception as exc:
            raise GitHubError(
                f"Failed to set default branch for {owner}/{repo} to {branch}."
            ) from exc

    def merge_pull_request(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        method: str = "squash",
    ) -> bool:
        try:
            resp = self._client.put(
                f"/repos/{owner}/{repo}/pulls/{pr_number}/merge",
                json={"merge_method": method},
            )
            resp.raise_for_status()
            return bool(resp.json().get("merged", False))
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise GitHubError(f"Failed to merge PR #{pr_number}: {detail}") from exc
        except Exception as exc:
            raise GitHubError(f"Failed to merge PR #{pr_number}.") from exc

    def _get_pr_node_id(self, owner: str, repo: str, pr_number: int) -> str | None:
        try:
            resp = self._client.get(f"/repos/{owner}/{repo}/pulls/{pr_number}")
            resp.raise_for_status()
            return resp.json().get("node_id")
        except Exception:
            return None

    @staticmethod
    def _parse_pr(data: dict[str, Any]) -> PRInfo:
        head = data.get("head", {})
        created_at = None
        raw_created_at = data.get("created_at")
        if raw_created_at:
            try:
                created_at = datetime.fromisoformat(raw_created_at.replace("Z", "+00:00"))
            except ValueError:
                created_at = None
        return PRInfo(
            number=data["number"],
            title=data.get("title", ""),
            state=data.get("state", "unknown"),
            merged=data.get("merged", False),
            draft=bool(data.get("draft", False)),
            head_sha=head.get("sha", ""),
            html_url=data.get("html_url", ""),
            branch=head.get("ref", ""),
            created_at=created_at,
        )

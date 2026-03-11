"""GitHub REST API client for PR and CI status queries."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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
    head_sha: str
    html_url: str
    branch: str


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

    @staticmethod
    def _parse_pr(data: dict[str, Any]) -> PRInfo:
        head = data.get("head", {})
        return PRInfo(
            number=data["number"],
            title=data.get("title", ""),
            state=data.get("state", "unknown"),
            merged=data.get("merged", False),
            head_sha=head.get("sha", ""),
            html_url=data.get("html_url", ""),
            branch=head.get("ref", ""),
        )

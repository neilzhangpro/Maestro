"""CI Watcher — monitor PRs for issues in CI-watch states and transition automatically.

For each issue in a ``ci_watch_state`` (e.g. "In Review"):
- Find the associated PR on GitHub (by branch name or issue identifier).
- If the PR is merged and CI passed → move to ``ci_pass_target_state`` (e.g. Done).
- If CI failed → move to ``ci_fail_target_state`` (e.g. In Progress) and add a comment
  with the failure summary so the Agent can pick it up for a fix run.
- If CI is still running → skip and check again on the next tick.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from maestro.github.client import GitHubClient
from maestro.linear.client import LinearClient

if TYPE_CHECKING:
    from maestro.linear.models import Issue
    from maestro.workflow.config import GitHubConfig, ServiceConfig

log = logging.getLogger(__name__)


class CIWatcher:
    """Polls GitHub CI status for issues in CI-watch states and transitions them."""

    def __init__(
        self,
        config: "ServiceConfig",
        linear: LinearClient,
    ) -> None:
        self.config = config
        self._linear = linear
        self._github: GitHubClient | None = None

    def _get_github(self) -> GitHubClient | None:
        gh_config = self.config.github
        if not gh_config.token or not gh_config.owner or not gh_config.repo:
            return None
        if self._github is None:
            self._github = GitHubClient(gh_config.token)
        return self._github

    def poll(self) -> None:
        """Run one CI-watch cycle. Called from Scheduler._on_tick."""
        gh_config = self.config.github
        if not gh_config.ci_watch_states:
            return

        github = self._get_github()
        if github is None:
            return

        try:
            issues = self._linear.fetch_issues(state_names=gh_config.ci_watch_states)
        except Exception:
            log.warning("CI watcher: failed to fetch issues in watch states.", exc_info=True)
            return

        if not issues:
            return

        log.debug("CI watcher: checking %d issue(s) in %s", len(issues), gh_config.ci_watch_states)

        for issue in issues:
            try:
                self._check_issue(issue, github, gh_config)
            except Exception:
                log.warning("CI watcher: error checking %s", issue.identifier, exc_info=True)

    def _check_issue(
        self,
        issue: "Issue",
        github: GitHubClient,
        gh_config: "GitHubConfig",
    ) -> None:
        owner = gh_config.owner
        repo = gh_config.repo

        pr = None
        if issue.branch_name:
            pr = github.find_pr_for_branch(owner, repo, issue.branch_name)
        if pr is None:
            pr = github.find_pr_by_identifier(owner, repo, issue.identifier)
        if pr is None:
            log.debug("CI watcher: no PR found for %s — skipping.", issue.identifier)
            return

        if pr.merged:
            self._transition_issue(
                issue,
                target_state=gh_config.ci_pass_target_state,
                comment=f"PR [#{pr.number}]({pr.html_url}) has been merged. Moving to {gh_config.ci_pass_target_state}.",
            )
            return

        checks = github.get_check_status(owner, repo, pr.head_sha)
        grace_s = max(int(gh_config.ci_poll_interval_ms / 1000) * 2, 60)

        if checks.total == 0:
            created_at = pr.created_at
            if created_at is not None:
                elapsed_s = (datetime.now(timezone.utc) - created_at).total_seconds()
                if elapsed_s < grace_s:
                    log.debug(
                        "CI watcher: %s PR #%d — waiting for checks to appear (%ds/%ds)",
                        issue.identifier, pr.number, int(elapsed_s), grace_s,
                    )
                    return
            github.merge_pull_request(owner, repo, pr.number)
            self._transition_issue(
                issue,
                target_state=gh_config.ci_pass_target_state,
                comment=(
                    f"PR [#{pr.number}]({pr.html_url}) has no CI checks after waiting; merged automatically. "
                    f"Moving to {gh_config.ci_pass_target_state}."
                ),
            )
            return

        if not checks.all_done:
            log.debug(
                "CI watcher: %s PR #%d — CI still running (%d/%d complete)",
                issue.identifier, pr.number, checks.completed, checks.total,
            )
            return

        if checks.all_passed:
            github.merge_pull_request(owner, repo, pr.number)
            self._transition_issue(
                issue,
                target_state=gh_config.ci_pass_target_state,
                comment=(
                    f"All CI checks passed on PR [#{pr.number}]({pr.html_url}). "
                    f"PR merged automatically. Moving to {gh_config.ci_pass_target_state}."
                ),
            )
        elif checks.has_failures:
            failure_summary = github.get_failed_job_logs_summary(owner, repo, pr.head_sha)
            self._transition_issue(
                issue,
                target_state=gh_config.ci_fail_target_state,
                comment=(
                    f"CI failed on PR [#{pr.number}]({pr.html_url}).\n\n"
                    f"**Failed checks:** {', '.join(checks.failed)}\n\n"
                    f"**Failure details:**\n{failure_summary[:2000]}\n\n"
                    f"Moving to {gh_config.ci_fail_target_state} for automated fix."
                ),
            )

    def _transition_issue(
        self,
        issue: "Issue",
        *,
        target_state: str,
        comment: str,
    ) -> None:
        if not issue.team_key:
            log.warning("CI watcher: %s has no team_key — cannot transition.", issue.identifier)
            return

        state_id = self._linear.find_state_id(issue.team_key, target_state)
        if not state_id:
            log.warning(
                "CI watcher: state '%s' not found for team %s — skipping %s.",
                target_state, issue.team_key, issue.identifier,
            )
            return

        try:
            self._linear.update_issue_state(issue.id, state_id)
            log.info("CI watcher: %s → %s", issue.identifier, target_state)
        except Exception:
            log.warning("CI watcher: failed to update state for %s", issue.identifier, exc_info=True)
            return

        try:
            self._linear.create_comment(issue.id, comment)
        except Exception:
            log.warning("CI watcher: failed to add comment to %s", issue.identifier, exc_info=True)

    def close(self) -> None:
        if self._github:
            self._github.close()
            self._github = None

"""Parse node: fetch issue from Linear and prepare workspace."""

from __future__ import annotations

import logging
from typing import Any

from maestro.config import MaestroConfig
from maestro.graph.state import MaestroState
from maestro.linear.client import LinearClient
from maestro.linear.models import issue_to_prompt
from maestro.workspace.manager import WorkspaceManager

log = logging.getLogger(__name__)


def make_parse_node(config: MaestroConfig):
    """Return a graph node function that fetches the issue and sets up workspace."""

    def parse_issue(state: MaestroState) -> dict[str, Any]:
        issue_id = state["issue_id"]
        log.info("parse_issue: fetching %s", issue_id)

        with LinearClient(config.linear) as client:
            issue = client.fetch_issue(issue_id)

        manager = WorkspaceManager(config.workspace.root)
        workspace = manager.prepare_workspace(issue.identifier)
        manager.run_before(workspace)

        issue_dict = {
            "id": issue.id,
            "identifier": issue.identifier,
            "title": issue.title,
            "description": issue.description,
            "state": issue.state,
            "team_key": issue.team_key,
            "state_id": issue.state_id,
            "url": issue.url,
            "labels": issue.labels,
        }

        return {
            "issue": issue_dict,
            "workspace_path": str(workspace.path),
            "prompt": issue_to_prompt(issue),
            "status": "running",
        }

    return parse_issue

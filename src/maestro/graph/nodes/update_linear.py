"""Update Linear node: mark issue done and post a summary comment."""

from __future__ import annotations

import logging
from typing import Any

from maestro.config import MaestroConfig
from maestro.graph.state import MaestroState
from maestro.linear.client import LinearClient

log = logging.getLogger(__name__)


def make_update_linear_node(config: MaestroConfig):
    """Return a graph node function that updates Linear after execution."""

    def update_linear(state: MaestroState) -> dict[str, Any]:
        issue = state.get("issue") or {}
        result = state.get("execute_result") or {}
        issue_id = issue.get("id")

        if not issue_id:
            log.warning("update_linear: no issue id, skipping")
            return {"linear_updated": False, "status": "completed"}

        log.info("update_linear: updating %s", issue.get("identifier", "?"))

        with LinearClient(config.linear) as client:
            team_key = issue.get("team_key")
            if team_key:
                done_id = client.find_state_id(team_key, "Done")
                if done_id and done_id != issue.get("state_id"):
                    client.update_issue_state(issue_id, done_id)

            output = (result.get("output_text") or "").strip()
            if len(output) > 500:
                output = output[:500] + "…"

            body = (
                f"**Maestro automated run completed**\n\n"
                f"- **stop_reason**: `{result.get('stop_reason', 'unknown')}`\n"
                f"- **session_id**: `{result.get('session_id', 'n/a')}`\n\n"
                f"**Agent output**:\n```\n{output}\n```"
            )
            client.create_comment(issue_id, body)

        return {"linear_updated": True, "status": "completed"}

    return update_linear

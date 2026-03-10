"""Execute node: run the prompt through the ACP client."""

from __future__ import annotations

import logging
from typing import Any

from maestro.acp.client import ACPClient
from maestro.config import MaestroConfig
from maestro.graph.state import MaestroState

log = logging.getLogger(__name__)


def make_execute_node(config: MaestroConfig):
    """Return a graph node function that executes the prompt via ACP."""

    def execute_task(state: MaestroState) -> dict[str, Any]:
        workspace_path = state["workspace_path"]
        prompt = state["prompt"]
        issue = state.get("issue") or {}

        log.info(
            "execute_task: running ACP for %s in %s",
            issue.get("identifier", "?"),
            workspace_path,
        )

        chunks: list[str] = []
        acp = ACPClient(config.acp)
        result = acp.run_prompt(
            cwd=workspace_path,
            prompt=prompt,
            on_chunk=chunks.append,
        )

        return {
            "execute_result": {
                "session_id": result.session_id,
                "stop_reason": result.stop_reason,
                "output_text": result.output_text,
            },
        }

    return execute_task

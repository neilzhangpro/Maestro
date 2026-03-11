"""Build the Maestro orchestration pipeline.

A lightweight sequential pipeline that replaces the previous LangGraph
dependency.  Each node is a callable that receives the current state dict
and returns a partial update which is merged back into the state.

Flow: parse_issue → execute_task → update_linear
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from maestro.config import MaestroConfig
from maestro.graph.nodes.execute import make_execute_node
from maestro.graph.nodes.parse import make_parse_node
from maestro.graph.nodes.update_linear import make_update_linear_node
from maestro.graph.state import MaestroState

log = logging.getLogger(__name__)

NodeFn = Callable[[MaestroState], dict[str, Any]]


class Pipeline:
    """A simple sequential pipeline that threads state through a list of nodes."""

    def __init__(self, nodes: list[tuple[str, NodeFn]]) -> None:
        self._nodes = nodes

    def invoke(self, initial_state: MaestroState) -> MaestroState:
        state: dict[str, Any] = dict(initial_state)
        for name, fn in self._nodes:
            log.info("Pipeline node: %s", name)
            try:
                update = fn(state)  # type: ignore[arg-type]
                if update:
                    state.update(update)
            except Exception:
                log.exception("Pipeline node '%s' failed", name)
                state["status"] = "failed"
                return state  # type: ignore[return-value]
        return state  # type: ignore[return-value]


def build_graph(config: MaestroConfig) -> Pipeline:
    """Construct the Maestro pipeline.

    Flow: parse_issue → execute_task → update_linear
    On error at any node the pipeline stops with status=failed.
    """
    return Pipeline([
        ("parse_issue", make_parse_node(config)),
        ("execute_task", make_execute_node(config)),
        ("update_linear", make_update_linear_node(config)),
    ])

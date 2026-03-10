"""Build the Maestro orchestration graph."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from maestro.config import MaestroConfig
from maestro.graph.nodes.execute import make_execute_node
from maestro.graph.nodes.parse import make_parse_node
from maestro.graph.nodes.update_linear import make_update_linear_node
from maestro.graph.state import MaestroState


def build_graph(config: MaestroConfig) -> StateGraph:
    """Construct and compile the Maestro StateGraph.

    Flow: parse_issue → execute_task → update_linear → END
    On error at any node the graph routes to END with status=failed.
    """
    graph = StateGraph(MaestroState)

    graph.add_node("parse_issue", make_parse_node(config))
    graph.add_node("execute_task", make_execute_node(config))
    graph.add_node("update_linear", make_update_linear_node(config))

    graph.set_entry_point("parse_issue")
    graph.add_edge("parse_issue", "execute_task")
    graph.add_edge("execute_task", "update_linear")
    graph.add_edge("update_linear", END)

    return graph.compile()

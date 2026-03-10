"""Global and per-state concurrency control (SPEC §8.3)."""

from __future__ import annotations

from maestro.linear.models import Issue
from maestro.orchestrator.state import OrchestratorState, RunningEntry
from maestro.workflow.config import AgentConfig


class ConcurrencyController:
    """Decide whether a new dispatch is allowed given current load."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def update_config(self, config: AgentConfig) -> None:
        self.config = config

    def available_global_slots(self, running_count: int) -> int:
        return max(self.config.max_concurrent_agents - running_count, 0)

    def can_dispatch(
        self,
        issue: Issue,
        running: dict[str, RunningEntry],
    ) -> bool:
        if self.available_global_slots(len(running)) <= 0:
            return False
        return self._state_slot_available(issue.state, running)

    def _state_slot_available(
        self,
        state: str,
        running: dict[str, RunningEntry],
    ) -> bool:
        key = state.strip().lower()
        limit = self.config.max_concurrent_agents_by_state.get(key)
        if limit is None:
            return True
        current = sum(
            1 for r in running.values()
            if r.issue_state.strip().lower() == key
        )
        return current < limit

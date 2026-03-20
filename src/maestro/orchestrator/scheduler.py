"""Polling orchestrator — dispatch, reconcile, and retry (SPEC §7-8, §16)."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from maestro.agent.events import AgentEvent
from maestro.config import LinearConfig
from maestro.learning.evolution import EvolutionLoop
from maestro.linear.client import LinearClient
from maestro.linear.models import Issue
from maestro.orchestrator.ci_watcher import CIWatcher
from maestro.orchestrator.concurrency import ConcurrencyController
from maestro.orchestrator.reconciliation import Reconciler
from maestro.orchestrator.retry import RetryQueue
from maestro.orchestrator.state import OrchestratorState, RunningEntry
from maestro.worker.worker import Worker
from maestro.workflow.config import ConfigError, ServiceConfig, validate_dispatch_config

log = logging.getLogger(__name__)


class Scheduler:
    """Core orchestration loop implementing the Symphony scheduling spec."""

    def __init__(
        self,
        config: ServiceConfig,
        *,
        on_state_change: Callable[[], None] | None = None,
    ) -> None:
        self.config = config
        self.state = OrchestratorState()
        self._on_state_change = on_state_change

        self._concurrency = ConcurrencyController(config.agent)
        self._retry_queue = RetryQueue(
            self.state,
            on_fire=self._on_retry_fired,
            max_retry_backoff_ms=config.agent.max_retry_backoff_ms,
        )

        self._linear = self._make_linear_client(config)
        self._reconciler = Reconciler(
            self.state, config, self._linear,
            on_terminate=self._terminate_running,
        )
        self._ci_watcher = CIWatcher(config, self._linear)
        self._evolution_loop = EvolutionLoop(config)

        self._tick_timer: threading.Timer | None = None
        self._stop_event = threading.Event()
        self._immediate_poll = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        log.info("Scheduler starting.")
        try:
            validate_dispatch_config(self.config)
        except ConfigError as exc:
            log.error("Startup validation failed: %s", exc)
            raise

        self._startup_terminal_cleanup()
        self._schedule_tick(delay_ms=0)
        if self.config.agent.auto_dispatch:
            log.info(
                "Scheduler running — poll=%dms max_agents=%d auto_dispatch=ON",
                self.config.polling.interval_ms,
                self.config.agent.max_concurrent_agents,
            )
        else:
            log.info(
                "Scheduler running — poll=%dms auto_dispatch=OFF (use TUI to run issues manually)",
                self.config.polling.interval_ms,
            )

    def stop(self) -> None:
        log.info("Scheduler stopping.")
        self._stop_event.set()
        if self._tick_timer:
            self._tick_timer.cancel()
        self._ci_watcher.close()

    def request_immediate_poll(self) -> None:
        """Trigger an out-of-band poll (e.g. from /api/v1/refresh)."""
        self._immediate_poll.set()
        self._schedule_tick(delay_ms=0)

    def reload_config(self, config: ServiceConfig) -> None:
        """Apply hot-reloaded configuration."""
        self.config = config
        self._concurrency.update_config(config.agent)
        self._retry_queue.max_retry_backoff_ms = config.agent.max_retry_backoff_ms
        self._reconciler.config = config

        old_linear = self._linear
        self._linear = self._make_linear_client(config)
        self._reconciler._linear = self._linear
        old_linear.close()

        self._ci_watcher.close()
        self._ci_watcher = CIWatcher(config, self._linear)
        self._evolution_loop.reload_config(config)

        log.info("Scheduler config reloaded.")

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------

    def _schedule_tick(self, delay_ms: int) -> None:
        if self._stop_event.is_set():
            return
        if self._tick_timer:
            self._tick_timer.cancel()
        self._tick_timer = threading.Timer(delay_ms / 1000, self._on_tick)
        self._tick_timer.daemon = True
        self._tick_timer.start()

    def _on_tick(self) -> None:
        if self._stop_event.is_set():
            return

        self._immediate_poll.clear()

        # 1. Reconcile
        try:
            self._reconciler.reconcile()
        except Exception:
            log.warning("Reconciliation error", exc_info=True)

        # 2. Validate config
        try:
            validate_dispatch_config(self.config)
        except ConfigError as exc:
            log.error("Dispatch validation failed: %s — skipping dispatch.", exc)
            self._notify()
            self._schedule_tick(self.config.polling.interval_ms)
            return

        # 3. Fetch candidates
        try:
            candidates = self._fetch_candidates()
        except Exception:
            log.warning("Candidate fetch failed — skipping dispatch.", exc_info=True)
            self._notify()
            self._schedule_tick(self.config.polling.interval_ms)
            return

        # 4. Sort
        candidates = self._sort_candidates(candidates)

        # 5. Dispatch (only when auto_dispatch is enabled)
        dispatched = 0
        if self.config.agent.auto_dispatch:
            for issue in candidates:
                if self._concurrency.available_global_slots(self.state.running_count()) <= 0:
                    break
                if self._should_dispatch(issue):
                    self._dispatch_issue(issue, attempt=None)
                    dispatched += 1

            if dispatched:
                log.info("Dispatched %d issue(s).", dispatched)

        # 6. CI watcher — check issues in watch states
        try:
            self._ci_watcher.poll()
        except Exception:
            log.warning("CI watcher error", exc_info=True)

        # 7. Skill evolution (runs only when no agents are active)
        try:
            self._evolution_loop.maybe_evolve(running_count=self.state.running_count())
        except Exception:
            log.warning("Evolution loop error", exc_info=True)

        # 8. Notify + schedule next
        self._notify()
        self._schedule_tick(self.config.polling.interval_ms)

    # ------------------------------------------------------------------
    # Candidate selection (SPEC §8.2)
    # ------------------------------------------------------------------

    def _fetch_candidates(self) -> list[Issue]:
        issues = self._linear.fetch_issues(
            project_slug=self.config.tracker.project_slug or None,
            state_names=self.config.tracker.active_states,
        )
        return issues

    @staticmethod
    def _sort_candidates(issues: list[Issue]) -> list[Issue]:
        def key(i: Issue):
            prio = i.priority if i.priority is not None else 999
            created = i.created_at or ""
            return (prio, created, i.identifier)
        return sorted(issues, key=key)

    def _should_dispatch(self, issue: Issue) -> bool:
        if not issue.id or not issue.identifier or not issue.title:
            return False
        if self.state.is_claimed(issue.id):
            return False
        if self.state.in_cooldown(issue.id):
            return False
        if not self._concurrency.can_dispatch(issue, self.state.running):
            return False

        active = {s.strip().lower() for s in self.config.tracker.active_states}
        terminal = {s.strip().lower() for s in self.config.tracker.terminal_states}
        handoff = {s.strip().lower() for s in self.config.tracker.handoff_states}
        normalized = issue.state.strip().lower()
        if normalized not in active or normalized in terminal or normalized in handoff:
            return False

        if normalized == "todo" and issue.blocked_by:
            for blocker in issue.blocked_by:
                if blocker.state and blocker.state.strip().lower() not in terminal:
                    return False

        return True

    # ------------------------------------------------------------------
    # Dispatch (SPEC §16.4)
    # ------------------------------------------------------------------

    def cancel_worker(self, issue_id: str) -> bool:
        """Cancel a running worker by issue ID. Returns True if a worker was found."""
        entry = self.state.running.get(issue_id)
        if not entry:
            return False
        if entry.worker_ref and hasattr(entry.worker_ref, "cancel"):
            entry.worker_ref.cancel()
            log.info("Cancel requested for worker %s", entry.identifier)
            return True
        return False

    def _dispatch_issue(self, issue: Issue, attempt: int | None) -> None:
        log.info("Dispatching: %s (attempt=%s)", issue.identifier, attempt)

        entry = RunningEntry(
            issue_id=issue.id,
            identifier=issue.identifier,
            issue_state=issue.state,
            retry_attempt=attempt,
        )

        worker = Worker(
            config=self.config,
            issue=issue,
            attempt=attempt,
            on_event=self._on_worker_event,
            on_exit=self._on_worker_exit,
        )

        thread = threading.Thread(
            target=worker.run,
            name=f"worker-{issue.identifier}",
            daemon=True,
        )
        entry.worker_thread = thread
        entry.worker_ref = worker
        self.state.add_running(entry)
        thread.start()

    # ------------------------------------------------------------------
    # Worker callbacks
    # ------------------------------------------------------------------

    def _on_worker_event(self, issue_id: str, event: AgentEvent) -> None:
        self.state.update_running_event(
            issue_id,
            event=event.event,
            message=event.message,
            session_id=event.session_id or None,
        )
        self._notify()

    def _on_worker_exit(self, issue_id: str, reason: str, error: str | None) -> None:
        entry = self.state.remove_running(issue_id)
        if entry:
            runtime_s = (datetime.now(timezone.utc) - entry.started_at).total_seconds()
            self.state.add_runtime_seconds(runtime_s)
            self.state.record_exit(entry, reason=reason, error=error)
            identifier = entry.identifier
            current_attempt = entry.retry_attempt
        else:
            identifier = issue_id
            current_attempt = None

        if reason == "normal":
            if entry:
                self._reconciler.ensure_handoff_for_issue(issue_id, entry.identifier)
            log.info("Worker %s exited normally — cooldown applied.", identifier)
            self.state.mark_completed(issue_id)
        else:
            next_attempt = (current_attempt or 0) + 1
            log.warning("Worker %s exited abnormally: %s", identifier, error)
            self._retry_queue.schedule(
                issue_id, identifier, attempt=next_attempt,
                error=error,
            )

        self._notify()

    # ------------------------------------------------------------------
    # Retry handling (SPEC §16.6)
    # ------------------------------------------------------------------

    def _on_retry_fired(self, issue_id: str) -> None:
        retry_entry = self.state.pop_retry(issue_id)
        if not retry_entry:
            return

        try:
            candidates = self._fetch_candidates()
        except Exception:
            log.warning("Retry poll failed for %s — rescheduling.", issue_id)
            self._retry_queue.schedule(
                issue_id, retry_entry.identifier,
                attempt=retry_entry.attempt + 1,
                error="retry_poll_failed",
            )
            return

        issue = next((c for c in candidates if c.id == issue_id), None)
        if issue is None:
            log.info("Retry: %s no longer a candidate — releasing.", retry_entry.identifier)
            self.state.release_claim(issue_id)
            return

        if self._concurrency.available_global_slots(self.state.running_count()) <= 0:
            self._retry_queue.schedule(
                issue_id, issue.identifier,
                attempt=retry_entry.attempt + 1,
                error="no available orchestrator slots",
            )
            return

        self._dispatch_issue(issue, attempt=retry_entry.attempt)

    # ------------------------------------------------------------------
    # Terminate a running issue (from reconciliation or stall)
    # ------------------------------------------------------------------

    def _terminate_running(self, issue_id: str, reason: str, *, cleanup: bool) -> None:
        entry = self.state.remove_running(issue_id)
        if not entry:
            return

        if entry.worker_thread and entry.worker_thread.is_alive():
            log.info("Terminating worker thread for %s (reason=%s)", entry.identifier, reason)

        self.state.release_claim(issue_id)

        if cleanup:
            self._reconciler.cleanup_workspace(entry.identifier)

    # ------------------------------------------------------------------
    # Startup cleanup (SPEC §8.6)
    # ------------------------------------------------------------------

    def _startup_terminal_cleanup(self) -> None:
        try:
            terminal_issues = self._linear.fetch_issues_by_states(
                self.config.tracker.terminal_states,
            )
        except Exception:
            log.warning("Startup terminal fetch failed — continuing.", exc_info=True)
            return

        for issue in terminal_issues:
            self._reconciler.cleanup_workspace(issue.identifier)

        if terminal_issues:
            log.info("Cleaned %d terminal workspace(s) at startup.", len(terminal_issues))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _notify(self) -> None:
        if self._on_state_change:
            try:
                self._on_state_change()
            except Exception:
                pass

    @staticmethod
    def _make_linear_client(config: ServiceConfig) -> LinearClient:
        return LinearClient(LinearConfig(
            api_key=config.tracker.api_key,
            api_url=config.tracker.endpoint,
            project_slug=config.tracker.project_slug or None,
            team_id=config.tracker.team_id,
            assignee=config.tracker.assignee,
            active_states=config.tracker.active_states,
            terminal_states=config.tracker.terminal_states,
            timeout_s=config.tracker.timeout_s,
        ))

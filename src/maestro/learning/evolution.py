"""EvolutionLoop — orchestrates the Skill self-evolution cycle.

Called from :class:`Scheduler._on_tick` whenever no agent is running and
the minimum time / run-count thresholds have been exceeded.

Cycle steps
-----------
A. **Skill Critique** — :class:`SkillAnalyser` identifies existing Skills with
   unaddressed failure/success patterns; :class:`SkillMutator` performs a
   Cross-Rollout Critique and produces up to three outputs:

   * ``addendum.md``             → appended to the Skill's learned section
   * ``new_experiences.json``    → new entries added to the ExperienceBank
   * ``consolidation_ops.json``  → merge/delete ops applied to the ExperienceBank

B. **Flow distillation** — :class:`FlowDistiller` discovers frequent tool-call
   sub-sequences; :class:`SkillMutator` generates new SKILL.md files for them.

C. **Logging** — every mutation is recorded to ``evolution_log.jsonl``.

All operations are guarded by a threading lock so that the loop is safe even
if tick intervals overlap (they shouldn't, but defensive programming applies).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from maestro.learning.experience_bank import ExperienceBank
from maestro.learning.flow_distiller import FlowDistiller
from maestro.learning.flow_recorder import FlowRecorder
from maestro.learning.recorder import RunRecorder
from maestro.learning.skill_analyser import SkillAnalyser
from maestro.learning.skill_mutator import SkillMutator
from maestro.learning.skill_store import SkillStore

if TYPE_CHECKING:
    from maestro.workflow.config import EvolutionConfig, ServiceConfig

log = logging.getLogger(__name__)

_LOG_FILE = "evolution_log.jsonl"


class EvolutionLoop:
    """Run periodic Skill evolution cycles driven by execution history.

    Parameters
    ----------
    service_config:
        The active Maestro service configuration.
    """

    def __init__(self, service_config: "ServiceConfig") -> None:
        self._svc = service_config
        self._cfg = service_config.evolution
        self._lock = threading.Lock()

        store_dir = service_config.workspace.root / ".maestro"
        self._store_dir = store_dir
        self._log_path = store_dir / _LOG_FILE

        self._recorder = RunRecorder(store_dir)
        self._flow_recorder = FlowRecorder(store_dir)

        evolved_dir = store_dir / "evolved_skills"
        pending_dir = store_dir / "pending_skills"
        self._skill_store = SkillStore(evolved_dir, pending_dir=pending_dir)

        self._exp_bank = ExperienceBank(store_dir)

        # State tracking
        self._last_cycle_time: float = 0.0
        self._last_cycle_run_count: int = self._count_successful_runs()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def maybe_evolve(self, running_count: int = 0) -> None:
        """Run an evolution cycle if conditions are met.

        Parameters
        ----------
        running_count:
            Number of agent workers currently running.  Evolution is skipped
            when any workers are active (avoids Skill changes mid-run).
        """
        if not self._cfg.enabled:
            return
        if running_count > 0:
            return
        if not self._lock.acquire(blocking=False):
            log.debug("EvolutionLoop: cycle already running — skipping.")
            return
        try:
            self._maybe_evolve_locked()
        finally:
            self._lock.release()

    def reload_config(self, service_config: "ServiceConfig") -> None:
        """Apply hot-reloaded configuration."""
        self._svc = service_config
        self._cfg = service_config.evolution

    # ------------------------------------------------------------------
    # Internal cycle logic
    # ------------------------------------------------------------------

    def _maybe_evolve_locked(self) -> None:
        if not self._should_trigger():
            return

        log.info("EvolutionLoop: starting evolution cycle.")
        cycle_start = time.monotonic()
        mutations: list[dict] = []

        runner = _create_runner(self._svc)
        evo_ws = self._store_dir / "evolution_workspace"
        mutator = SkillMutator(
            runner=runner,
            evolution_workspace=evo_ws,
            experience_bank=self._exp_bank,
        )

        # --- A. Skill Critique (Cross-Rollout) ---
        analyser = SkillAnalyser(
            recorder=self._recorder,
            skill_store=self._skill_store,
            min_occurrences=self._cfg.min_pattern_occurrences,
        )
        patch_candidates = analyser.find_candidates()
        patch_limit = 3

        for patch in patch_candidates[:patch_limit]:
            log.info(
                "EvolutionLoop: running critique for skill %r.", patch.skill_name
            )
            try:
                critique = mutator.generate_critique(patch)
                mutation_record: dict = {"type": "critique", "skill": patch.skill_name}

                # Apply addendum
                if critique.addendum:
                    self._skill_store.append_learned(patch.skill_name, critique.addendum)
                    mutation_record["addendum_chars"] = len(critique.addendum)
                    log.info(
                        "EvolutionLoop: addendum applied to %r (%d chars).",
                        patch.skill_name, len(critique.addendum),
                    )

                # Apply new Experiences
                if critique.new_experience_ops:
                    # Normalise field name: agent may use "action_text" to avoid
                    # clashing with the "action" operation type field
                    normalised_new = _normalise_exp_ops(critique.new_experience_ops)
                    applied = self._exp_bank.apply_operations(normalised_new)
                    mutation_record["new_experiences"] = applied
                    log.info(
                        "EvolutionLoop: %d new experience(s) added for %r.",
                        applied, patch.skill_name,
                    )

                # Apply consolidation ops
                if critique.consolidation_ops:
                    normalised_consol = _normalise_exp_ops(critique.consolidation_ops)
                    applied = self._exp_bank.apply_operations(normalised_consol)
                    mutation_record["consolidation_ops"] = applied
                    log.info(
                        "EvolutionLoop: %d consolidation op(s) applied for %r.",
                        applied, patch.skill_name,
                    )

                mutations.append(mutation_record)

            except Exception:
                log.warning(
                    "EvolutionLoop: critique failed for %r", patch.skill_name,
                    exc_info=True,
                )

        # --- B. Flow distillation ---
        distiller = FlowDistiller(
            flow_recorder=self._flow_recorder,
            skill_store=self._skill_store,
            min_occurrences=self._cfg.min_pattern_occurrences,
        )
        patterns = distiller.discover_patterns()
        new_skill_count = 0

        for pattern in patterns:
            if new_skill_count >= self._cfg.max_new_skills_per_cycle:
                break
            existing_names = [m.name for m in self._skill_store.list_skills()]
            log.info(
                "EvolutionLoop: generating new Skill for pattern %r.",
                pattern.suggested_name,
            )
            try:
                result = mutator.generate_new_skill(pattern, existing_names)
                if result:
                    skill_name, content = result
                    pending = not self._cfg.auto_apply
                    path = self._skill_store.create_skill(
                        skill_name, content, pending=pending
                    )
                    new_skill_count += 1
                    mutations.append({
                        "type": "new_skill",
                        "skill": skill_name,
                        "pending": pending,
                        "path": str(path),
                    })
                    log.info(
                        "EvolutionLoop: created new Skill %r (pending=%s).",
                        skill_name, pending,
                    )
            except Exception:
                log.warning(
                    "EvolutionLoop: new-Skill generation failed for %r",
                    pattern.suggested_name,
                    exc_info=True,
                )

        # --- C. Logging ---
        elapsed_ms = int((time.monotonic() - cycle_start) * 1000)
        self._log_cycle(mutations, elapsed_ms)

        self._last_cycle_time = time.monotonic()
        self._last_cycle_run_count = self._count_successful_runs()

        log.info(
            "EvolutionLoop: cycle complete — %d mutation(s) in %dms.",
            len(mutations), elapsed_ms,
        )

    def _should_trigger(self) -> bool:
        now = time.monotonic()
        elapsed_min = (now - self._last_cycle_time) / 60
        if elapsed_min < self._cfg.min_interval_minutes:
            log.debug(
                "EvolutionLoop: interval not met (%.1f / %d min).",
                elapsed_min, self._cfg.min_interval_minutes,
            )
            return False

        current_runs = self._count_successful_runs()
        new_runs = current_runs - self._last_cycle_run_count
        if new_runs < self._cfg.min_runs_between:
            log.debug(
                "EvolutionLoop: not enough new runs (%d / %d).",
                new_runs, self._cfg.min_runs_between,
            )
            return False

        return True

    def _count_successful_runs(self) -> int:
        """Count total successful RunRecords in history (cheap linear scan)."""
        records = self._recorder.load_recent(limit=10_000)
        return sum(1 for r in records if r.success)

    def _log_cycle(self, mutations: list[dict], elapsed_ms: int) -> None:
        entry = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": elapsed_ms,
            "mutations": mutations,
        }
        self._store_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        try:
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            log.warning(
                "EvolutionLoop: could not write evolution log.", exc_info=True
            )


# ---------------------------------------------------------------------------
# Runner factory (mirrors Worker logic)
# ---------------------------------------------------------------------------

def _create_runner(config: "ServiceConfig"):
    """Instantiate the correct runner for the configured backend."""
    if config.backend == "claude_code" and config.claude_code:
        from maestro.agent.claude_code import ClaudeCodeRunner
        return ClaudeCodeRunner(config.claude_code)
    from maestro.agent.headless import HeadlessRunner
    return HeadlessRunner(config.cursor)


# ---------------------------------------------------------------------------
# Normalisation helper
# ---------------------------------------------------------------------------

def _normalise_exp_ops(ops: list[dict]) -> list[dict]:
    """Rename ``action_text`` → ``action`` in experience operation dicts.

    The meta-prompt uses ``action_text`` for the Experience ``action`` field to
    avoid ambiguity with the operation type field (also named ``action``).  The
    ExperienceBank expects ``action`` for both — so we fix the field name here.
    """
    normalised = []
    for op in ops:
        op = dict(op)
        if "action_text" in op:
            op["action"] = op.pop("action_text")
        normalised.append(op)
    return normalised

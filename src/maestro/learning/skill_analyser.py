"""SkillAnalyser — pattern extraction and Skill-patch candidate discovery.

Reads the shared ``run_history.jsonl`` (via :class:`RunRecorder`) and
produces a list of :class:`SkillPatch` candidates describing which existing
Skills should receive an experience addendum and what that addendum should
contain.

Algorithm overview
------------------
1. Load the last N RunRecords.
2. Group records by each Skill name found in ``skill_refs``.
3. For each Skill:
   a. Count failure patterns (``error`` field value) that co-occur with this
      Skill and appear at least ``min_occurrences`` times.
   b. Find tool sub-sequences common to successful runs that reference this
      Skill (shared prefix/suffix of length >= ``min_seq_len``).
   c. Skip the Skill if its ``learned_section`` already mentions the same
      error strings (rough deduplication).
4. Return :class:`SkillPatch` objects for Skills with new patterns.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from maestro.learning.recorder import RunRecord, RunRecorder
from maestro.learning.skill_store import SkillStore

log = logging.getLogger(__name__)


@dataclass
class FailurePattern:
    """A recurring failure associated with a Skill."""

    error: str
    count: int
    sample_summaries: list[str] = field(default_factory=list)
    """Up to 3 output_summary excerpts from failed runs."""


@dataclass
class SuccessPattern:
    """Common tool sub-sequence found in successful runs referencing a Skill."""

    tool_sequence: list[str]
    """Ordered list of tool names."""

    count: int
    """Number of runs containing this sub-sequence."""


@dataclass
class SkillPatch:
    """A Skill that should receive an evolution addendum."""

    skill_name: str

    failure_patterns: list[FailurePattern] = field(default_factory=list)
    success_patterns: list[SuccessPattern] = field(default_factory=list)

    # Raw records for richer LLM context
    recent_records: list[RunRecord] = field(default_factory=list)
    """Last few records that reference this Skill (both success and failure)."""

    current_learned_section: str = ""
    """Existing learned content so the mutator can avoid duplication."""


class SkillAnalyser:
    """Derive Skill-patch candidates from execution history."""

    def __init__(
        self,
        recorder: RunRecorder,
        skill_store: SkillStore,
        *,
        min_occurrences: int = 3,
        min_seq_len: int = 3,
        history_limit: int = 100,
        max_sample_summaries: int = 3,
    ) -> None:
        self._recorder = recorder
        self._skill_store = skill_store
        self._min_occ = min_occurrences
        self._min_seq = min_seq_len
        self._limit = history_limit
        self._max_samples = max_sample_summaries

    def find_candidates(self) -> list[SkillPatch]:
        """Return Skills that have new, unaddressed patterns in history."""
        records = self._recorder.load_recent(self._limit)
        if not records:
            log.debug("SkillAnalyser: no history records found.")
            return []

        # Group records by Skill name
        by_skill: dict[str, list[RunRecord]] = defaultdict(list)
        for rec in records:
            for skill in rec.skill_refs:
                by_skill[skill].append(rec)

        if not by_skill:
            log.debug("SkillAnalyser: no skill_refs found in recent records.")
            return []

        existing_skills = {m.name for m in self._skill_store.list_skills()}
        candidates: list[SkillPatch] = []

        for skill_name, skill_records in by_skill.items():
            if skill_name not in existing_skills:
                log.debug("SkillAnalyser: unknown skill %r — skipping.", skill_name)
                continue

            # Load current learned section for deduplication check
            try:
                content = self._skill_store.read_skill(skill_name)
                if content.opt_out:
                    log.debug("SkillAnalyser: skill %r has NO_AUTO_MUTATE — skipping.", skill_name)
                    continue
                learned = content.learned_section
            except Exception:
                learned = ""

            failures = [r for r in skill_records if not r.success]
            successes = [r for r in skill_records if r.success]

            failure_patterns = self._extract_failure_patterns(failures, learned)
            success_patterns = self._extract_success_patterns(successes)

            if not failure_patterns and not success_patterns:
                continue

            candidates.append(SkillPatch(
                skill_name=skill_name,
                failure_patterns=failure_patterns,
                success_patterns=success_patterns,
                recent_records=skill_records[-10:],
                current_learned_section=learned,
            ))

        log.info("SkillAnalyser: found %d patch candidate(s).", len(candidates))
        return candidates

    # ------------------------------------------------------------------
    # Pattern extraction helpers
    # ------------------------------------------------------------------

    def _extract_failure_patterns(
        self,
        failures: list[RunRecord],
        learned: str,
    ) -> list[FailurePattern]:
        if not failures:
            return []

        error_counts: Counter[str] = Counter()
        error_samples: dict[str, list[str]] = defaultdict(list)

        for rec in failures:
            err = rec.error or "unknown_error"
            error_counts[err] += 1
            if len(error_samples[err]) < self._max_samples and rec.output_summary:
                error_samples[err].append(rec.output_summary[:200])

        patterns: list[FailurePattern] = []
        for err, count in error_counts.most_common():
            if count < self._min_occ:
                continue
            # Skip if already mentioned in the learned section
            if err in learned:
                continue
            patterns.append(FailurePattern(
                error=err,
                count=count,
                sample_summaries=error_samples[err],
            ))

        return patterns

    def _extract_success_patterns(
        self,
        successes: list[RunRecord],
    ) -> list[SuccessPattern]:
        if len(successes) < self._min_occ:
            return []

        # Build tool-name-only sequences from tool_sequence field
        sequences: list[list[str]] = []
        for rec in successes:
            seq = [step["tool"] for step in rec.tool_sequence if "tool" in step]
            if len(seq) >= self._min_seq:
                sequences.append(seq)

        if not sequences:
            return []

        # Count N-grams of length min_seq across all sequences
        ngram_counts: Counter[tuple[str, ...]] = Counter()
        for seq in sequences:
            seen: set[tuple[str, ...]] = set()
            for start in range(len(seq) - self._min_seq + 1):
                ngram = tuple(seq[start: start + self._min_seq])
                if ngram not in seen:
                    ngram_counts[ngram] += 1
                    seen.add(ngram)

        patterns: list[SuccessPattern] = []
        for ngram, count in ngram_counts.most_common(5):
            if count >= self._min_occ:
                patterns.append(SuccessPattern(
                    tool_sequence=list(ngram),
                    count=count,
                ))

        return patterns

"""FlowDistiller — workflow clustering and new-Skill candidate extraction.

Reads :class:`FlowRecord` objects from ``flow_history.jsonl`` and identifies
repeated tool-call sub-sequences that could be codified as reusable Skills.

Algorithm
---------
1. Load all *successful* FlowRecords.
2. Extract a tool-name sequence (ignoring path details) for each run.
3. Count N-grams of configurable length across all sequences.
4. Filter to N-grams that appear in at least ``min_occurrences`` distinct runs.
5. Deduplicate against existing Skill names (rough string-match heuristic).
6. For each surviving pattern, build a :class:`FlowPattern` that contains
   the representative tool chain and concrete sample runs for LLM context.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from maestro.learning.flow_recorder import FlowRecord, FlowRecorder, FlowStep
from maestro.learning.skill_store import SkillStore

log = logging.getLogger(__name__)

_SAFE_NAME_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class FlowPattern:
    """A recurring tool sub-sequence that is a candidate for a new Skill."""

    tool_sequence: list[str]
    """Ordered tool names (the repeating pattern)."""

    occurrences: int
    """Number of distinct successful runs containing this pattern."""

    sample_runs: list[FlowRecord] = field(default_factory=list)
    """Up to 5 concrete FlowRecord examples for LLM context."""

    suggested_name: str = ""
    """Auto-generated slug for the new Skill directory name."""

    description_hint: str = ""
    """Human-readable one-liner describing the pattern (for the LLM prompt)."""


class FlowDistiller:
    """Discover reusable workflow patterns from full FlowRecord history."""

    def __init__(
        self,
        flow_recorder: FlowRecorder,
        skill_store: SkillStore,
        *,
        min_occurrences: int = 3,
        min_pattern_len: int = 4,
        max_pattern_len: int = 10,
        history_limit: int = 200,
        max_samples_per_pattern: int = 5,
    ) -> None:
        self._flow_recorder = flow_recorder
        self._skill_store = skill_store
        self._min_occ = min_occurrences
        self._min_len = min_pattern_len
        self._max_len = max_pattern_len
        self._limit = history_limit
        self._max_samples = max_samples_per_pattern

    def discover_patterns(self) -> list[FlowPattern]:
        """Return new-Skill candidates not already covered by existing Skills."""
        records = self._flow_recorder.load_successful(self._limit)
        if len(records) < self._min_occ:
            log.debug("FlowDistiller: not enough successful records (%d).", len(records))
            return []

        # Build per-record tool-name sequences
        seqs: list[list[str]] = []
        for rec in records:
            tools = [s.tool_name for s in rec.steps if s.tool_name]
            if len(tools) >= self._min_len:
                seqs.append(tools)

        if not seqs:
            return []

        # Enumerate N-grams at all configured lengths
        ngram_counts: Counter[tuple[str, ...]] = Counter()
        ngram_runs: dict[tuple[str, ...], list[FlowRecord]] = defaultdict(list)

        for i, (seq, rec) in enumerate(zip(seqs, records)):
            seen_in_run: set[tuple[str, ...]] = set()
            for length in range(self._min_len, min(self._max_len + 1, len(seq) + 1)):
                for start in range(len(seq) - length + 1):
                    ngram = tuple(seq[start: start + length])
                    if ngram not in seen_in_run:
                        ngram_counts[ngram] += 1
                        ngram_runs[ngram].append(rec)
                        seen_in_run.add(ngram)

        # Filter by occurrence threshold
        frequent = [
            (ng, cnt)
            for ng, cnt in ngram_counts.most_common()
            if cnt >= self._min_occ
        ]

        if not frequent:
            log.debug("FlowDistiller: no frequent patterns found.")
            return []

        # Prune redundant sub-patterns: skip a pattern if a longer pattern
        # that contains it already has the same or higher count
        pruned = _prune_subpatterns(frequent)

        # Exclude patterns already covered by existing Skills
        existing_skills = {m.name for m in self._skill_store.list_skills()}
        patterns: list[FlowPattern] = []

        for ngram, count in pruned:
            suggested = _suggest_name(ngram)
            # Skip if a Skill with a similar name already exists
            if _name_already_covered(suggested, existing_skills):
                log.debug("FlowDistiller: pattern %r covered by existing skill.", suggested)
                continue

            sample_runs = ngram_runs[ngram][: self._max_samples]
            patterns.append(FlowPattern(
                tool_sequence=list(ngram),
                occurrences=count,
                sample_runs=sample_runs,
                suggested_name=suggested,
                description_hint=_describe_pattern(ngram),
            ))

        log.info("FlowDistiller: found %d new-Skill candidate(s).", len(patterns))
        return patterns

    def extract_candidate_context(self, pattern: FlowPattern) -> dict:
        """Return a serialisable dict with all context needed by SkillMutator."""
        return {
            "tool_sequence": pattern.tool_sequence,
            "occurrences": pattern.occurrences,
            "suggested_name": pattern.suggested_name,
            "description_hint": pattern.description_hint,
            "sample_runs": [
                {
                    "issue_identifier": r.issue_identifier,
                    "labels": r.labels,
                    "total_turns": r.total_turns,
                    "steps": [
                        {
                            "tool": s.tool_name,
                            "path": s.tool_path,
                        }
                        for s in r.steps
                        if s.tool_name in pattern.tool_sequence
                    ],
                }
                for r in pattern.sample_runs
            ],
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _prune_subpatterns(
    frequent: list[tuple[tuple[str, ...], int]],
) -> list[tuple[tuple[str, ...], int]]:
    """Remove N-grams that are sub-sequences of a longer, equally-frequent N-gram."""
    result: list[tuple[tuple[str, ...], int]] = []
    ngram_set = {ng for ng, _ in frequent}
    counts = dict(frequent)

    for ngram, count in frequent:
        dominated = False
        # Check if any longer ngram containing this one has count >= count
        for other in ngram_set:
            if len(other) <= len(ngram):
                continue
            if count <= counts[other] and _is_subsequence(ngram, other):
                dominated = True
                break
        if not dominated:
            result.append((ngram, count))

    return result


def _is_subsequence(short: tuple, long: tuple) -> bool:
    """Return True if *short* appears as a contiguous sub-sequence of *long*."""
    ls, ll = len(short), len(long)
    for i in range(ll - ls + 1):
        if long[i: i + ls] == short:
            return True
    return False


def _suggest_name(ngram: tuple[str, ...]) -> str:
    """Derive a slug Skill name from an N-gram of tool names."""
    parts: list[str] = []
    for tool in ngram[:4]:
        # Normalise: strip common suffixes like ToolCall, Runner
        clean = re.sub(r"(ToolCall|Runner|Call)$", "", tool, flags=re.IGNORECASE)
        clean = _SAFE_NAME_RE.sub("-", clean.lower()).strip("-")
        if clean and clean not in parts:
            parts.append(clean)
    return "learned-" + "-".join(parts) if parts else "learned-workflow"


def _name_already_covered(name: str, existing: set[str]) -> bool:
    """Heuristic: return True if *name* is similar to an existing Skill name."""
    # Strip the 'learned-' prefix for comparison
    core = name.removeprefix("learned-")
    for skill in existing:
        skill_core = skill.removeprefix("learned-")
        if core in skill_core or skill_core in core:
            return True
    return False


def _describe_pattern(ngram: tuple[str, ...]) -> str:
    """Build a one-line human-readable description of the tool pattern."""
    tools = ", ".join(ngram)
    return f"Repeated tool sequence: {tools}"

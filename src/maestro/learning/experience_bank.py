"""ExperienceBank — action-level tactical insights, separate from Skill workflows.

XSkill (arxiv.org/abs/2603.12056) demonstrates that two complementary knowledge
streams substantially improve agent performance:

- **Skills** (SKILL.md) — task-level, stable, structured workflows
- **Experiences** — action-level, context-specific ``condition → action`` pairs

This module implements the second stream.  Each *experience* captures:

- ``condition`` — the triggering situation ("when pytest fails with ImportError")
- ``action``    — the recommended response ("check pyproject.toml packages first")
- ``source_issues`` — which issue runs contributed this insight (for auditability)

Storage
-------
``{workspace_root}/.maestro/experience_bank.jsonl`` — one JSON object per line.
The file is rewritten in full on every mutation (the bank is small enough for
this to be fast).

Deduplication / Consolidation
-------------------------------
Before adding a new experience the bank checks the condition text against all
existing conditions using ``difflib.SequenceMatcher``.  If the similarity ratio
exceeds ``SIMILARITY_THRESHOLD`` (default 0.75) the entries are *merged* rather
than appended, preventing the bank from accumulating redundant observations.

Retrieval
---------
Simple keyword-overlap scoring — no embedding model or vector store required.
For Maestro's typical scale (dozens to low hundreds of experiences) this is
fast enough and avoids any new heavy dependencies.  The interface is designed
so that the scoring strategy can be swapped out without changing callers.
"""

from __future__ import annotations

import difflib
import fcntl
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_BANK_FILE = "experience_bank.jsonl"
SIMILARITY_THRESHOLD = 0.75


@dataclass
class Experience:
    """One action-level tactical insight."""

    id: str
    """Short unique identifier (8-char UUID prefix)."""

    condition: str
    """Triggering situation described in plain English."""

    action: str
    """Recommended action / decision the agent should take."""

    source_issues: list[str] = field(default_factory=list)
    """Issue identifiers (e.g. NOV-25) that generated this experience."""

    created_at: str = ""
    """ISO-8601 UTC timestamp of first creation."""

    updated_at: str = ""
    """ISO-8601 UTC timestamp of last modification."""

    use_count: int = 0
    """Number of times retrieved for prompt injection (tracking utility)."""


class ExperienceBank:
    """JSONL-backed store for :class:`Experience` objects.

    Experiences are lazy-loaded on first access and kept in an in-memory cache.
    All mutating operations flush the cache back to disk immediately.

    Parameters
    ----------
    store_dir:
        Directory that contains (or will contain) ``experience_bank.jsonl``.
        Typically ``{workspace_root}/.maestro/``.
    """

    def __init__(self, store_dir: Path) -> None:
        self._store_dir = store_dir
        self._path = store_dir / _BANK_FILE
        self._cache: list[Experience] | None = None

    # ------------------------------------------------------------------
    # Read access
    # ------------------------------------------------------------------

    def load_all(self) -> list[Experience]:
        """Return all experiences (lazily loaded, then cached)."""
        if self._cache is None:
            self._cache = self._read_from_disk()
        return self._cache

    def search(self, query: str, top_k: int = 10) -> list[Experience]:
        """Return up to *top_k* experiences most relevant to *query*.

        Scoring uses keyword overlap between *query* and each experience's
        combined ``condition + action`` text.
        """
        all_exp = self.load_all()
        if not all_exp:
            return []

        query_words = set(query.lower().split())
        scored: list[tuple[int, Experience]] = []
        for exp in all_exp:
            combined_words = set((exp.condition + " " + exp.action).lower().split())
            overlap = len(query_words & combined_words)
            if overlap > 0:
                scored.append((overlap, exp))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    def to_context_list(
        self,
        experiences: list[Experience] | None = None,
    ) -> list[dict]:
        """Serialise *experiences* (or the full bank) to a JSON-safe list.

        The returned format is designed to be directly written into the
        evolution workspace so the agent can read, modify, and return it.
        """
        exps = experiences if experiences is not None else self.load_all()
        return [
            {
                "id": e.id,
                "condition": e.condition,
                "action": e.action,
                "source_issues": e.source_issues,
                "use_count": e.use_count,
            }
            for e in exps
        ]

    # ------------------------------------------------------------------
    # Write access (called by EvolutionLoop after parsing agent outputs)
    # ------------------------------------------------------------------

    def apply_operations(self, ops: list[dict]) -> int:
        """Apply a list of add / modify / delete operations from the agent.

        The agent writes these operations to ``output/consolidation_ops.json``
        in the format::

            [
              {"action": "add",    "condition": "...", "action": "...", "source_issues": [...]},
              {"action": "modify", "id": "abc12345", "condition": "...", "action": "..."},
              {"action": "delete", "id": "abc12345"}
            ]

        Returns the number of operations successfully applied.
        """
        all_exp = list(self.load_all())
        applied = 0

        for op in ops:
            op_type = op.get("action", "").lower()
            try:
                if op_type == "add":
                    exp = _build_experience(op)
                    if exp:
                        all_exp = self._add_or_merge(all_exp, exp)
                        applied += 1
                elif op_type == "modify":
                    exp_id = op.get("id", "")
                    for i, e in enumerate(all_exp):
                        if e.id == exp_id:
                            all_exp[i] = _apply_modify(e, op)
                            applied += 1
                            break
                    else:
                        log.debug("ExperienceBank: modify target not found: %s", exp_id)
                elif op_type == "delete":
                    exp_id = op.get("id", "")
                    before = len(all_exp)
                    all_exp = [e for e in all_exp if e.id != exp_id]
                    if len(all_exp) < before:
                        applied += 1
                else:
                    log.debug("ExperienceBank: unknown op action %r", op_type)
            except Exception:
                log.warning("ExperienceBank: failed to apply op %r", op, exc_info=True)

        self._cache = all_exp
        self._flush()
        log.info("ExperienceBank: applied %d/%d operation(s).", applied, len(ops))
        return applied

    def add(
        self,
        condition: str,
        action: str,
        source_issues: list[str] | None = None,
    ) -> str:
        """Add a single experience and return its ID.

        If a sufficiently similar condition already exists the two are merged
        and the existing ID is returned.
        """
        exp = Experience(
            id=_short_id(),
            condition=condition.strip(),
            action=action.strip(),
            source_issues=source_issues or [],
            created_at=_now(),
            updated_at=_now(),
        )
        all_exp = list(self.load_all())
        original_len = len(all_exp)
        all_exp = self._add_or_merge(all_exp, exp)
        self._cache = all_exp
        self._flush()
        # If the list grew, the new entry is at the end; otherwise it was merged
        if len(all_exp) > original_len:
            return all_exp[-1].id
        return next(
            (e.id for e in all_exp if e.condition == condition.strip()),
            exp.id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_or_merge(
        self,
        all_exp: list[Experience],
        new: Experience,
    ) -> list[Experience]:
        for i, existing in enumerate(all_exp):
            sim = difflib.SequenceMatcher(
                None,
                existing.condition.lower(),
                new.condition.lower(),
            ).ratio()
            if sim >= SIMILARITY_THRESHOLD:
                log.debug(
                    "ExperienceBank: merging with existing id=%s (sim=%.2f)",
                    existing.id, sim,
                )
                merged = Experience(
                    id=existing.id,
                    condition=existing.condition,
                    action=_merge_actions(existing.action, new.action),
                    source_issues=sorted(
                        set(existing.source_issues + new.source_issues)
                    ),
                    created_at=existing.created_at,
                    updated_at=_now(),
                    use_count=existing.use_count,
                )
                all_exp[i] = merged
                return all_exp
        all_exp.append(new)
        return all_exp

    def _read_from_disk(self) -> list[Experience]:
        if not self._path.exists():
            return []
        results: list[Experience] = []
        try:
            with open(self._path, encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                        # Back-fill any missing fields added in later versions
                        data.setdefault("source_issues", [])
                        data.setdefault("created_at", "")
                        data.setdefault("updated_at", "")
                        data.setdefault("use_count", 0)
                        results.append(Experience(**data))
                    except (json.JSONDecodeError, TypeError, KeyError):
                        log.debug(
                            "ExperienceBank: skipping malformed line: %s", raw[:80]
                        )
        except OSError:
            log.warning(
                "ExperienceBank: could not read %s", self._path, exc_info=True
            )
        return results

    def _flush(self) -> None:
        """Rewrite the JSONL file from the in-memory cache (file-lock protected)."""
        self._store_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(asdict(e), ensure_ascii=False) + "\n"
            for e in (self._cache or [])
        ]
        with open(self._path, "w", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.writelines(lines)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        log.debug("ExperienceBank: flushed %d experience(s).", len(self._cache or []))


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _short_id() -> str:
    return str(uuid.uuid4())[:8]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_experience(op: dict) -> Experience | None:
    condition = op.get("condition", "").strip()
    action_text = op.get("action", "").strip()
    if not condition or not action_text:
        return None
    return Experience(
        id=op.get("id") or _short_id(),
        condition=condition,
        action=action_text,
        source_issues=op.get("source_issues", []),
        created_at=_now(),
        updated_at=_now(),
    )


def _apply_modify(existing: Experience, op: dict) -> Experience:
    return Experience(
        id=existing.id,
        condition=op.get("condition", existing.condition),
        action=op.get("action", existing.action),
        source_issues=op.get("source_issues", existing.source_issues),
        created_at=existing.created_at,
        updated_at=_now(),
        use_count=existing.use_count,
    )


def _merge_actions(old: str, new: str) -> str:
    """Combine two action strings, suppressing duplicate sentences."""
    old_sents = {s.strip().rstrip(".") for s in old.split(".") if s.strip()}
    result = old.rstrip()
    for sent in new.split("."):
        sent = sent.strip().rstrip(".")
        if sent and sent not in old_sents:
            result = result.rstrip(".") + ". " + sent + "."
    return result.strip()

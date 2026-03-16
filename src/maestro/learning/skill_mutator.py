"""SkillMutator — agent-driven Skill patch and Experience generation.

Rather than calling an LLM API directly, this module re-uses the *already
configured* agent runner (either :class:`HeadlessRunner` for Cursor or
:class:`ClaudeCodeRunner` for Claude Code).  No additional API key is required.

Mechanism — Evolution Workspace
--------------------------------
For each generation request the mutator:

1. Creates (or resets) a lightweight *evolution workspace* directory.
2. Writes all relevant context as plain files under ``context/``.
3. Builds a meta-prompt that instructs the agent to read those files and
   write its outputs to specific paths under ``output/``.
4. Calls ``runner.run_turn(workspace=evolution_ws, prompt=meta_prompt)``.
5. Reads back the output files.
6. Returns the parsed results.

The evolution workspace is **not** a real code repository — it is just a
directory of context JSON/Markdown files.  The agent uses its Read/Write
tools to process them, keeping the interface backend-agnostic.

Critique cycle (``generate_critique``)
---------------------------------------
Inspired by XSkill's *Cross-Rollout Critique*, the primary cycle now:

- Provides both **success trajectories** and **failure trajectories** side-by-side
  so the agent can do contrastive causal analysis.
- Provides the **current Experience Bank** so the agent can propose merges /
  deletions as well as new entries.
- Produces **three outputs** in one agent turn:

  * ``output/addendum.md``          — Markdown addendum for the SKILL.md
  * ``output/new_experiences.json`` — New Experience entries (add ops)
  * ``output/consolidation_ops.json`` — Merge / delete ops on existing Experiences

New-Skill cycle (``generate_new_skill``) is unchanged in mechanism but uses
the same richer prompt style.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from maestro.learning.experience_bank import ExperienceBank
from maestro.learning.skill_analyser import SkillPatch
from maestro.learning.flow_distiller import FlowPattern

if TYPE_CHECKING:
    from maestro.agent.headless import HeadlessRunner
    from maestro.agent.claude_code import ClaudeCodeRunner

log = logging.getLogger(__name__)

# Cap on how many records / steps are serialised to context files
_MAX_STEPS_PER_RECORD = 30
_MAX_CONTEXT_CHARS = 6_000


class CritiqueResult:
    """Parsed output from a ``generate_critique`` run."""

    __slots__ = ("addendum", "new_experience_ops", "consolidation_ops")

    def __init__(
        self,
        addendum: str | None,
        new_experience_ops: list[dict],
        consolidation_ops: list[dict],
    ) -> None:
        self.addendum = addendum
        self.new_experience_ops = new_experience_ops
        self.consolidation_ops = consolidation_ops


class SkillMutator:
    """Generate Skill patches, Experiences, and new Skills via the configured runner.

    Parameters
    ----------
    runner:
        A :class:`HeadlessRunner` or :class:`ClaudeCodeRunner` instance —
        the same runner type that :class:`Worker` uses for normal issue runs.
    evolution_workspace:
        Path to the dedicated evolution workspace directory.  It is wiped and
        recreated before each request.
    experience_bank:
        The active :class:`ExperienceBank` — read to provide current experiences
        as context; written by the caller (EvolutionLoop) after this method returns.
    """

    def __init__(
        self,
        runner: "HeadlessRunner | ClaudeCodeRunner",
        evolution_workspace: Path,
        experience_bank: ExperienceBank,
    ) -> None:
        self._runner = runner
        self._workspace = evolution_workspace
        self._exp_bank = experience_bank
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_critique(self, patch: SkillPatch) -> CritiqueResult:
        """Run a Cross-Rollout Critique for *patch.skill_name*.

        The agent receives both success and failure trajectories side-by-side
        together with the current Experience Bank and is asked to produce:

        - ``output/addendum.md``             — Markdown addendum (may be empty)
        - ``output/new_experiences.json``    — New add operations
        - ``output/consolidation_ops.json``  — Merge / delete operations

        Returns a :class:`CritiqueResult` even on partial failure.
        """
        with self._lock:
            self._prepare_workspace()
            self._write_critique_context(patch)
            prompt = self._critique_prompt(patch.skill_name)

            result = self._runner.run_turn(
                workspace=self._workspace,
                prompt=prompt,
            )
            if not result.success:
                log.warning(
                    "SkillMutator: critique failed for %r: %s",
                    patch.skill_name, result.error,
                )
                return CritiqueResult(None, [], [])

            addendum = self._read_output_text("addendum.md")
            new_exp_ops = self._read_output_json("new_experiences.json")
            consol_ops = self._read_output_json("consolidation_ops.json")

            log.info(
                "SkillMutator: critique for %r → addendum=%s, new_exp=%d, consol=%d",
                patch.skill_name,
                f"{len(addendum)} chars" if addendum else "none",
                len(new_exp_ops),
                len(consol_ops),
            )
            return CritiqueResult(addendum, new_exp_ops, consol_ops)

    def generate_new_skill(
        self,
        pattern: FlowPattern,
        existing_skill_names: list[str],
    ) -> tuple[str, str] | None:
        """Generate a new SKILL.md from *pattern*.

        Returns ``(skill_name, skill_content)`` or ``None`` on failure.
        """
        with self._lock:
            self._prepare_workspace()
            self._write_pattern_context(pattern, existing_skill_names)
            prompt = self._new_skill_prompt(pattern.suggested_name)

            result = self._runner.run_turn(
                workspace=self._workspace,
                prompt=prompt,
            )
            if not result.success:
                log.warning(
                    "SkillMutator: new-skill generation failed for %r: %s",
                    pattern.suggested_name, result.error,
                )
                return None

            content = self._read_output_text("new_skill.md")
            if not content:
                return None
            name = self._extract_skill_name(content) or pattern.suggested_name
            return name, content

    # ------------------------------------------------------------------
    # Workspace management
    # ------------------------------------------------------------------

    def _prepare_workspace(self) -> None:
        if self._workspace.exists():
            shutil.rmtree(self._workspace)
        (self._workspace / "context").mkdir(parents=True)
        (self._workspace / "output").mkdir(parents=True)

    def _read_output_text(self, filename: str) -> str | None:
        path = self._workspace / "output" / filename
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            return text or None
        return None

    def _read_output_json(self, filename: str) -> list[dict]:
        path = self._workspace / "output" / filename
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except (json.JSONDecodeError, OSError):
            log.debug("SkillMutator: could not parse %s", filename)
        return []

    # ------------------------------------------------------------------
    # Context serialisation — Critique cycle
    # ------------------------------------------------------------------

    def _write_critique_context(self, patch: SkillPatch) -> None:
        ctx = self._workspace / "context"

        # current_skill.md — full SKILL body for reference
        (ctx / "current_skill.md").write_text(
            f"# Skill: {patch.skill_name}\n\n"
            "## Existing Learned Section\n\n"
            + (patch.current_learned_section or "(none yet)")[:_MAX_CONTEXT_CHARS],
            encoding="utf-8",
        )

        # success_trajectories.json — full tool-call chains from successful runs
        success_data = _serialise_records(patch.success_records)
        (ctx / "success_trajectories.json").write_text(
            json.dumps(success_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # failure_trajectories.json — full tool-call chains from failed runs
        failure_data = _serialise_records(patch.failure_records)
        (ctx / "failure_trajectories.json").write_text(
            json.dumps(failure_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # experience_bank.json — current experiences for consolidation
        (ctx / "experience_bank.json").write_text(
            json.dumps(
                self._exp_bank.to_context_list(),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # instructions.md — summary of what the agent needs to produce
        (ctx / "instructions.md").write_text(
            _INSTRUCTIONS_TEMPLATE.format(skill_name=patch.skill_name),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Context serialisation — New-Skill cycle
    # ------------------------------------------------------------------

    def _write_pattern_context(
        self,
        pattern: FlowPattern,
        existing_names: list[str],
    ) -> None:
        ctx = self._workspace / "context"

        (ctx / "flow_pattern.json").write_text(
            json.dumps({
                "tool_sequence": pattern.tool_sequence,
                "occurrences": pattern.occurrences,
                "suggested_name": pattern.suggested_name,
                "description_hint": pattern.description_hint,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        samples = []
        for run in pattern.sample_runs[:5]:
            samples.append({
                "issue": run.issue_identifier,
                "labels": run.labels,
                "total_turns": run.total_turns,
                "relevant_steps": [
                    {"tool": s.tool_name, "path": s.tool_path}
                    for s in run.steps
                    if s.tool_name in pattern.tool_sequence
                ][:_MAX_STEPS_PER_RECORD],
            })
        (ctx / "sample_runs.json").write_text(
            json.dumps(samples, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        (ctx / "existing_skills.txt").write_text(
            "\n".join(sorted(existing_names)),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Prompt templates
    # ------------------------------------------------------------------

    @staticmethod
    def _critique_prompt(skill_name: str) -> str:
        return f"""\
You are the knowledge manager for the Maestro coding-agent orchestrator.

Your task is to analyse the execution history for the Skill named **"{skill_name}"**
and produce three outputs that improve the system's knowledge base.

## Step 1 — Read all context files

Read these files from the `context/` directory:

- `context/current_skill.md`          — the Skill's existing learned section
- `context/success_trajectories.json` — full tool-call sequences from successful runs
- `context/failure_trajectories.json` — full tool-call sequences from failed runs
- `context/experience_bank.json`      — current action-level Experience entries
- `context/instructions.md`           — detailed output format requirements

## Step 2 — Cross-Rollout Critique

Compare the success and failure trajectories:

1. At which step do the two groups start to diverge?
2. What is the **root cause** of the failures? (Not the error message itself, but
   the underlying wrong decision or missing step.)
3. Which steps in the successful trajectories are the key decision points that
   prevented failure?

## Step 3 — Write your three outputs

### output/addendum.md
If the analysis reveals a pattern worth adding to the Skill's documented workflow,
write a concise Markdown addendum (< 400 words).
- Do NOT repeat anything already in `context/current_skill.md`.
- Do NOT include shell scripts or runnable code blocks.
- If there is nothing new to add, write an empty file.

### output/new_experiences.json
Write a JSON array of new Experience entries to add to the bank.
Format each entry as:
```json
[
  {{
    "action": "add",
    "condition": "When <triggering situation>",
    "action_text": "<recommended response>",
    "source_issues": ["<issue-id>", ...]
  }}
]
```
Use the field name `"action_text"` (not `"action"`) to avoid collision with the
operation type field.
If there are no new experiences to add, write `[]`.

### output/consolidation_ops.json
Write a JSON array of operations to merge or remove **existing** Experience entries
(from `context/experience_bank.json`) that are now redundant, contradicted, or
covered by the new ones.
Format:
```json
[
  {{"action": "modify", "id": "<existing-id>", "condition": "...", "action_text": "..."}},
  {{"action": "delete", "id": "<existing-id>"}}
]
```
If no consolidation is needed, write `[]`.

Write all three files now.
"""

    @staticmethod
    def _new_skill_prompt(suggested_name: str) -> str:
        return f"""\
You are a Skill author for the Maestro coding-agent orchestrator.

Your task is to create a new SKILL.md for a reusable workflow pattern that has been
automatically detected in the agent's execution history.

## Instructions

1. Read all files in the `context/` directory:
   - `context/flow_pattern.json`  — the tool sequence and its frequency
   - `context/sample_runs.json`   — concrete examples showing the pattern in use
   - `context/existing_skills.txt` — existing Skill names (avoid duplicates)

2. Understand what the pattern does:
   - What is the high-level purpose of this tool sequence?
   - Under what conditions does the agent use it?
   - What are the prerequisites and expected outcomes?

3. Write a complete SKILL.md to `output/new_skill.md` using this structure:

```
---
name: <kebab-case-name>
description: <one-sentence description, ≤ 120 characters>
---

# <Title>

<One paragraph explaining when to use this Skill.>

## Prerequisites

- <prerequisite>

## Step-by-Step Procedure

1. <step>
2. <step>
...

## Notes

- <optional caveats or edge cases>
```

Rules:
- Choose a clear, descriptive name (suggested: `{suggested_name}`, improve if needed).
- Prefer concrete numbered steps over vague advice.
- Do NOT include shell scripts or runnable code blocks.
- Keep total file under 600 words.
- Do NOT duplicate any Skill listed in `context/existing_skills.txt`.

Write ONLY the SKILL.md content to `output/new_skill.md`.
"""

    @staticmethod
    def _extract_skill_name(content: str) -> str | None:
        """Parse the ``name:`` field from SKILL.md frontmatter."""
        if not content.startswith("---"):
            return None
        end = content.find("\n---", 3)
        if end == -1:
            return None
        for line in content[3:end].splitlines():
            if line.startswith("name:"):
                return line.split(":", 1)[1].strip().strip("\"'") or None
        return None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_INSTRUCTIONS_TEMPLATE = """\
# Critique Instructions for Skill: {skill_name}

## Output files you must produce

| File | Purpose |
|------|---------|
| `output/addendum.md` | Markdown additions to the Skill's learned section (empty if nothing new) |
| `output/new_experiences.json` | New Experience entries to add to the bank |
| `output/consolidation_ops.json` | Merge/delete ops for existing Experience entries |

## What makes a good Experience entry

An Experience captures a **specific, actionable** insight:

```
condition:   "When pytest reports 'ModuleNotFoundError' for a local package"
action_text: "Check pyproject.toml [tool.poetry.packages] before modifying sys.path;
              the missing entry there is the most common root cause."
```

Bad (too vague):
```
condition:   "When there is an import error"
action_text: "Fix the imports."
```

## Consolidation rules

- If a new Experience says the same thing as an existing one → **modify** to combine
- If an existing Experience is now known to be wrong → **delete** it
- If an existing Experience is a subset of a new one → **delete** old, **add** new
"""


def _serialise_records(records: list) -> list[dict]:
    """Convert RunRecord objects to JSON-safe dicts for trajectory context files."""
    out = []
    for rec in records:
        out.append({
            "issue": rec.issue_identifier,
            "success": rec.success,
            "error": rec.error,
            "duration_ms": rec.duration_ms,
            "files_changed": rec.files_changed[:10],
            "output_summary": rec.output_summary[:300],
            "tool_sequence": rec.tool_sequence[:_MAX_STEPS_PER_RECORD],
        })
    return out

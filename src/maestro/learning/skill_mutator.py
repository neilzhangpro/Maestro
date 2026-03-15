"""SkillMutator — agent-driven Skill patch and new-Skill generation.

Rather than calling an LLM API directly, this module re-uses the *already
configured* agent runner (either :class:`HeadlessRunner` for Cursor or
:class:`ClaudeCodeRunner` for Claude Code) so that no additional API key is
required.

Mechanism — Evolution Workspace
--------------------------------
For each generation request the mutator:

1. Creates (or resets) a lightweight *evolution workspace* directory.
2. Writes all relevant context as plain files under ``context/``.
3. Builds a meta-prompt that instructs the agent to read those files and
   write its output to a specific path under ``output/``.
4. Calls ``runner.run_turn(workspace=evolution_ws, prompt=meta_prompt)``.
5. Reads back the output file.
6. Returns the content (or ``None`` on failure).

The evolution workspace is **not** a real code repository — it is just a
directory of context JSON/Markdown files.  The agent uses its Read/Write
tools to process them, which keeps the interface simple and backend-agnostic.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from maestro.learning.skill_analyser import SkillPatch
from maestro.learning.flow_distiller import FlowPattern, FlowDistiller

if TYPE_CHECKING:
    from maestro.agent.headless import HeadlessRunner, TurnResult
    from maestro.agent.claude_code import ClaudeCodeRunner

log = logging.getLogger(__name__)

# Maximum characters written to context files (guards against huge payloads)
_MAX_RECORD_CHARS = 2_000
_MAX_CONTEXT_CHARS = 8_000


class SkillMutator:
    """Generate Skill patches and new Skills via the configured agent runner.

    Parameters
    ----------
    runner:
        A :class:`HeadlessRunner` or :class:`ClaudeCodeRunner` instance — the
        same runner type that :class:`Worker` uses for normal issue runs.
    evolution_workspace:
        Path to the dedicated evolution workspace directory.  It is wiped and
        recreated before each request.
    """

    def __init__(
        self,
        runner: "HeadlessRunner | ClaudeCodeRunner",
        evolution_workspace: Path,
    ) -> None:
        self._runner = runner
        self._workspace = evolution_workspace
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_addendum(self, patch: SkillPatch) -> str | None:
        """Return a Markdown addendum for *patch.skill_name*, or ``None``."""
        with self._lock:
            self._prepare_workspace()
            self._write_patch_context(patch)
            prompt = self._addendum_prompt(patch.skill_name)
            result = self._runner.run_turn(
                workspace=self._workspace,
                prompt=prompt,
            )
            if not result.success:
                log.warning(
                    "SkillMutator: addendum generation failed for %r: %s",
                    patch.skill_name, result.error,
                )
                return None
            return self._read_output("addendum.md")

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
            content = self._read_output("new_skill.md")
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

    def _read_output(self, filename: str) -> str | None:
        path = self._workspace / "output" / filename
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            return text or None
        return None

    # ------------------------------------------------------------------
    # Context serialisation
    # ------------------------------------------------------------------

    def _write_patch_context(self, patch: SkillPatch) -> None:
        ctx = self._workspace / "context"

        # current_skill.md — full current SKILL body for reference
        from maestro.learning.skill_store import SkillStoreError
        try:
            skill_body = (
                patch.current_learned_section
                or "(no existing learned section)"
            )
        except Exception:
            skill_body = "(could not read existing skill)"
        (ctx / "current_skill.md").write_text(
            f"# Current Skill: {patch.skill_name}\n\n"
            "## Existing Learned Section\n\n"
            + skill_body[:_MAX_CONTEXT_CHARS],
            encoding="utf-8",
        )

        # failure_patterns.json
        fp_data = [
            {
                "error": fp.error,
                "count": fp.count,
                "samples": fp.sample_summaries[:3],
            }
            for fp in patch.failure_patterns
        ]
        (ctx / "failure_patterns.json").write_text(
            json.dumps(fp_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # success_patterns.json
        sp_data = [
            {"tool_sequence": sp.tool_sequence, "count": sp.count}
            for sp in patch.success_patterns
        ]
        (ctx / "success_patterns.json").write_text(
            json.dumps(sp_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # recent_records.json (capped)
        records_data = []
        for rec in patch.recent_records[-10:]:
            records_data.append({
                "issue": rec.issue_identifier,
                "success": rec.success,
                "error": rec.error,
                "tools_used": rec.tools_used,
                "summary": rec.output_summary[:200],
            })
        (ctx / "recent_records.json").write_text(
            json.dumps(records_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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
                ][:20],
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
    def _addendum_prompt(skill_name: str) -> str:
        return f"""\
You are a Skill evolution analyst working inside the Maestro coding-agent orchestrator.

Your task is to generate a concise experience addendum for the Skill named "{skill_name}".

## Instructions

1. Read all files in the `context/` directory:
   - `context/current_skill.md` — the existing learned section (if any)
   - `context/failure_patterns.json` — recurring failure error codes with sample output
   - `context/success_patterns.json` — common tool sequences from successful runs
   - `context/recent_records.json` — recent raw run records for additional context

2. Analyse the failure patterns:
   - Identify the root cause of each recurring error.
   - Propose a concrete mitigation step the agent can follow to avoid it next time.

3. Analyse the success patterns:
   - Extract any repeating tool sequences that represent a reusable sub-workflow.
   - Describe the sub-workflow as a concise numbered procedure.

4. Write the addendum to `output/addendum.md`:
   - Use plain Markdown.
   - Start with bullet points for each failure mitigation.
   - Follow with a brief numbered procedure for any success pattern (if relevant).
   - Keep the total addendum under 500 words.
   - Do NOT repeat content already present in the existing learned section.
   - Do NOT include shell scripts or executable code blocks.
   - Do NOT include a frontmatter section.

Write ONLY the addendum content to `output/addendum.md` — nothing else.
"""

    @staticmethod
    def _new_skill_prompt(suggested_name: str) -> str:
        return f"""\
You are a Skill author working inside the Maestro coding-agent orchestrator.

Your task is to create a new SKILL.md for a reusable workflow pattern that has been
automatically detected in the agent's execution history.

## Instructions

1. Read all files in the `context/` directory:
   - `context/flow_pattern.json` — the tool sequence and its frequency
   - `context/sample_runs.json` — concrete examples showing the pattern in use
   - `context/existing_skills.txt` — existing Skill names (avoid duplicates)

2. Understand what the pattern does:
   - What is the high-level purpose of this tool sequence?
   - Under what conditions does the agent use it?
   - What are the prerequisites and expected outcomes?

3. Write a complete SKILL.md to `output/new_skill.md` using this structure:

```
---
name: <kebab-case-name>
description: <one-sentence description>
---

# <Title>

<Brief introduction explaining when to use this Skill.>

## Prerequisites

- <list prerequisites>

## Step-by-Step Procedure

1. <step>
2. <step>
...

## Notes

- <optional caveats or edge cases>
```

Rules:
- Choose a clear, descriptive name (suggested: `{suggested_name}` — but use a better name if appropriate).
- The description must be one sentence, ≤ 120 characters.
- Prefer concrete, numbered steps over vague advice.
- Do NOT include shell scripts or executable code blocks.
- Keep the total file under 600 words.
- Do NOT duplicate any Skill listed in `context/existing_skills.txt`.

Write ONLY the SKILL.md content to `output/new_skill.md` — nothing else.
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

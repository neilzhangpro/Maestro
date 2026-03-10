"""Parse WORKFLOW.md into config mapping + prompt template."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class WorkflowLoadError(ValueError):
    """Raised when the workflow file cannot be loaded or parsed."""


@dataclass(frozen=True)
class WorkflowDefinition:
    config: dict[str, Any]
    prompt_template: str
    source_path: Path


_FRONT_MATTER_SEP = "---"


def load_workflow(path: Path | None = None) -> WorkflowDefinition:
    resolved = Path(path) if path is not None else Path("WORKFLOW.md")
    if not resolved.is_absolute():
        resolved = (Path.cwd() / resolved).resolve()

    if not resolved.exists():
        raise WorkflowLoadError(f"Workflow file not found: {resolved}")

    text = resolved.read_text(encoding="utf-8")
    config, prompt = _split_front_matter(text)
    return WorkflowDefinition(config=config, prompt_template=prompt, source_path=resolved)


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    stripped = text.lstrip()
    if not stripped.startswith(_FRONT_MATTER_SEP):
        return {}, text.strip()

    after_first = stripped[len(_FRONT_MATTER_SEP) :]
    end_idx = after_first.find(f"\n{_FRONT_MATTER_SEP}")
    if end_idx == -1:
        raise WorkflowLoadError("YAML front matter opened but never closed (missing second ---).")

    yaml_block = after_first[:end_idx]
    body_start = end_idx + len(f"\n{_FRONT_MATTER_SEP}")
    body = after_first[body_start:]

    parsed = yaml.safe_load(yaml_block)
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise WorkflowLoadError("YAML front matter must be a mapping/object.")

    return parsed, body.strip()

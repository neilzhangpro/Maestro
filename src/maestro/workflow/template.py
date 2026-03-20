"""Liquid-compatible prompt template rendering."""

from __future__ import annotations

from typing import Any

from liquid import Environment


class TemplateRenderError(ValueError):
    """Raised when prompt template rendering fails."""


_ENV = Environment()

_MANDATORY_HANDOFF_POLICY = """
## Mandatory Human Review Handoff
This policy is enforced by Maestro and overrides any repository workflow that says otherwise.

1. Complete the implementation and local validation in the workspace.
2. When development is complete, move the Linear issue to `Human Review`.
3. Leave a short Linear comment summarizing what changed, what was validated, and any remaining risks.
4. Stop after the handoff. Human Review and Maestro's submission pipeline will handle commit, push, PR, CI, and merge.

## Git Submission Guardrail
You must NOT perform any repository submission actions yourself.
- Do NOT run `git commit`.
- Do NOT run `git push`.
- Do NOT create or update any Pull Request.
- Do NOT move the issue to `In Review` or `Done` as part of development completion.

If the code is ready, the correct terminal action is: update the issue to `Human Review`, add the handoff comment, then stop.
""".strip()


def render_prompt(
    template_source: str,
    *,
    issue: dict[str, Any],
    attempt: int | None = None,
    learning_context: str | None = None,
    backend: str = "cursor",
) -> str:
    try:
        tpl = _ENV.from_string(template_source)
        return tpl.render(
            issue=issue,
            attempt=attempt,
            learning_context=learning_context,
            backend=backend,
        ).strip()
    except Exception as exc:
        raise TemplateRenderError(f"Prompt rendering failed: {exc}") from exc


def compose_agent_prompt(rendered_prompt: str) -> str:
    """Append Maestro's non-optional handoff policy to a rendered prompt."""
    base = rendered_prompt.strip()
    if not base:
        return _MANDATORY_HANDOFF_POLICY
    return f"{base}\n\n{_MANDATORY_HANDOFF_POLICY}"

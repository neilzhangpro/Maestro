"""Liquid-compatible prompt template rendering."""

from __future__ import annotations

from typing import Any

from liquid import Environment


class TemplateRenderError(ValueError):
    """Raised when prompt template rendering fails."""


_ENV = Environment()


def render_prompt(
    template_source: str,
    *,
    issue: dict[str, Any],
    attempt: int | None = None,
    learning_context: str | None = None,
) -> str:
    try:
        tpl = _ENV.from_string(template_source)
        return tpl.render(
            issue=issue,
            attempt=attempt,
            learning_context=learning_context,
        ).strip()
    except Exception as exc:
        raise TemplateRenderError(f"Prompt rendering failed: {exc}") from exc

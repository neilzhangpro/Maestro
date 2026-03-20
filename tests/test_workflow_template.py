from __future__ import annotations

from maestro.workflow.template import compose_agent_prompt, render_prompt


def test_compose_agent_prompt_appends_human_review_policy() -> None:
    prompt = compose_agent_prompt("Implement the issue.")

    assert "Mandatory Human Review Handoff" in prompt
    assert "move the Linear issue to `Human Review`" in prompt
    assert "Do NOT run `git commit`." in prompt
    assert "Do NOT run `git push`." in prompt
    assert "Do NOT create or update any Pull Request." in prompt


def test_rendered_prompt_can_be_wrapped_with_runtime_policy() -> None:
    rendered = render_prompt(
        "Issue {{ issue.identifier }} in {{ issue.state }}",
        issue={"identifier": "MAE-1", "state": "In Progress"},
    )

    prompt = compose_agent_prompt(rendered)

    assert prompt.startswith("Issue MAE-1 in In Progress")
    assert prompt.endswith(
        "If the code is ready, the correct terminal action is: update the issue to `Human Review`, add the handoff comment, then stop."
    )

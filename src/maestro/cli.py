"""CLI entrypoint for Maestro."""

from __future__ import annotations

from pathlib import Path

import typer

from maestro import __version__
from maestro.config import LinearConfig, load_config
from maestro.linear.client import LinearClient
from maestro.workspace.manager import WorkspaceManager

app = typer.Typer(help="Maestro — Symphony-compatible coding agent orchestrator.")
workspace_app = typer.Typer(help="Workspace utilities.")
app.add_typer(workspace_app, name="workspace")


@app.command()
def version() -> None:
    """Print the installed Maestro version."""
    typer.echo(__version__)


@app.command()
def start(
    workflow: Path = typer.Argument(
        None, help="Path to WORKFLOW.md (default: ./WORKFLOW.md)",
    ),
    port: int | None = typer.Option(
        None, "--port", help="HTTP server port (overrides WORKFLOW.md server.port)",
    ),
) -> None:
    """Start the Maestro orchestration service.

    Reads WORKFLOW.md, polls the issue tracker, and dispatches Cursor agent
    workers to handle issues concurrently.
    """
    from maestro.service import MaestroService
    from maestro.workflow.config import ConfigError
    from maestro.workflow.loader import WorkflowLoadError

    wf_path = workflow
    if wf_path and not wf_path.exists():
        typer.echo(f"Error: {wf_path} not found.", err=True)
        raise typer.Exit(1)

    try:
        service = MaestroService(workflow_path=wf_path, port=port)
        service.start()
    except (WorkflowLoadError, ConfigError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command("run")
def run_once(
    issue_ref: str = typer.Argument(..., help="Issue identifier (e.g. MAE-42)"),
    workflow: Path = typer.Argument(
        None, help="Path to WORKFLOW.md",
    ),
) -> None:
    """Run a single issue through the Cursor agent (one-shot mode)."""
    from maestro.agent.headless import HeadlessRunner
    from maestro.linear.models import Issue
    from maestro.workflow.config import ConfigError, ServiceConfig
    from maestro.workflow.loader import WorkflowLoadError, load_workflow
    from maestro.workflow.template import compose_agent_prompt, render_prompt
    from maestro.workspace.hooks import ShellHooks
    from maestro.workspace.manager import WorkspaceManager
    from maestro.config import LinearConfig
    from maestro.linear.client import LinearClient, LinearError

    try:
        wd = load_workflow(workflow)
        config = ServiceConfig.from_workflow(wd)
    except (WorkflowLoadError, ConfigError) as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(1) from exc

    linear_cfg = LinearConfig(
        api_key=config.tracker.api_key,
        api_url=config.tracker.endpoint,
        project_slug=config.tracker.project_slug or None,
        team_id=config.tracker.team_id,
        assignee=config.tracker.assignee,
        active_states=config.tracker.active_states,
        terminal_states=config.tracker.terminal_states,
        timeout_s=config.tracker.timeout_s,
    )

    try:
        with LinearClient(linear_cfg) as client:
            issue = client.fetch_issue(issue_ref)
    except LinearError as exc:
        typer.echo(f"Linear error: {exc}", err=True)
        raise typer.Exit(1) from exc

    resolved = config.resolved_hooks()
    hooks = ShellHooks(
        after_create_script=resolved.after_create,
        before_run_script=resolved.before_run,
        after_run_script=resolved.after_run,
        timeout_ms=resolved.timeout_ms,
    )
    manager = WorkspaceManager(config.workspace.root, hooks=hooks)
    workspace = manager.prepare_workspace(issue.identifier)
    manager.run_before(workspace)

    prompt = compose_agent_prompt(
        render_prompt(
            config.prompt_template,
            issue=issue.to_template_dict(),
            backend=config.backend,
        )
    )

    typer.echo(f"Issue:     {issue.identifier} — {issue.title}")
    typer.echo(f"Workspace: {workspace.path}")
    typer.echo("")

    if config.backend == "claude_code" and config.claude_code:
        from maestro.agent.claude_code import ClaudeCodeRunner
        runner = ClaudeCodeRunner(config.claude_code)
    else:
        runner = HeadlessRunner(config.cursor)

    result = runner.run_turn(
        workspace=workspace.path,
        prompt=prompt,
        on_event=lambda e: typer.echo(f"  [{e.event}] {e.message or e.tool_path or ''}"),
    )

    typer.echo("")
    if result.success:
        typer.echo(f"Completed in {result.duration_ms}ms (session={result.session_id})")
    else:
        typer.echo(f"Failed: {result.error}")
        raise typer.Exit(1)

    manager.run_after(workspace)


@app.command()
def tui(
    url: str = typer.Option(
        "http://127.0.0.1:8080",
        "--url",
        help="Maestro API base URL.",
    ),
) -> None:
    """Launch the interactive terminal workbench."""
    from maestro.tui.app import run_tui

    run_tui(url)


@app.command("list")
def list_issues(
    workflow: Path = typer.Argument(None, help="Path to WORKFLOW.md"),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to legacy maestro.yaml config.",
    ),
    state: list[str] | None = typer.Option(None, "--state", help="Filter by state."),
) -> None:
    """List candidate Linear issues."""
    if config is not None:
        loaded = load_config(config)
        linear_cfg = loaded.linear
    else:
        from maestro.workflow.config import ServiceConfig
        from maestro.workflow.loader import load_workflow

        wd = load_workflow(workflow)
        service_config = ServiceConfig.from_workflow(wd)
        linear_cfg = LinearConfig(
            api_key=service_config.tracker.api_key,
            api_url=service_config.tracker.endpoint,
            project_slug=service_config.tracker.project_slug or None,
            team_id=service_config.tracker.team_id,
            assignee=service_config.tracker.assignee,
            active_states=service_config.tracker.active_states,
            terminal_states=service_config.tracker.terminal_states,
            timeout_s=service_config.tracker.timeout_s,
        )

    with LinearClient(linear_cfg) as client:
        issues = client.fetch_issues(state_names=state or None)

    if not issues:
        typer.echo("No issues matched.")
        return

    for issue in issues:
        typer.echo(f"{issue.identifier:<12} {issue.state:<15} {issue.title}")


@workspace_app.command("show")
def workspace_show(
    issue_ref: str = typer.Argument(..., help="Issue identifier (e.g. MAE-42)"),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to maestro.yaml (default: ./config/maestro.yaml).",
    ),
) -> None:
    """Print the local workspace path for an issue identifier."""
    loaded = load_config(config)
    manager = WorkspaceManager(loaded.workspace.root)
    typer.echo(manager.workspace_path_for(issue_ref))


if __name__ == "__main__":
    app()

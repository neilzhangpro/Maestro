"""CLI entrypoint for Maestro."""

from __future__ import annotations

from pathlib import Path

import typer

from maestro import __version__

app = typer.Typer(help="Maestro — Symphony-compatible coding agent orchestrator.")


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
    from maestro.workflow.template import render_prompt
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

    hooks = ShellHooks(
        after_create_script=config.hooks.after_create,
        before_run_script=config.hooks.before_run,
        after_run_script=config.hooks.after_run,
        timeout_ms=config.hooks.timeout_ms,
    )
    manager = WorkspaceManager(config.workspace.root, hooks=hooks)
    workspace = manager.prepare_workspace(issue.identifier)
    manager.run_before(workspace)

    prompt = render_prompt(
        config.prompt_template,
        issue=issue.to_template_dict(),
    )

    typer.echo(f"Issue:     {issue.identifier} — {issue.title}")
    typer.echo(f"Workspace: {workspace.path}")
    typer.echo("")

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


@app.command("list")
def list_issues(
    workflow: Path = typer.Argument(None, help="Path to WORKFLOW.md"),
    state: list[str] | None = typer.Option(None, "--state", help="Filter by state."),
) -> None:
    """List candidate Linear issues."""
    from maestro.workflow.config import ServiceConfig
    from maestro.workflow.loader import load_workflow
    from maestro.config import LinearConfig
    from maestro.linear.client import LinearClient

    wd = load_workflow(workflow)
    config = ServiceConfig.from_workflow(wd)
    linear_cfg = LinearConfig(
        api_key=config.tracker.api_key,
        api_url=config.tracker.endpoint,
        project_slug=config.tracker.project_slug or None,
        active_states=config.tracker.active_states,
        terminal_states=config.tracker.terminal_states,
        timeout_s=config.tracker.timeout_s,
    )

    with LinearClient(linear_cfg) as client:
        issues = client.fetch_issues(state_names=state or None)

    if not issues:
        typer.echo("No issues matched.")
        return

    for issue in issues:
        typer.echo(f"{issue.identifier:<12} {issue.state:<15} {issue.title}")


if __name__ == "__main__":
    app()

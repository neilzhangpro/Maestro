"""Maestro Terminal Workbench — rich + questionary interactive console."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any

import questionary
from questionary import Style as QStyle
from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from maestro import __version__
from maestro.tui.client import MaestroAPIClient

# ── Colours & Styles ────────────────────────────────────────────────────────

PROMPT_STYLE = QStyle([
    ("qmark", "fg:cyan bold"),
    ("question", "fg:white bold"),
    ("answer", "fg:cyan bold"),
    ("pointer", "fg:cyan bold"),
    ("highlighted", "fg:cyan bold"),
    ("selected", "fg:cyan"),
    ("separator", "fg:#6c6c6c"),
    ("instruction", "fg:#6c6c6c"),
])

PRIORITY_MAP: dict[int | None, tuple[str, str]] = {
    0: ("dim", "—"),
    1: ("red bold", "P0 ▲"),
    2: ("yellow", "P1 ↑"),
    3: ("blue", "P2 ●"),
    4: ("dim", "P3 ↓"),
    None: ("dim", "—"),
}

STATE_ICONS: dict[str, str] = {
    "backlog": "[dim]◌[/dim]",
    "todo": "[white]○[/white]",
    "in progress": "[yellow]●[/yellow]",
    "done": "[green]✓[/green]",
    "cancelled": "[red]✗[/red]",
    "closed": "[red]✗[/red]",
    "human review": "[magenta]⊙[/magenta]",
    "in review": "[blue]◉[/blue]",
}

LOGO = r"""[cyan]
 ╔╦╗┌─┐┌─┐┌─┐┌┬┐┬─┐┌─┐
 ║║║├─┤├┤ └─┐ │ ├┬┘│ │
 ╩ ╩┴ ┴└─┘└─┘ ┴ ┴└─└─┘[/cyan]"""

ACTIONS_ALL = [
    "↻  Refresh",
    "▶  Run issue",
    "🛑 Stop worker",
    "⇄  Move issue",
    "🔍 Issue detail",
    "🧪 E2E Test",
    "⟳  Force poll",
    "⎋  Quit",
]

ACTIONS_DISCONNECTED = [
    "↻  Refresh",
    "⎋  Quit",
]

MOVABLE_STATES = ["Backlog", "Todo", "In Progress", "In Review", "Done", "Human Review", "Cancelled"]

BACK_LABEL = "← Back"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _truncate(value: str | None, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text or "—"
    return text[: max(0, limit - 1)].rstrip() + "…"


def _state_counts(issues: list[dict]) -> list[tuple[str, int]]:
    order = ["In Progress", "Todo", "Human Review", "In Review", "Backlog", "Done"]
    counts: dict[str, int] = {}
    for issue in issues:
        state = str(issue.get("state") or "Unknown")
        counts[state] = counts.get(state, 0) + 1
    rows: list[tuple[str, int]] = []
    for state in order:
        if counts.get(state):
            rows.append((state, counts[state]))
    for state, count in sorted(counts.items()):
        if state not in order:
            rows.append((state, count))
    return rows


def _issue_choice_label(issue: dict[str, Any], *, include_state: bool = False) -> str:
    ident = str(issue.get("identifier") or "?")
    state = str(issue.get("state") or "—")
    title = _truncate(str(issue.get("title") or ""), 58)
    if include_state:
        return f"{ident:<12} [{state:<12}] {title}"
    return f"{ident:<12} {title}"

def _state_icon(state: str) -> str:
    return STATE_ICONS.get(state.strip().lower(), f"[dim]?[/dim]")


def _fmt_priority(p: int | None) -> Text:
    style, label = PRIORITY_MAP.get(p, PRIORITY_MAP[None])
    return Text(label, style=style)


def _elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _fmt_token_count(value: int | float | None) -> str:
    if not value:
        return "0"
    n = int(value)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _clear() -> None:
    os.system("clear" if sys.platform != "win32" else "cls")


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ── Renderers ───────────────────────────────────────────────────────────────

def render_header(console: Console, connected: bool) -> None:
    status = "[green]● connected[/green]" if connected else "[red]● disconnected[/red]"
    header = Text.from_markup(
        f"{LOGO}  [dim]v{__version__}[/dim]  {status}\n"
        "[dim]Linear issue orchestration, human review, PR submission, and CI merge control[/dim]"
    )
    console.print(header)


def render_stats(console: Console, orch: dict[str, Any], issue_count: int) -> None:
    counts = orch.get("counts", {})
    totals = orch.get("totals", {})
    rtk = orch.get("rtk", {})
    running = counts.get("running", 0)
    retrying = counts.get("retrying", 0)
    queued = max(issue_count - running, 0)
    secs = totals.get("seconds_running", 0)

    grid = Table.grid(padding=(0, 3))
    cells = [
        _stat_cell("ISSUES", str(issue_count), "cyan"),
        _stat_cell("READY", str(queued), "white"),
        _stat_cell("RUNNING", str(running), "yellow" if running else "dim"),
        _stat_cell("RETRYING", str(retrying), "red" if retrying else "dim"),
        _stat_cell("AGENT TIME", _elapsed(secs), "green"),
    ]
    if rtk.get("enabled"):
        saved = _fmt_token_count(rtk.get("estimated_tokens_saved", 0))
        cells.append(_stat_cell("RTK SAVED", saved, "magenta"))
    grid.add_row(*cells)
    console.print(
        Panel(grid, border_style="bright_black", box=box.HEAVY_EDGE, padding=(0, 1)),
    )


def render_state_summary(console: Console, issues: list[dict]) -> None:
    rows = _state_counts(issues)
    if not rows:
        return
    grid = Table.grid(expand=True)
    for _ in rows:
        grid.add_column(justify="center")
    grid.add_row(*[
        Text.from_markup(f"{_state_icon(state)} [bold]{count}[/bold]\n[dim]{state}[/dim]")
        for state, count in rows
    ])
    console.print(
        Panel(
            grid,
            title="[bold]State Summary[/bold]",
            border_style="bright_black",
            box=box.HEAVY_EDGE,
            padding=(0, 1),
        ),
    )


def _stat_cell(label: str, value: str, style: str) -> Text:
    return Text.from_markup(f"[dim]{label}[/dim]\n[{style} bold]{value}[/{style} bold]")


def render_issues(console: Console, issues: list[dict], orch: dict) -> None:
    running_ids = {r.get("issue_identifier") for r in orch.get("running", [])}
    retry_ids = {r.get("issue_identifier") for r in orch.get("retrying", [])}

    tbl = Table(
        box=box.SIMPLE_HEAVY,
        border_style="bright_black",
        show_edge=False,
        pad_edge=False,
        expand=True,
        row_styles=["none", "dim"],
    )
    tbl.add_column("ID", style="cyan", no_wrap=True, width=12)
    tbl.add_column("State", width=16, no_wrap=True)
    tbl.add_column("Pri", width=6, justify="center")
    tbl.add_column("Title", ratio=2)
    tbl.add_column("Labels", ratio=1, style="magenta")
    tbl.add_column("Runtime", width=18, justify="right", no_wrap=True)

    state_order = {"in progress": 0, "todo": 1, "in review": 2, "human review": 3, "backlog": 4, "done": 5}
    pri_order = lambda i: i.get("priority") or 99
    sorted_issues = sorted(
        issues,
        key=lambda i: (state_order.get(i.get("state", "").lower(), 9), pri_order(i)),
    )

    for issue in sorted_issues:
        ident = issue.get("identifier", "?")
        state = issue.get("state", "?")
        icon = _state_icon(state)
        pri = _fmt_priority(issue.get("priority"))
        title = _truncate(issue.get("title", ""), 72)
        labels = _truncate(", ".join(issue.get("labels") or []), 24)

        badge = ""
        if ident in running_ids:
            badge = "[yellow bold]⚡ running[/yellow bold]"
        elif ident in retry_ids:
            badge = "[red]↺ retry[/red]"
        elif state.strip().lower() == "human review":
            badge = "[magenta]manual gate[/magenta]"
        elif state.strip().lower() == "in review":
            badge = "[blue]ci watch[/blue]"

        tbl.add_row(ident, Text.from_markup(f"{icon} {state}"), pri, title, labels, Text.from_markup(badge))

    console.print(
        Panel(
            tbl,
            title=f"[bold]Issues[/bold] [dim]({len(sorted_issues)})[/dim]",
            subtitle="[dim]priority-sorted, active work first[/dim]",
            border_style="bright_black",
            box=box.HEAVY_EDGE,
        ),
    )


def build_workers_panel(orch: dict) -> Panel:
    running = orch.get("running", [])

    if not running:
        return Panel(
            "[dim]No active sessions[/dim]",
            title="[bold]Workers[/bold]",
            border_style="bright_black",
            box=box.HEAVY_EDGE,
        )

    tbl = Table(
        box=box.SIMPLE_HEAVY,
        border_style="bright_black",
        show_edge=False,
        pad_edge=False,
        expand=True,
    )
    tbl.add_column("Issue", style="cyan", no_wrap=True, width=12)
    tbl.add_column("Turn", width=6, justify="right")
    tbl.add_column("Uptime", width=8, justify="right")
    tbl.add_column("Session", width=12)
    tbl.add_column("Status", ratio=1)

    for w in running:
        started = _parse_iso8601(w.get("started_at"))
        uptime = "—"
        if started is not None:
            uptime = _elapsed((datetime.now(timezone.utc) - started).total_seconds())
        tbl.add_row(
            w.get("issue_identifier", "?"),
            str(w.get("turn_count", 0)),
            uptime,
            _truncate(w.get("session_id") or "starting…", 12),
            _truncate(w.get("last_message") or w.get("last_event") or "starting", 40),
        )

    return Panel(
        tbl,
        title=f"[bold]Workers[/bold] [dim]({len(running)})[/dim]",
        border_style="bright_black",
        box=box.HEAVY_EDGE,
    )


def build_recent_work_log_panel(orch: dict) -> Panel:
    entries: list[tuple[datetime, str, str, str]] = []

    for worker in orch.get("running", []):
        ident = worker.get("issue_identifier", "?")
        for item in worker.get("event_history") or []:
            ts = _parse_iso8601(item.get("timestamp"))
            if ts is None:
                continue
            event = str(item.get("event") or "event")
            message = str(item.get("message") or "—")[:120]
            entries.append((ts, ident, event, message))

    for item in orch.get("recent_exits", []):
        ts = _parse_iso8601(item.get("ended_at"))
        if ts is None:
            continue
        ident = item.get("issue_identifier", "?")
        reason = str(item.get("reason") or "exit")
        message = str(item.get("error") or reason)[:120]
        entries.append((ts, ident, f"exit:{reason}", message))

    entries.sort(key=lambda row: row[0], reverse=True)

    if not entries:
        return Panel(
            "[dim]No recent worker activity[/dim]",
            title="[bold]Recent Work Log[/bold]",
            border_style="bright_black",
            box=box.HEAVY_EDGE,
        )

    tbl = Table(
        box=box.SIMPLE_HEAVY,
        border_style="bright_black",
        show_edge=False,
        pad_edge=False,
        expand=True,
        row_styles=["none", "dim"],
    )
    tbl.add_column("Time", width=9, style="dim")
    tbl.add_column("Issue", width=12, style="cyan", no_wrap=True)
    tbl.add_column("Event", width=18)
    tbl.add_column("Message", ratio=1)

    for ts, ident, event, message in entries[:20]:
        tbl.add_row(ts.strftime("%H:%M:%S"), ident, _truncate(event, 18), _truncate(message, 88))

    return Panel(
        tbl,
        title="[bold]Recent Work Log[/bold]",
        subtitle="[dim]latest 20 worker events and exits[/dim]",
        border_style="bright_black",
        box=box.HEAVY_EDGE,
    )


def render_disconnected(console: Console, url: str) -> None:
    console.print(
        Panel(
            f"[red bold]Cannot reach Maestro API[/red bold]\n\n"
            f"  endpoint  [cyan]{url}[/cyan]\n"
            f"  fix       [green]maestro start[/green]  or  [green]make up[/green]\n",
            title="[red]Connection Error[/red]",
            border_style="red",
            box=box.DOUBLE_EDGE,
        ),
    )


def render_footer(console: Console, connected: bool) -> None:
    if not connected:
        return
    console.print(
        Panel(
            "[dim]Run issue[/dim] dispatches new work.  "
            "[dim]Issue detail[/dim] shows workspace context.  "
            "[dim]E2E Test[/dim] is the human submit gate for issues in Human Review.",
            border_style="bright_black",
            box=box.SIMPLE,
            padding=(0, 1),
        ),
    )


# ── Actions ─────────────────────────────────────────────────────────────────

def action_run_issue(
    console: Console,
    client: MaestroAPIClient,
    issues: list[dict],
    orch: dict,
) -> None:
    running_ids = {r.get("issue_identifier") for r in orch.get("running", [])}
    candidates = [
        i for i in issues
        if i.get("identifier") not in running_ids
        and i.get("state", "").lower() not in ("done", "cancelled", "closed")
    ]
    if not candidates:
        console.print("[yellow]No dispatchable issues.[/yellow]")
        return

    choices = [_issue_choice_label(i) for i in candidates] + [BACK_LABEL]
    answer = questionary.select(
        "Select issue to run:", choices=choices, style=PROMPT_STYLE,
    ).ask()
    if not answer or answer == BACK_LABEL:
        return

    ref = answer.split()[0]
    issue = next((i for i in candidates if i["identifier"] == ref), None)
    if not issue:
        return

    try:
        client.trigger(issue["identifier"])
        console.print(f"[green]✓ Dispatched {ref}[/green]")
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")

    _pause()


def action_stop_worker(
    console: Console,
    client: MaestroAPIClient,
    orch: dict,
) -> None:
    """Stop a running worker."""
    running = orch.get("running", [])
    if not running:
        console.print("[yellow]No workers currently running.[/yellow]")
        _pause()
        return

    choices = []
    for w in running:
        ident = w.get("issue_identifier", "?")
        sid = (w.get("session_id") or "")[:8] or "—"
        turn = w.get("turn_count", 0)
        evt = (w.get("last_message") or w.get("last_event") or "")[:40]
        started = w.get("started_at")
        uptime = ""
        if started:
            try:
                dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                uptime = _elapsed((datetime.now(timezone.utc) - dt).total_seconds())
            except (ValueError, TypeError):
                pass
        choices.append(f"{ident}  turn={turn}  {uptime}  {evt}")
    choices.append(BACK_LABEL)

    answer = questionary.select(
        "Select worker to stop:", choices=choices, style=PROMPT_STYLE,
    ).ask()
    if not answer or answer == BACK_LABEL:
        return

    ref = answer.split()[0]

    confirm = questionary.confirm(
        f"Stop worker for {ref}? The agent subprocess will be killed.",
        default=False,
        style=PROMPT_STYLE,
    ).ask()
    if not confirm:
        return

    try:
        client.cancel_worker(ref)
        console.print(f"[red]✓ Cancel requested for {ref}[/red]")
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")

    _pause()


def action_move_issue(
    console: Console,
    client: MaestroAPIClient,
    issues: list[dict],
) -> None:
    if not issues:
        console.print("[yellow]No issues.[/yellow]")
        return

    choices = [_issue_choice_label(i, include_state=True) for i in issues] + [BACK_LABEL]
    answer = questionary.select(
        "Select issue:", choices=choices, style=PROMPT_STYLE,
    ).ask()
    if not answer or answer == BACK_LABEL:
        return

    ref = answer.split()[0]
    issue = next((i for i in issues if i["identifier"] == ref), None)
    if not issue:
        return

    targets = [s for s in MOVABLE_STATES if s != issue.get("state")] + [BACK_LABEL]
    new_state = questionary.select(
        f"Move {ref} to:", choices=targets, style=PROMPT_STYLE,
    ).ask()
    if not new_state or new_state == BACK_LABEL:
        return

    try:
        client.set_state(ref, new_state)
        console.print(f"[green]✓ {ref} → {new_state}[/green]")
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")

    _pause()


def action_issue_detail(
    console: Console,
    issues: list[dict],
    orch: dict,
) -> None:
    if not issues:
        console.print("[yellow]No issues.[/yellow]")
        return

    choices = [_issue_choice_label(i) for i in issues] + [BACK_LABEL]
    answer = questionary.select(
        "Select issue:", choices=choices, style=PROMPT_STYLE,
    ).ask()
    if not answer or answer == BACK_LABEL:
        return

    ref = answer.split()[0]
    issue = next((i for i in issues if i["identifier"] == ref), None)
    if not issue:
        return

    running_map = {r["issue_identifier"]: r for r in orch.get("running", []) if "issue_identifier" in r}
    retry_map = {r["issue_identifier"]: r for r in orch.get("retrying", []) if "issue_identifier" in r}
    exit_map = {r["issue_identifier"]: r for r in orch.get("recent_exits", []) if "issue_identifier" in r}
    worker = running_map.get(ref)
    retry = retry_map.get(ref)
    recent_exit = exit_map.get(ref)

    detail = Table.grid(padding=(0, 2))
    detail.add_column(style="dim", width=14)
    detail.add_column()
    detail.add_row("Identifier", f"[cyan bold]{issue['identifier']}[/cyan bold]")
    detail.add_row("Title", issue.get("title", ""))
    detail.add_row("State", f"{_state_icon(issue.get('state', ''))} {issue.get('state', '')}")
    detail.add_row("Priority", str(_fmt_priority(issue.get("priority"))))
    detail.add_row("Labels", ", ".join(issue.get("labels") or []) or "—")
    if issue.get("url"):
        detail.add_row("URL", f"[link={issue['url']}]{issue['url']}[/link]")

    desc = issue.get("description") or "(no description)"
    desc_panel = Panel(
        desc[:800],
        title="Description",
        border_style="bright_black",
        box=box.ROUNDED,
    )

    parts = [detail, desc_panel]

    if worker:
        w_tbl = Table.grid(padding=(0, 2))
        w_tbl.add_column(style="dim", width=14)
        w_tbl.add_column()
        w_tbl.add_row("Session", (worker.get("session_id") or "—")[:12])
        w_tbl.add_row("Turns", str(worker.get("turn_count", 0)))
        w_tbl.add_row("Last event", worker.get("last_event") or "—")
        w_tbl.add_row("Message", (worker.get("last_message") or "—")[:80])
        parts.append(Panel(w_tbl, title="[yellow]Active Worker[/yellow]", border_style="yellow", box=box.ROUNDED))

    if retry:
        r_tbl = Table.grid(padding=(0, 2))
        r_tbl.add_column(style="dim", width=14)
        r_tbl.add_column()
        r_tbl.add_row("Retry attempt", str(retry.get("attempt", "—")))
        r_tbl.add_row("Error", (retry.get("error") or "—")[:160])
        parts.append(Panel(r_tbl, title="[red]Retry Status[/red]", border_style="red", box=box.ROUNDED))

    if recent_exit and not worker:
        x_tbl = Table.grid(padding=(0, 2))
        x_tbl.add_column(style="dim", width=14)
        x_tbl.add_column()
        x_tbl.add_row("Result", recent_exit.get("reason", "—"))
        x_tbl.add_row("Turns", str(recent_exit.get("turn_count", 0)))
        x_tbl.add_row("Session", (recent_exit.get("session_id") or "—")[:12])
        x_tbl.add_row("Error", (recent_exit.get("error") or "—")[:160])
        parts.append(Panel(x_tbl, title="[bold]Recent Exit[/bold]", border_style="bright_black", box=box.ROUNDED))

    console.print(Panel(
        parts[0],
        title=f"[bold]{ref}[/bold]",
        subtitle="[dim]issue review snapshot[/dim]",
        border_style="cyan",
        box=box.DOUBLE_EDGE,
    ))
    if len(parts) > 1:
        console.print(Columns(parts[1:], equal=True, expand=True))

    _pause()


def action_e2e_test(
    console: Console,
    client: MaestroAPIClient,
    issues: list[dict],
) -> None:
    """E2E test gate for issues in Human Review state."""
    candidates = [
        i for i in issues
        if i.get("state", "").lower() == "human review"
    ]
    if not candidates:
        console.print("[yellow]No issues in Human Review awaiting E2E test.[/yellow]")
        _pause()
        return

    choices = [_issue_choice_label(i) for i in candidates] + [BACK_LABEL]
    answer = questionary.select(
        "Select issue to test:", choices=choices, style=PROMPT_STYLE,
    ).ask()
    if not answer or answer == BACK_LABEL:
        return

    ref = answer.split()[0]
    issue = next((i for i in candidates if i["identifier"] == ref), None)
    if not issue:
        return

    detail = Table.grid(padding=(0, 2))
    detail.add_column(style="dim", width=14)
    detail.add_column()
    detail.add_row("Identifier", f"[cyan bold]{issue['identifier']}[/cyan bold]")
    detail.add_row("Title", issue.get("title", ""))
    detail.add_row("State", f"{_state_icon('human review')} Human Review")
    detail.add_row("Next step", "Submit reviewed branch and advance workflow")
    if issue.get("url"):
        detail.add_row("Linear URL", f"[link={issue['url']}]{issue['url']}[/link]")

    console.print(Panel(
        detail,
        title=f"[bold]🧪 E2E Test — {ref}[/bold]",
        border_style="cyan",
        box=box.DOUBLE_EDGE,
    ))
    console.print()
    console.print("[dim]Run local end-to-end checks in the preserved workspace, then record the review result below.[/dim]")
    console.print("[dim]Pass will submit the branch, create or update the PR, then either wait for CI or merge automatically.[/dim]")
    console.print()

    verdict = questionary.select(
        "Test result:",
        choices=["✅ Pass — submit PR / merge", "❌ Fail — send back for fix", BACK_LABEL],
        style=PROMPT_STYLE,
    ).ask()

    if verdict is None or verdict == BACK_LABEL:
        return

    if "Pass" in verdict:
        try:
            result = client.submit_review(ref, "Manual end-to-end testing completed successfully.")
            pr_num = result.get("pr_number", "?")
            pr_url = result.get("html_url", "")
            branch = result.get("branch", "?")
            committed = result.get("committed", False)
            bootstrap_only = bool(result.get("bootstrap_only", False))
            bootstrap_reason = (result.get("bootstrap_reason") or "").strip()
            awaiting_ci = bool(result.get("awaiting_ci", False))
            target_state = result.get("state") or "Done"
            result_grid = Table.grid(padding=(0, 2))
            result_grid.add_column(style="dim", width=14)
            result_grid.add_column()
            result_grid.add_row("Issue", ref)
            result_grid.add_row("Branch", branch)
            result_grid.add_row("Commit", "created and pushed" if committed else "no new local code changes")
            if bootstrap_only:
                result_grid.add_row("PR", "skipped")
            else:
                result_grid.add_row("PR", f"#{pr_num}" + (f"  {pr_url}" if pr_url else ""))
            result_grid.add_row("Workflow", f"{target_state}" + (" (waiting for CI)" if awaiting_ci else ""))
            console.print(Panel(
                result_grid,
                title=f"[bold green]Submission Complete[/bold green]",
                border_style="green",
                box=box.ROUNDED,
            ))
            if bootstrap_only:
                message = bootstrap_reason or "Remote repository is still in bootstrap state; no PR was created for this submission."
                console.print(f"[yellow]• {message}[/yellow]")
            elif bootstrap_reason:
                console.print(f"[dim]{bootstrap_reason}[/dim]")
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/red]")
    else:
        fail_reason = questionary.text(
            "Describe what failed:",
            style=PROMPT_STYLE,
        ).ask()
        if fail_reason is None:
            return
        fail_reason = fail_reason.strip() or "E2E test failed (no details provided)"

        try:
            client.add_comment(
                ref,
                f"**E2E Test Failed** ❌\n\n{fail_reason}\n\n"
                f"Moving back to In Progress for automated fix.",
            )
            client.set_state(ref, "In Progress")
            console.print(f"[yellow]↩ {ref} → In Progress (E2E failed)[/yellow]")
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/red]")

    _pause()


def _pause() -> None:
    questionary.press_any_key_to_continue(style=PROMPT_STYLE).ask()


# ── Main Loop ───────────────────────────────────────────────────────────────

def run_tui(url: str = "http://127.0.0.1:8080") -> None:
    """Main entry point for the Maestro TUI workbench."""
    console = Console()
    client = MaestroAPIClient(url)

    issues: list[dict] = []
    orch: dict = {}

    try:
        while True:
            connected = client.healthy()
            if connected:
                try:
                    issues = client.issues()
                except Exception:
                    issues = []
                try:
                    orch = client.orchestrator()
                except Exception:
                    orch = {}
            else:
                issues = []
                orch = {}

            _clear()
            render_header(console, connected)

            if not connected:
                render_disconnected(console, client.base_url)
                action_list = ACTIONS_DISCONNECTED
            else:
                render_stats(console, orch, len(issues))
                render_state_summary(console, issues)
                render_issues(console, issues, orch)
                console.print(Columns(
                    [build_workers_panel(orch), build_recent_work_log_panel(orch)],
                    equal=True,
                    expand=True,
                ))
                render_footer(console, connected)
                action_list = ACTIONS_ALL

            console.print()
            choice = questionary.select(
                "▸",
                choices=action_list,
                style=PROMPT_STYLE,
                instruction="(↑↓ navigate, enter select)",
            ).ask()

            if choice is None or "Quit" in (choice or ""):
                break

            if "Refresh" in choice:
                continue

            if "Run issue" in choice:
                action_run_issue(console, client, issues, orch)
            elif "Stop worker" in choice:
                action_stop_worker(console, client, orch)
            elif "Move issue" in choice:
                action_move_issue(console, client, issues)
            elif "Issue detail" in choice:
                action_issue_detail(console, issues, orch)
            elif "E2E Test" in choice:
                action_e2e_test(console, client, issues)
            elif "Force poll" in choice:
                try:
                    client.refresh()
                    console.print("[green]✓ Immediate poll queued[/green]")
                except Exception as exc:
                    console.print(f"[red]Error: {exc}[/red]")
                _pause()

    except KeyboardInterrupt:
        pass
    finally:
        client.close()
        console.print("\n[dim]Maestro TUI closed.[/dim]")

"""Maestro Terminal Workbench — rich + questionary interactive console."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any

import questionary
from questionary import Style as QStyle
from rich import box
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


# ── Helpers ─────────────────────────────────────────────────────────────────

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


def _clear() -> None:
    os.system("clear" if sys.platform != "win32" else "cls")


# ── Renderers ───────────────────────────────────────────────────────────────

def render_header(console: Console, connected: bool) -> None:
    status = "[green]● connected[/green]" if connected else "[red]● disconnected[/red]"
    header = Text.from_markup(
        f"{LOGO}  [dim]v{__version__}[/dim]  {status}\n"
    )
    console.print(header)


def render_stats(console: Console, orch: dict[str, Any], issue_count: int) -> None:
    counts = orch.get("counts", {})
    totals = orch.get("totals", {})
    running = counts.get("running", 0)
    retrying = counts.get("retrying", 0)
    secs = totals.get("seconds_running", 0)

    grid = Table.grid(padding=(0, 3))
    grid.add_row(
        _stat_cell("ISSUES", str(issue_count), "cyan"),
        _stat_cell("RUNNING", str(running), "yellow" if running else "dim"),
        _stat_cell("RETRYING", str(retrying), "red" if retrying else "dim"),
        _stat_cell("AGENT TIME", _elapsed(secs), "green"),
    )
    console.print(
        Panel(grid, border_style="bright_black", box=box.HEAVY_EDGE, padding=(0, 1)),
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
    )
    tbl.add_column("ID", style="cyan", no_wrap=True, width=12)
    tbl.add_column("State", width=16)
    tbl.add_column("Pri", width=7)
    tbl.add_column("Title", ratio=1)
    tbl.add_column("Labels", style="magenta", width=18)
    tbl.add_column("Status", width=12, justify="right")

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
        title = issue.get("title", "")
        labels = ", ".join(issue.get("labels") or [])

        badge = ""
        if ident in running_ids:
            badge = "[yellow bold]⚡ running[/yellow bold]"
        elif ident in retry_ids:
            badge = "[red]↺ retry[/red]"

        tbl.add_row(ident, Text.from_markup(f"{icon} {state}"), pri, title, labels, Text.from_markup(badge))

    console.print(
        Panel(tbl, title="[bold]Issues[/bold]", border_style="bright_black", box=box.HEAVY_EDGE),
    )


def render_workers(console: Console, orch: dict) -> None:
    running = orch.get("running", [])
    retrying = orch.get("retrying", [])

    if not running and not retrying:
        console.print(
            Panel(
                "[dim]No active workers[/dim]",
                title="[bold]Workers[/bold]",
                border_style="bright_black",
                box=box.HEAVY_EDGE,
            ),
        )
        return

    tbl = Table(
        box=box.SIMPLE_HEAVY,
        border_style="bright_black",
        show_edge=False,
        pad_edge=False,
        expand=True,
    )
    tbl.add_column("Issue", style="cyan", no_wrap=True, width=12)
    tbl.add_column("Mode", width=10)
    tbl.add_column("Turn", width=8)
    tbl.add_column("Session", width=12)
    tbl.add_column("Event", ratio=1)
    tbl.add_column("Uptime", width=10, justify="right")

    for w in running:
        sid = (w.get("session_id") or "")[:8] or "—"
        turn = str(w.get("turn_count", "?"))
        evt = w.get("last_event") or ""
        msg = w.get("last_message") or ""
        display = msg[:60] if msg else evt

        started = w.get("started_at")
        uptime = ""
        if started:
            try:
                dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                uptime = _elapsed((datetime.now(timezone.utc) - dt).total_seconds())
            except (ValueError, TypeError):
                pass

        tbl.add_row(
            w.get("issue_identifier", "?"),
            "[yellow bold]⚡ run[/yellow bold]",
            turn,
            f"[dim]{sid}[/dim]",
            display,
            f"[green]{uptime}[/green]",
        )

    for r in retrying:
        tbl.add_row(
            r.get("issue_identifier", "?"),
            f"[red]↺ #{r.get('attempt', '?')}[/red]",
            "—",
            "—",
            (r.get("error") or "")[:60],
            "",
        )

    console.print(
        Panel(tbl, title="[bold]Workers[/bold]", border_style="bright_black", box=box.HEAVY_EDGE),
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

    choices = [f"{i['identifier']}  {i['title'][:50]}" for i in candidates]
    answer = questionary.select(
        "Select issue to run:", choices=choices, style=PROMPT_STYLE,
    ).ask()
    if not answer:
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


def action_move_issue(
    console: Console,
    client: MaestroAPIClient,
    issues: list[dict],
) -> None:
    if not issues:
        console.print("[yellow]No issues.[/yellow]")
        return

    choices = [f"{i['identifier']}  [{i['state']}]  {i['title'][:40]}" for i in issues]
    answer = questionary.select(
        "Select issue:", choices=choices, style=PROMPT_STYLE,
    ).ask()
    if not answer:
        return

    ref = answer.split()[0]
    issue = next((i for i in issues if i["identifier"] == ref), None)
    if not issue:
        return

    targets = [s for s in MOVABLE_STATES if s != issue.get("state")]
    new_state = questionary.select(
        f"Move {ref} to:", choices=targets, style=PROMPT_STYLE,
    ).ask()
    if not new_state:
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

    choices = [f"{i['identifier']}  {i['title'][:50]}" for i in issues]
    answer = questionary.select(
        "Select issue:", choices=choices, style=PROMPT_STYLE,
    ).ask()
    if not answer:
        return

    ref = answer.split()[0]
    issue = next((i for i in issues if i["identifier"] == ref), None)
    if not issue:
        return

    running_map = {r["issue_identifier"]: r for r in orch.get("running", []) if "issue_identifier" in r}
    worker = running_map.get(ref)

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

    console.print(Panel(
        parts[0],
        title=f"[bold]{ref}[/bold]",
        border_style="cyan",
        box=box.DOUBLE_EDGE,
    ))
    for p in parts[1:]:
        console.print(p)

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

    choices = [
        f"{i['identifier']}  {i['title'][:50]}"
        for i in candidates
    ]
    answer = questionary.select(
        "Select issue to test:", choices=choices, style=PROMPT_STYLE,
    ).ask()
    if not answer:
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
    if issue.get("url"):
        detail.add_row("Linear URL", f"[link={issue['url']}]{issue['url']}[/link]")

    console.print(Panel(
        detail,
        title=f"[bold]🧪 E2E Test — {ref}[/bold]",
        border_style="cyan",
        box=box.DOUBLE_EDGE,
    ))
    console.print()
    console.print("[dim]Run your local end-to-end tests, then report the result below.[/dim]")
    console.print()

    verdict = questionary.select(
        "Test result:",
        choices=["✅ Pass — move to Done", "❌ Fail — send back for fix"],
        style=PROMPT_STYLE,
    ).ask()

    if verdict is None:
        return

    if "Pass" in verdict:
        try:
            client.set_state(ref, "Done")
            client.add_comment(
                ref,
                "**E2E Test Passed** ✅\n\nManual end-to-end testing completed successfully. Moving to Done.",
            )
            console.print(f"[green]✓ {ref} → Done (E2E passed)[/green]")
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

            _clear()
            render_header(console, connected)

            if not connected:
                render_disconnected(console, client.base_url)
                action_list = ACTIONS_DISCONNECTED
            else:
                render_stats(console, orch, len(issues))
                render_issues(console, issues, orch)
                render_workers(console, orch)
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

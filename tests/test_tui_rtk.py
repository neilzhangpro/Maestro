from __future__ import annotations

from rich.console import Console

from maestro.tui.app import _fmt_token_count, render_stats


def test_fmt_token_count() -> None:
    assert _fmt_token_count(0) == "0"
    assert _fmt_token_count(950) == "950"
    assert _fmt_token_count(3200) == "3.2k"


def test_render_stats_shows_rtk_when_enabled() -> None:
    console = Console(record=True, width=120)
    render_stats(
        console,
        {
            "counts": {"running": 1, "retrying": 0},
            "totals": {"seconds_running": 12},
            "rtk": {"enabled": True, "estimated_tokens_saved": 3200},
        },
        issue_count=5,
    )
    output = console.export_text()
    assert "RTK SAVED" in output
    assert "3.2k" in output

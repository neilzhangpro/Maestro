# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Maestro is a Symphony-compatible coding agent orchestrator that turns Linear issues into AI-powered coding runs inside isolated Docker workspaces. It supports **Cursor ACP** and **Claude Code** as pluggable execution backends, with built-in RTK token-optimization for Bash-heavy workflows.

## Commands

### Docker (Recommended)

```bash
make workbench     # One-command startup: services + logs + TUI
make up            # Build and start all Docker services
make down          # Stop all services
make restart       # Rebuild and restart
make logs          # Tail all service logs
make tui-docker    # Launch TUI from inside the container
make build         # Rebuild images without starting
```

### Local Development

```bash
make install       # Create .venv and install dependencies
source .venv/bin/activate
make sandbox-dev   # Start opensandbox-server locally (separate terminal)
make dev           # Start Maestro locally
make tui           # Launch terminal workbench
```

### Testing

```bash
make test                          # Run all tests
.venv/bin/pytest tests/ -v         # Verbose output
.venv/bin/pytest tests/test_acp.py # Single test file
```

### CLI

```bash
maestro version                    # Print version
maestro start [WORKFLOW.md]        # Start service
maestro tui                        # Launch TUI
maestro workspace list             # List workspaces
maestro workspace rm <issue_id>    # Delete workspace
```

## Architecture

```
Linear Issues → Scheduler → Pipeline Engine → Agent Runner → Isolated Workspace
                    ↓              ↓                              ↓
              CI Watcher    parse → execute → update_linear    OpenSandbox Tests
                    ↓                                              ↓
              State Transitions ←─────────── Test Results ────────┘
```

### Key Components

| Module | Path | Purpose |
|--------|------|---------|
| Scheduler | `src/maestro/orchestrator/scheduler.py` | Main loop: polls Linear, dispatches workers, reconciles state |
| Worker | `src/maestro/worker/worker.py` | Per-issue multi-turn execution pipeline |
| Pipeline | `src/maestro/graph/graph.py` | Sequential: parse_issue → execute_task → update_linear |
| Headless Runner | `src/maestro/agent/headless.py` | Cursor ACP CLI launcher |
| Claude Code Runner | `src/maestro/agent/claude_code.py` | Claude Code CLI launcher |
| Linear Client | `src/maestro/linear/client.py` | GraphQL API for issue fetching/updating |
| GitHub Client | `src/maestro/github/client.py` | PR creation, CI status checks |
| Workspace Manager | `src/maestro/workspace/manager.py` | Per-issue directory lifecycle, hook execution |
| TUI | `src/maestro/tui/app.py` | Terminal workbench (rich + questionary) |
| Skill Evolution | `src/maestro/learning/` | RunRecord, FlowRecord, SkillStore, SkillAnalyser, SkillMutator |

### Data Flow

1. **Scheduler** picks up issues from Linear (filtered by team/assignee/state)
2. **Worker** runs the configured backend (Cursor ACP or Claude Code) through multi-turn execution
3. **Pipeline nodes** parse issue → execute agent → update Linear state
4. **Workspace hooks** (`after_create`, `before_run`, `after_run`) manage repo clone, rebase, and test execution
5. **CI Watcher** monitors GitHub Actions and auto-transitions issues based on CI results
6. **TUI** provides E2E test gate before marking issues Done

### Workspace Structure

Each issue gets an isolated workspace with:
- `.cursor/rules/` - Cursor rules (5 MDC files)
- `.cursor/skills/` - Shared skills (5 SKILL.md files)
- `.cursor/mcp.json` - MCP server config
- `.claude/mcp.json` - Mirrored for Claude Code
- `CLAUDE.md` - Aggregated rules for Claude Code
- `.maestro/run_history.jsonl` - Per-turn execution history
- `.maestro/flow_history.jsonl` - Tool-call chain capture

## Configuration

All behavior is driven by `WORKFLOW.md`:

- `backend: cursor | claude_code` - Switch execution backends
- `tracker:` - Linear API config, team/assignee filters, state mappings
- `cursor:` / `claude_code:` - Backend-specific settings (model, timeouts)
- `rtk:` - Token optimization for Claude Code Bash flows
- `agent:` - Concurrency, max turns, retry settings
- `github:` - PR creation, CI watcher configuration
- `hooks:` - Shell scripts for workspace lifecycle
- `evolution:` - Optional skill self-evolution (disabled by default)

Environment variables are set in `.env` (copy from `.env.example`):
- `LINEAR_API_KEY` - Required
- `CURSOR_API_KEY` - When using Cursor backend
- `ANTHROPIC_API_KEY` - When using Claude Code backend
- `GITHUB_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO` - For PR creation and CI

## Issue State Machine

```
Todo → In Progress → Draft PR → In Review → Human Review → Done
            ↑                        │             │
            └──── CI Fail (auto-fix) ◄─────────────┘
```

- **In Progress**: Agent actively working
- **In Review**: CI Watcher monitoring GitHub Actions
- **Human Review**: E2E test gate in TUI, workspace preserved
- Agent pauses on handoff states; workspace preserved for human inspection

## Code Conventions

- Python 3.11+, dependencies managed via `pyproject.toml`
- Entry point: `maestro.cli:app` (Typer CLI)
- FastAPI service on port 8080, WebSocket for real-time updates
- Tests in `tests/` using pytest with asyncio auto mode
- JSONL files for audit logs (run_history, flow_history, evolution_log)

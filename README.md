# Maestro

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/FastAPI-Backend-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Linear-Issue%20Ops-5E6AD2?style=for-the-badge&logo=linear&logoColor=white" alt="Linear" />
  <img src="https://img.shields.io/badge/Cursor-ACP%20Runner-111111?style=for-the-badge" alt="Cursor ACP" />
  <img src="https://img.shields.io/badge/Claude%20Code-Runner-D97706?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code" />
  <img src="https://img.shields.io/badge/Docker-Portable-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker" />
</p>

<p align="center">
  <strong>Harness engineering for autonomous software development.</strong>
</p>

<p align="center">
  Maestro turns Linear issues into AI-agent-powered coding runs inside isolated Docker workspaces,
  with orchestration, visibility, and human control built in.
  Supports <strong>Cursor ACP</strong> and <strong>Claude Code</strong> as pluggable execution backends.
</p>

---

## What Is Maestro?

Maestro is a Symphony-compatible coding agent orchestrator built to operationalize
AI software agents, not just run them once.

It connects the source of work, the execution environment, and the orchestration
layer into one repeatable system:

- `Linear` is the source of truth for work, filtered by team and assignee.
- `Cursor ACP` or `Claude Code` executes the agent run inside isolated Docker workspaces (configurable via `backend` in WORKFLOW.md).
- `Pipeline Engine` orchestrates parse → execute → update in a controlled sequence.
- `FastAPI + WebSocket` expose service state and realtime visibility.
- `TUI Workbench` provides a terminal-native interface for monitoring and control.

In short, Maestro is the harness around the agent.

## Why It Exists

Running an AI coding agent once is easy.

Running it repeatedly across real issues, with isolation, retries, workflow
control, and observability, is a different problem entirely.

Maestro is designed for that layer.

## Core Capabilities

- **Multi-backend agent execution** — switch between `Cursor ACP` and `Claude Code` via a single config field (`backend: cursor` or `backend: claude_code`)
- Filter Linear issues by **team and assignee** — manage only your own work in shared workspaces
- Turn `Linear` issues into executable coding runs with isolated per-issue Docker workspaces
- Execute agent sessions in a controlled multi-turn pipeline (up to 10 turns)
- **Dual-model strategy** — plan with one model, code with another (configurable `plan_model` + `model` for both backends)
- Run up to N concurrent agent tasks with automatic retry (max 3 retries with exponential backoff), stall detection, and 10-minute cooldown between runs
- **Automated workspace bootstrap** — `after_create` hook clones the repo; `before_run` hook auto-rebases onto latest `origin/main` every turn
- **Unified Skills & Rules** — one set of Skills (`.cursor/skills/`) and Rules (`.cursor/rules/`) shared by both backends; `after_create` hook auto-generates `CLAUDE.md` and `.claude/mcp.json` for Claude Code
- **RTK token-aware Bash flows** — when using `backend: claude_code`, Maestro installs `rtk`, configures its Claude hook, and records estimated token savings from Bash-heavy workflows
- Configure **5 MCPs** (Linear, Playwright, GitHub, GitNexus, Greptile) in every agent workspace — mirrored to both `.cursor/mcp.json` and `.claude/mcp.json`
- Run tests in an isolated **OpenSandbox Code Interpreter** after each turn and feed results back to the agent
- **Draft PR workflow** — PRs are created as drafts; only converted to ready for review after human E2E testing passes
- **CI Watcher** — monitors GitHub CI status for issues in `In Review` state; auto-transitions to `Human Review` on success or back to `In Progress` on failure for automated fix
- **E2E Test gate** — TUI provides a `🧪 E2E Test` panel for human end-to-end testing; on pass, converts draft PR to ready and marks issue Done
- Human-in-the-loop via `Human Review` handoff state — agent pauses, workspace preserved
- **Docker-only deployment** — agents run inside containers, avoiding local client sprawl
- Terminal workbench (`make tui`) with **← Back navigation** for real-time monitoring, issue management, and E2E testing
- **Skill self-evolution** — execution history is automatically analysed to patch existing Skills and crystallise recurring workflows into new Skills (see [Skill Evolution](#skill-evolution))

## TUI Workbench

<p align="center">
  <img src="docs/images/tui-workbench.png" alt="Maestro TUI Workbench" width="800" />
</p>

<p align="center"><em>Terminal workbench — real-time issue tracking, worker monitoring, and RTK token-savings visibility when enabled</em></p>

## Architecture

```mermaid
flowchart LR
    A[Linear Issues] --> B[Maestro Scheduler]
    B --> C[Pipeline Engine]
    C --> D{Backend?}
    D -->|cursor| E[Cursor ACP Runner]
    D -->|claude_code| F[Claude Code Runner]
    E --> G[Isolated Docker Workspace]
    F --> G
    B --> H[FastAPI Service]
    H --> I[TUI Workbench]
    G --> J[OpenSandbox Tests]
    J --> C
    B --> K[CI Watcher]
    K -->|CI pass| L[Done / Human Review]
    K -->|CI fail| B
    B --> M[Evolution Loop]
    M -->|analyse history| N[SkillAnalyser / FlowDistiller]
    N -->|generate via Runner| O[SkillMutator]
    O -->|patch / create| P[SkillStore]
    P -->|before_run sync| G
```

## Repository Layout

```text
.
├── src/maestro/           # Core service
│   ├── agent/             # Cursor headless runner, Claude Code runner, event normalization
│   ├── api/               # FastAPI routes (issues, runs, state, refresh)
│   ├── github/            # GitHub REST client (PR lookup, CI checks)
│   ├── learning/          # Skill evolution subsystem
│   │   ├── recorder.py        # RunRecord v2 — per-turn execution history (JSONL)
│   │   ├── flow_recorder.py   # FlowRecord — full tool-call chain capture per issue run
│   │   ├── skill_store.py     # SkillStore — read/write/patch/create SKILL.md files
│   │   ├── skill_analyser.py  # SkillAnalyser — failure & success pattern extraction
│   │   ├── flow_distiller.py  # FlowDistiller — N-gram workflow clustering
│   │   ├── skill_mutator.py   # SkillMutator — agent-driven Skill generation
│   │   └── evolution.py       # EvolutionLoop — orchestrates the full evolution cycle
│   ├── linear/            # Linear GraphQL client and models
│   ├── orchestrator/      # Scheduler, reconciler, retry, CI watcher, concurrency
│   ├── tui/               # Terminal workbench (rich + questionary)
│   ├── worker/            # Multi-turn worker per issue
│   └── workflow/          # WORKFLOW.md parser, config, template engine
├── docs/                  # Architecture notes
├── config/                # Runtime configuration
├── scripts/               # install-cursor-cli.sh, start-opensandbox.sh
├── WORKFLOW.md            # Prompt template, tracker config, hooks, agent instructions
├── Dockerfile             # Multi-stage build — cursor-agent installed at build time
├── docker-compose.yml     # Maestro + OpenSandbox services
├── Makefile               # One-command developer experience
└── tests/                 # Test suite (including Claude Code integration tests)
```

## Quick Start

> **Important:** Maestro is designed to run via Docker. Running locally (`make dev`)
> is **not recommended** as it causes the host OS to continuously spawn agent client
> windows (Cursor GUI instances), which can crash your system.

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env — fill in your keys (see Environment Variables below)

# 2. Choose your backend in WORKFLOW.md
#    backend: cursor       — uses Cursor ACP (requires CURSOR_API_KEY)
#    backend: claude_code  — uses Claude Code CLI (requires ANTHROPIC_API_KEY)

# 3. One-command workbench
make workbench

# Alternative manual flow:
# 3a. Build and start services
make up

# 3b. Open the TUI workbench in another terminal
make tui-docker

# 3c. View logs
make logs
```

The Docker build automatically downloads the agent CLI (Cursor or Claude Code)
and installs `rtk` at build time. No host-side installation required.

`make workbench` prefers `tmux` when available and opens three panes: service startup, `docker compose logs -f`, and the TUI running inside the `maestro` container. On macOS without `tmux`, it falls back to opening Terminal.app tabs automatically.

## Configuration

All behaviour is driven by `WORKFLOW.md`. Key settings:

```yaml
backend: cursor                        # "cursor" or "claude_code"

tracker:
  kind: linear
  api_key: $LINEAR_API_KEY
  team_id: "your-linear-team-id"
  assignee: "me"                       # only process issues assigned to you
  active_states: [Todo, In Progress]
  handoff_states: [Human Review, In Review]

cursor:                                # Cursor ACP backend settings
  model: sonnet-4.6                    # model for coding turns
  plan_model: opus-4.6                 # model for planning turn (turn 1)

claude_code:                           # Claude Code backend settings
  command: claude
  model: claude-sonnet-4-20250514
  api_key: $ANTHROPIC_API_KEY
  skip_permissions: true               # skip tool permission prompts (required for automation)
  max_turns_per_invocation: 0          # 0 = unlimited
  max_budget_usd: 0                    # 0 = unlimited; set a positive value to cap spend

rtk:                                   # RTK token-reduction settings (Claude Code only)
  enabled: true
  mode: hook
  binary: rtk

agent:
  auto_dispatch: true                  # true for Docker production; false for TUI-only manual runs
  max_concurrent_agents: 2
  max_turns: 10

github:
  token: $GITHUB_TOKEN
  owner: $GITHUB_OWNER                # set in .env — no hardcoded repo references
  repo: $GITHUB_REPO                  # set in .env — switch projects by changing .env only
  ci_watch_states: [In Review]         # monitor CI for issues in these states
  ci_pass_target_state: Human Review   # where to move on CI pass
  ci_fail_target_state: In Progress    # where to move on CI fail (triggers re-fix)
```

**Switching backends:** Change `backend` to `cursor` or `claude_code`. Each backend reads its own config section. The `after_create` hook auto-generates both `.cursor/` and `.claude/` configurations from a single source — no backend-specific hook overrides needed.

**RTK integration:** When `rtk.enabled: true` and `backend: claude_code`, Maestro configures RTK's Claude hook in the agent environment and appends RTK usage guidance to `CLAUDE.md`. This only affects Bash-driven workflows; Claude built-in `Read`, `Grep`, and `Glob` do not flow through RTK.

**Skill evolution:** Enable the optional `evolution` block (see [Skill Evolution](#skill-evolution) for full details):

```yaml
evolution:
  enabled: true        # disabled by default
  auto_apply: false    # new Skills land in pending_skills/ for review before applying
```

**Switching projects:** Change `GITHUB_OWNER`, `GITHUB_REPO`, and `GITHUB_TOKEN` in `.env`. All Skills, hooks, and CI monitoring automatically use these values — no hardcoded repo references in WORKFLOW.md.

## RTK Metrics

When RTK is enabled, Maestro records the latest `rtk gain --all --format json` snapshot after each Claude Code turn and exposes a best-effort `estimated_tokens_saved` metric:

- `GET /api/v1/state` includes an `rtk` object with `enabled`, `mode`, `binary`, `estimated_tokens_saved`, and `last_snapshot_at`
- `GET /api/v1/orchestrator` includes the same `rtk` object alongside the combined dashboard payload
- `make tui` shows an `RTK SAVED` stats card only when `rtk.enabled` is true

This metric is derived from RTK's own JSON output and is intended as an operational estimate, not billing-grade accounting.

## Makefile Targets

| Target | Description |
|--------|-------------|
| `make up` | Build and start all Docker services |
| `make workbench` | One-command startup for services, logs, and TUI |
| `make down` | Stop all services |
| `make restart` | Rebuild and restart |
| `make logs` | Tail all service logs |
| `make tui` | Launch terminal workbench |
| `make tui-docker` | Launch terminal workbench from inside the running container |
| `make test` | Run unit tests |
| `make clean` | Remove containers, volumes, and caches |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LINEAR_API_KEY` | Yes | Linear personal API key |
| `CURSOR_API_KEY` | When `backend: cursor` | Cursor API key for agent authentication |
| `ANTHROPIC_API_KEY` | When `backend: claude_code` | Anthropic API key for Claude Code |
| `GITHUB_TOKEN` | Recommended | For GitHub MCP, PR creation, and CI Watcher |
| `GITHUB_OWNER` | Recommended | GitHub organization or user name (used in Skills and hooks) |
| `GITHUB_REPO` | Recommended | GitHub repository name (used in Skills and hooks) |
| `GREPTILE_API_KEY` | Optional | For Greptile code-search MCP |
| `SANDBOX_DOMAIN` | Optional | OpenSandbox server URL (set automatically in Docker) |
| `SANDBOX_API_KEY` | Optional | OpenSandbox authentication key |
| `MAESTRO_WORKSPACE_ROOT` | Optional | Override workspace root directory (default: `~/maestro_workspaces`) |

## Workflow Lifecycle

```text
Linear Todo ──► In Progress ──► Draft PR ──► In Review ──► Human Review ──► Done
                     ▲                            │              │
                     │                            ▼              ▼
                     └──── CI Fail (auto-fix) ◄─ CI Watcher    E2E Test
                                                                 │  │
                                                           Pass ─┘  └─ Fail
                                                    PR → Ready       ↓
                                                    Issue → Done   In Progress
```

1. **Scheduler** picks up active issues from Linear (when `auto_dispatch: true`) or waits for manual trigger via TUI
2. **Worker** runs the configured agent backend (Cursor ACP or Claude Code) through multi-turn execution (plan → code → test → PR)
3. Agent creates a **draft PR** and moves the issue to **In Review** — the PR stays in draft throughout CI and review
4. **CI Watcher** monitors GitHub CI; on success, transitions to **Human Review**; on failure, moves back to **In Progress** for automated fix
5. **TUI E2E Test** provides a manual quality gate in `Human Review`:
   - **Pass**: converts the draft PR to **ready for review**, then moves the issue to **Done**
   - **Fail**: records failure details, moves back to **In Progress** for the agent to fix

## Human-in-the-Loop

When the agent cannot proceed autonomously (e.g. ambiguous requirements, failing tests),
it moves the Linear issue to **Human Review** state. Maestro:

1. Detects the state change and stops the worker
2. Preserves the workspace for human inspection
3. Does not reschedule until the issue moves back to an active state

The TUI provides an **E2E Test** action for issues in Human Review:
- **Pass**: converts the draft PR to ready for review, marks the issue as Done, and adds a success comment on Linear
- **Fail**: records the failure reason, adds a comment to Linear, and moves the issue back to In Progress for the agent to automatically fix

## Skill Evolution

Maestro includes an opt-in system that automatically improves existing Skills and crystallises recurring agent workflows into new Skills — all driven by the same agent backend already configured in `WORKFLOW.md` (no extra API key required).

### How It Works

**Data collection** (runs with every issue, zero configuration needed):

- `RunRecord v2` — every turn records the ordered tool-call chain, files changed, Skills referenced, and Linear labels alongside the existing success/error fields.
- `FlowRecord` — after each issue run completes, the full tool-call sequence is appended to `.maestro/flow_history.jsonl`.

**Evolution cycle** (triggered from `Scheduler._on_tick` when no agents are running):

| Step | Component | What It Does |
|------|-----------|--------------|
| A | `SkillAnalyser` | Groups history by `skill_refs`; finds recurring failure errors and common success tool sequences not yet covered by the Skill's learned section |
| B | `SkillMutator` | Writes context files to an *evolution workspace*, runs a meta-prompt through the configured Runner (Cursor or Claude Code), reads the output |
| C | `SkillStore` | Appends the generated addendum below the `<!-- LEARNED -->` sentinel in the target SKILL.md |
| D | `FlowDistiller` | N-gram clusters `flow_history.jsonl` to find tool sub-sequences that appear ≥ N times across successful runs |
| E | `SkillMutator` | Same evolution-workspace flow as above, but generates a complete new SKILL.md |
| F | `SkillStore` | Writes the new Skill to `evolved_skills/` (or `pending_skills/` if `auto_apply: false`) |

Evolved Skills are synced into every agent workspace via the `before_run` hook.

### Configuration

Add (or uncomment) the `evolution` block in `WORKFLOW.md`:

```yaml
evolution:
  enabled: true
  min_runs_between: 10         # wait until N successful runs have occurred since last cycle
  min_interval_minutes: 60     # also wait at least this many minutes
  max_addendum_tokens: 500     # soft cap on addendum length (guideline to the agent)
  max_new_skills_per_cycle: 2  # max new Skills created per cycle
  min_pattern_occurrences: 3   # a flow pattern must appear this many times to qualify
  auto_apply: false            # false → new Skills land in pending_skills/ for human review
```

Evolution is **disabled by default** (`enabled: false`). No separate LLM API key is required — the mutator reuses the same Cursor or Claude Code runner already configured in WORKFLOW.md.

### Safety

| Mechanism | Implementation |
|-----------|---------------|
| Original body never touched | Addenda are written only after the `<!-- LEARNED -->` sentinel |
| Opt-out per Skill | Add `<!-- NO_AUTO_MUTATE -->` anywhere in a SKILL.md to exclude it |
| Human review mode | `auto_apply: false` puts new Skills in `pending_skills/` for manual promotion |
| Rollback | Every mutation creates a `.bak` backup before writing |
| Frequency limit | Max 3 addenda + 2 new Skills per cycle |
| No executable code | Meta-prompts explicitly prohibit shell scripts or runnable code blocks |
| Audit log | Every cycle appends to `.maestro/evolution_log.jsonl` |

### Data Layout

```text
{workspace_root}/.maestro/
├── run_history.jsonl       # RunRecord v2 — per-turn execution history
├── flow_history.jsonl      # FlowRecord — full tool-call chains
├── evolution_log.jsonl     # Audit log of every evolution cycle
├── evolved_skills/         # Applied evolved Skills (synced to workspaces on before_run)
│   └── <name>/SKILL.md
├── pending_skills/         # Candidate Skills awaiting human review
│   └── <name>/SKILL.md
└── evolution_workspace/    # Temporary workspace used by SkillMutator (wiped per run)
```

---

## Philosophy

The value of an AI coding agent does not come only from the model.
It comes from the system that informs it, constrains it, monitors it, and
turns it into a reliable part of software delivery.

That system is the harness.

## Roadmap

### Multi-Runner Architecture

Maestro supports pluggable agent execution backends, switchable via `backend` in WORKFLOW.md:

| Runner | Status | Description |
|--------|--------|-------------|
| **Cursor ACP** | Stable | Default backend. Headless CLI with `stream-json` output, multi-turn sessions, MCP support. |
| **Claude Code** | Stable | Anthropic's CLI agent (`claude -p --output-format stream-json`). Native tool use, session resume, budget controls. |
| **Codex CLI** | Planned | OpenAI's open-source CLI agent (`codex --full-auto`). Runs locally with sandboxed execution. Will require adapter for its distinct event format and approval model. |

### Other Planned Enhancements

- **Webhook-driven CI** — Replace polling-based CI Watcher with GitHub webhook receiver for instant state transitions
- **Workspace snapshots** — Persist workspace state between runs for faster resumption
- **Multi-repo support** — Handle issues that span multiple repositories
- **Metrics & analytics** — Agent success rate, time-to-completion, cost tracking dashboard
- **Team collaboration** — Multi-user TUI with role-based access and shared visibility

## Status

**v0.8.0** — RTK token-savings integration + runtime/TUI visibility.

**Changelog (v0.8.0):**
- **RTK integration for Claude Code** — Docker image now installs `rtk`; `after_create` configures the Claude hook and appends RTK usage guidance to `CLAUDE.md`
- **RTK metrics in execution history** — `RunRecord` now stores the latest RTK gain snapshot per turn when available
- **API visibility** — `/api/v1/state` and `/api/v1/orchestrator` now expose RTK enablement and estimated token savings
- **TUI visibility** — stats panel now shows `RTK SAVED` when the feature is enabled in `WORKFLOW.md`

**v0.7.0** — Skill self-evolution system + Cursor auth token auto-refresh.

**Changelog (v0.7.0):**
- **Skill self-evolution** — new `src/maestro/learning/` subsystem:
  - `RunRecord v2` — extended with `tool_sequence`, `files_changed`, `skill_refs`, `labels` fields (backward-compatible)
  - `FlowRecorder` — captures the full ordered tool-call chain per issue run to `flow_history.jsonl`
  - `SkillStore` — manages `evolved_skills/` and `pending_skills/` directories with `<!-- LEARNED -->` sentinel patching and `.bak` rollback
  - `SkillAnalyser` — extracts recurring failure patterns and common success tool N-grams from `run_history.jsonl`
  - `FlowDistiller` — N-gram clustering of `flow_history.jsonl` to discover new-Skill candidates; prunes redundant sub-patterns
  - `SkillMutator` — uses the *evolution workspace* pattern to drive the already-configured Runner (Cursor or Claude Code) via meta-prompts; writes output to files in `output/`, reads result back — no extra API key required
  - `EvolutionLoop` — triggered from `Scheduler._on_tick` only when no agents are running; respects `min_runs_between` and `min_interval_minutes` thresholds; logs every mutation to `evolution_log.jsonl`
  - `EvolutionConfig` — new `evolution:` section in `WORKFLOW.md` with `enabled`, `auto_apply`, cycle limits, and occurrence thresholds
  - `before_run` hook — syncs `evolved_skills/` into `.cursor/skills/` before every agent run
- **Cursor auth token auto-refresh** — `HeadlessRunner` now detects expired-token errors ("authentication is invalid", "please log in") and automatically re-exchanges `CURSOR_API_KEY` for a fresh token before retrying the turn; eliminates the need for container restarts due to stale cached tokens

**Changelog (v0.6.0):**
- **Unified Skills & Rules** — single set of `.cursor/skills/` and `.cursor/rules/` used by both Cursor ACP and Claude Code; `after_create` hook auto-generates `CLAUDE.md` (aggregated rules) and `.claude/mcp.json` (mirrored MCP config)
- **Unified prompt template** — removed backend-specific `{% if %}` conditionals; all instructions now use "Read and follow `.cursor/skills/X/SKILL.md`" which works for both backends
- **Externalized repo config** — `GITHUB_OWNER` and `GITHUB_REPO` environment variables replace all hardcoded repository references in WORKFLOW.md; switch projects by editing `.env` only
- **Dynamic stall detection** — reconciler reads `stall_timeout_ms` from the active backend's config instead of hardcoding Cursor's value
- Updated `.env.example` with `GITHUB_OWNER`, `GITHUB_REPO`, and `MAESTRO_WORKSPACE_ROOT`

**Changelog (v0.5.0):**
- Add **Claude Code** as a pluggable execution backend (`backend: claude_code` in WORKFLOW.md)
- `ClaudeCodeRunner` with full feature parity: NDJSON streaming, session resume, stall/turn timeout, cancel support
- Claude Code config: `model`, `plan_model`, `skip_permissions`, `max_turns_per_invocation`, `max_budget_usd`, `append_system_prompt`, `allowed_tools`
- Backend-specific hook overrides (`claude_code_after_create`, etc.)
- Event normalization handles Claude Code's multi-block `assistant` messages and `completion` result subtype
- 28 integration tests covering config parsing, event normalization, command construction, and dispatch validation
- **Docker-only deployment** — local `make dev` removed from recommended workflow to prevent client window sprawl

**Changelog (v0.4.0):**
- Add `auto_dispatch` config (default `false`) — prevents uncontrolled agent spawning
- Add 10-minute cooldown after normal worker completion to prevent re-dispatch loops
- Cap abnormal retries at 3 with exponential backoff (10s → 20s → 40s)
- Add `← Back` navigation to all TUI sub-menus
- Configure Noval-X Linear workspace with `Backlog` state support

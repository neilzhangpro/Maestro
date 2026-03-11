# Maestro Architecture

> Design document reflecting the current implementation. Based on Symphony SPEC,
> Cursor ACP, and Linear GraphQL API.

---

## 1. System Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        TUI Workbench (rich + questionary)                │
│   Issue list │ Worker status │ Manual trigger │ State management         │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │ HTTP / WebSocket
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        Maestro Service (FastAPI)                          │
│  /api/issues  │  /api/runs  │  /api/v1/orchestrator  │  /api/v1/refresh  │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │   Scheduler (Reconciler)      │
                    │  • Polls Linear every 30s     │
                    │  • Filters: team + assignee   │
                    │  • Max 2 concurrent workers   │
                    │  • Dispatches new issues      │
                    │  • Handles handoff states     │
                    └──────────────┬───────────────┘
                                   │ spawn
                    ┌──────────────▼──────────────┐
                    │         Worker               │
                    │  Pipeline: parse → execute   │
                    │           → update_linear    │
                    │  • Up to 10 turns per issue  │
                    │  • Retry with backoff        │
                    │  • Stall detection           │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │   Headless Agent Runner      │
                    │  cursor-agent -p --yolo      │
                    │  NDJSON stream processing    │
                    │  User-input detection        │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │   Isolated Workspace         │
                    │  {workspace_root}/{issue-id} │
                    │  .cursor/rules/  (4 rules)   │
                    │  .cursor/skills/ (5 skills)  │
                    │  .cursor/mcp.json (5 MCPs)   │
                    └─────────────────────────────┘
```

---

## 2. Component Reference

### 2.1 Scheduler (`orchestrator/scheduler.py`)

Runs a polling loop against Linear GraphQL API every `polling.interval_ms` (default 30s).

**Issue filtering:**
- `tracker.team_id` — restrict to a specific Linear team
- `tracker.assignee` — `"me"` resolves to the API key owner via `isMe: {eq: true}`
- `tracker.active_states` — only `Todo` and `In Progress` by default

**Reconciliation logic:**
1. Fetch active issues matching filters
2. For each issue not already running: dispatch if `max_concurrent_agents` not reached
3. For each running issue: check if it moved to `handoff_states` or `terminal_states`
4. If handoff detected: stop worker, preserve workspace for human review
5. If terminal: stop worker, clean up workspace

### 2.2 Pipeline Engine (`graph/graph.py`)

Lightweight sequential pipeline replacing LangGraph:

```
parse_issue → execute_task → update_linear
```

On error at any node the pipeline stops with `status=failed`. The worker retries
the whole pipeline on the next turn up to `agent.max_turns`.

### 2.3 Headless Agent Runner (`agent/headless.py`)

Launches `cursor-agent` in headless mode:

```bash
cursor-agent -p "<prompt>" --yolo --output-format stream-json
```

Processes the NDJSON event stream:
- Detects `requestUserInput` events → raises `UserInputRequired`, triggers handoff
- Detects process exit with error → marks turn as failed
- Streams tool calls and responses back to the worker

### 2.4 Workspace Hooks (`workspace/hooks.py`)

Three hooks execute shell scripts in the workspace directory:

| Hook | When | Current Content |
|------|------|-----------------|
| `after_create` | Workspace first created | Injects 4 Cursor rules, 5 Skills, `mcp.json` |
| `before_run` | Before each agent turn | (empty) |
| `after_run` | After each agent turn | Runs tests in OpenSandbox or local pytest; writes `sandbox-test-results.txt` |
| `before_remove` | Before workspace deletion | (empty) |

### 2.5 Cursor Rules (injected via `after_create`)

| File | Purpose |
|------|---------|
| `.cursor/rules/english-only.mdc` | All output and comments in English |
| `.cursor/rules/ruff-best-practices.mdc` | Ruff linter and formatter conventions |
| `.cursor/rules/python-code-style.mdc` | Python style: type hints, naming, structure |
| `.cursor/rules/testing-conventions.mdc` | pytest conventions, no mocks of internals |

### 2.6 Project Skills (injected via `after_create`)

| Skill | Purpose |
|-------|---------|
| `git-branch-sync` | Branch management, sync with main, pre-PR checks |
| `pr-create-describe` | Commit → push → create GitHub PR with auto description |
| `ci-monitor-fix` | Monitor CI status post-PR, retrieve logs, fix issues |
| `linear-agentic-issues` | Standardize Linear issue titles and labels |
| `linear-update-on-pr` | Update Linear state and add PR link after CI passes |

### 2.7 MCP Configuration (injected via `after_create`)

```json
{
  "mcpServers": {
    "linear":    { "command": "npx", "args": ["-y", "@linear/mcp-server"] },
    "playwright":{ "command": "npx", "args": ["-y", "@playwright/mcp@latest"] },
    "github":    { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"] },
    "gitnexus":  { "command": "npx", "args": ["-y", "gitnexus@latest", "mcp"] },
    "greptile":  { "type": "http", "url": "https://api.greptile.com/mcp" }
  }
}
```

---

## 3. Docker Deployment

### 3.1 Build

The Dockerfile downloads the official Cursor agent CLI tarball at build time:

```
https://downloads.cursor.com/lab/{version}/{os}/{arch}/agent-cli-package.tar.gz
```

The tarball contains Node.js runtime + bundled JS modules, making the image
fully self-contained. No host Cursor installation needed.

```bash
# Build with default version
docker compose build

# Build with a specific cursor-agent version
docker compose build --build-arg CURSOR_AGENT_VERSION=2026.02.27-e7d2ef6
```

### 3.2 Services

```yaml
services:
  maestro:    # port 8080 — API + orchestration + agent runner
  opensandbox: # port 8899 — isolated Python test execution
```

`maestro` depends on `opensandbox` being healthy. After startup, the API
binds to `0.0.0.0:8080` (configurable via `MAESTRO_HTTP_HOST`).

### 3.3 Authentication

The `docker-entrypoint.sh` exchanges `CURSOR_API_KEY` for a short-lived
`CURSOR_AUTH_TOKEN` before starting Maestro, bypassing macOS Keychain in the
Linux container.

---

## 4. Human-in-the-Loop

Aligned with Symphony SPEC's handoff philosophy:

1. **Agent guidance**: The agent prompt instructs it to move issues to `Human Review`
   when it cannot proceed autonomously.
2. **Hard failure on `requestUserInput`**: If the agent tries to prompt the human
   directly via the interactive prompt, the runner kills the process and marks
   the turn as failed.
3. **Handoff state detection**: Scheduler detects `Human Review` state → stops
   worker → preserves workspace.
4. **Test gate**: The `after_run` hook writes `SANDBOX_TEST_FAILED` marker if tests
   fail. The agent checks this file and does not move to `Human Review` until
   tests pass.

---

## 5. Configuration Reference (`WORKFLOW.md`)

```yaml
tracker:
  kind: linear
  api_key: $LINEAR_API_KEY
  team_id: "<linear-team-uuid>"       # restrict to one team
  assignee: "me"                      # only process your issues
  active_states: [Todo, In Progress]
  terminal_states: [Done, Cancelled, Closed]
  handoff_states: [Human Review]

polling:
  interval_ms: 30000

workspace:
  root: $MAESTRO_WORKSPACE_ROOT       # /data/workspaces in Docker

agent:
  max_concurrent_agents: 2
  max_turns: 10

hooks:
  after_create: |   # injects rules, skills, mcp.json
  after_run: |      # runs tests in OpenSandbox or local pytest
```

---

## 6. Alignment with Symphony SPEC

| Dimension | Symphony | Maestro |
|-----------|----------|---------|
| Trigger | Poll Linear active states | Poll Linear with team + assignee filter |
| Execution | Codex app-server (JSON-RPC stdio) | Cursor ACP (headless `-p` mode) |
| Workflow definition | `WORKFLOW.md` YAML + prompt | `WORKFLOW.md` YAML + prompt + hooks |
| Human handoff | `handoff_states` config | `handoff_states` + hard-fail on `requestUserInput` |
| Workspace isolation | Per-issue directory | Per-issue directory + Skills + MCPs injected |
| Retry | Exponential backoff | Exponential backoff + stall detection |
| Concurrency | Configurable | Max 2 by default (local resource constraint) |
| Deployment | Any | Docker (self-contained with cursor-agent) |

---

## 7. Reference

- [Symphony SPEC](https://github.com/openai/symphony/blob/main/SPEC.md)
- [Cursor Headless CLI Docs](https://cursor.com/docs/cli/headless)
- [Cursor CLI Install](https://cursor.com/install)
- [Linear GraphQL API](https://developers.linear.app/docs/graphql/working-with-the-graphql-api)
- [Agent Client Protocol](https://agentclientprotocol.com/)

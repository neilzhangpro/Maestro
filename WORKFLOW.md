---
tracker:
  kind: linear
  api_key: $LINEAR_API_KEY
  project_slug: ""
  active_states: [Todo, In Progress]
  terminal_states: [Done, Cancelled, Closed]
  handoff_states: [Human Review]

polling:
  interval_ms: 30000

workspace:
  root: $MAESTRO_WORKSPACE_ROOT

hooks:
  after_create: ""
  before_run: ""
  after_run: ""
  before_remove: ""
  timeout_ms: 60000

cursor:
  command: agent
  model: ""
  sandbox: disabled
  force: true
  trust: true
  approve_mcps: true
  turn_timeout_ms: 3600000
  stall_timeout_ms: 300000

agent:
  max_concurrent_agents: 2
  max_turns: 10
  max_retry_backoff_ms: 300000
  max_concurrent_agents_by_state: {}

server:
  port: 8080
---

You are working on issue **{{ issue.identifier }}: {{ issue.title }}**.

## Issue Details
- **Priority**: {{ issue.priority | default: "unset" }}
- **State**: {{ issue.state }}
- **Labels**: {{ issue.labels | join: ", " | default: "none" }}
{% if issue.url %}- **URL**: {{ issue.url }}{% endif %}

## Description
{{ issue.description | default: "(no description)" }}

{% if issue.blocked_by.size > 0 %}
## Blocked By
{% for blocker in issue.blocked_by %}- {{ blocker.identifier }}: {{ blocker.title }} ({{ blocker.state }})
{% endfor %}{% endif %}

## Instructions
1. Read the codebase and understand the project structure.
2. Implement the changes described above.
3. Write or update tests for your changes.
4. Ensure all existing tests still pass.
5. When finished, update the Linear issue state to **Human Review** so a human can verify your work.

## Decision Policy
- If you encounter a problem that requires human judgment (e.g., architectural decisions, ambiguous requirements, security-sensitive changes), **stop working and update the issue state to Human Review** with a comment explaining what decision is needed.
- Do NOT ask for user input. If you cannot proceed autonomously, hand off to a human.

{% if attempt %}
## Retry Context
This is retry attempt #{{ attempt }}. Review your prior work in this workspace and fix any issues found during the previous run.
{% endif %}

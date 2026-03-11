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
  after_create: |
    mkdir -p .cursor/rules
    cat > .cursor/rules/english-only.mdc << 'RULE_EOF'
    ---
    description: All project output must be in English — docs, comments, commit messages, logs, UI copy
    alwaysApply: true
    ---

    # English-Only Output

    All human-readable output produced in this repository **must be in English**. This applies to both AI-generated and human-written content when contributing to the codebase.

    ## In Scope

    | Type | Requirement | Examples |
    |------|--------------|---------|
    | **Code comments** | English only | `# Validate tenant context before delegation` not `# 校验租户上下文` |
    | **Docstrings** | English only | Module, class, and function docstrings (Google or NumPy style) |
    | **Documentation** | English only | README, ARCHITECTURE.md, docs/, .md files, ADRs |
    | **Commit messages** | English only | `feat(agentic): add project_id to session schema` |
    | **Git branch names** | English only | `feat/agentic-project-entity-architecture` |
    | **Log messages** | English only | `logger.info("Session created for org=%s", org_id)` |
    | **User-facing strings** | English only | API error messages, CLI output, UI copy (unless i18n explicitly uses another locale) |
    | **Cursor rules / skills** | English only | Descriptions and body text in `.cursor/rules/*.mdc` and `.cursor/skills/**/SKILL.md` |

    ## Exceptions

    - **Local-only or personal notes** (e.g. scratch files, private TODOs not committed) may use any language.
    - **Secrets, env values, and opaque identifiers** are not "output" and are not constrained by this rule.
    - **Explicit i18n** where the product intentionally supports multiple languages: source/default locale is still English; other locales live in i18n assets.

    ## Rationale

    - Keeps the codebase consistent and readable for a global team and tooling.
    - Aligns with common OSS and enterprise conventions (English as default for technical content).
    - Reduces ambiguity in code review, search, and documentation.

    When in doubt, write it in English.
    RULE_EOF
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

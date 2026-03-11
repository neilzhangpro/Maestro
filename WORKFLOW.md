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
    cat > .cursor/rules/ruff-best-practices.mdc << 'RULE_EOF'
    ---
    description: Ruff code quality rules — refactor-first principle, common fix strategies
    globs: **/*.py
    alwaysApply: false
    ---

    # Python Linting — Ruff Best Practices

    > Core Principle: "When Ruff reports violations, ALWAYS fix the code first. Only add ignores when following established best practices."

    ## Ruff Config

    - Target: Python 3.14, line-length 120
    - Rules: E, F, W, I (isort)
    - Known first-party: `novie_agentic`, `novie_core`

    ## Process When Linter Complains

    1. Read the error — understand what rule is violated and why
    2. Refactor first — can you fix the code to eliminate the violation?
    3. Research best practice — is there an established pattern?
    4. Only add ignore if justified — document WHY, use per-file ignore
    5. NEVER use `--no-verify` to bypass checks

    ## Common Rules and Fix Strategies

    | Rule    | Violation                | Fix Strategy                                    |
    |---------|--------------------------|------------------------------------------------|
    | PLR0913 | Too many arguments (>5)  | Use Parameter Object Pattern (frozen dataclass) |
    | PLW0603 | Global statement usage   | Refactor to DI or separate module               |
    | PLC0415 | Import outside top-level | Move to module level or separate module pattern |
    | E501    | Line too long            | Break into multiple lines                       |
    | RET505  | Unnecessary elif         | Use early returns                               |

    ## Parameter Object Pattern (PLR0913)

    ```python
    # ✅ CORRECT — group related parameters
    @dataclass(frozen=True)
    class ModelConfig:
        model_key: str
        api_key: str
        temperature: float
        provider_config: dict[str, Any]

    def create_model(config: ModelConfig) -> ChatModel: ...

    # ❌ WRONG — too many arguments
    def create_model(model_key, api_key, temperature, provider_config, ...): ...
    ```

    ## Separate Module Pattern (PLC0415)

    For optional dependencies, isolate imports in a dedicated module:

    ```python
    # ✅ CORRECT — _alibaba.py (isolated module)
    def create_alibaba_model(config: ModelConfig) -> Any:
        try:
            from langchain_qwq import ChatQwen
        except ImportError as e:
            raise ConfigurationError("Install langchain-qwq") from e
        return ChatQwen(**kwargs)
    ```

    ## When Ignores Are Acceptable

    Only with documentation:

    ```toml
    [tool.ruff.lint.per-file-ignores]
    "engine/tools/_optional.py" = ["PLC0415"]  # handles optional dependency
    "tests/**/*.py" = ["S101", "ANN"]          # assert + no annotations in tests
    ```
    RULE_EOF
    cat > .cursor/rules/python-code-style.mdc << 'RULE_EOF'
    ---
    description: Python code style — import rules, naming conventions, data structure selection
    globs: **/*.py
    alwaysApply: false
    ---

    # Python Code Style

    ## Import Rules

    - ALWAYS place imports at the top of the file (module level)
    - NEVER use inline imports inside functions/methods in production code
    - Exceptions: test files, CLI scripts, or resolving genuine circular imports (must document why)

    ```python
    # ✅ CORRECT
    from novie_core.shared.encryption import EncryptionService

    def process_token(token: str) -> bytes:
        return EncryptionService().encrypt(token)

    # ❌ WRONG — inline import in production code
    def process_token(token: str) -> bytes:
        from novie_core.shared.encryption import EncryptionService
        return EncryptionService().encrypt(token)
    ```

    ## Naming Conventions (PEP 8)

    | Type              | Convention   | Example                            |
    |-------------------|--------------|------------------------------------|
    | Variables         | `snake_case` | `user_id`, `organization_name`     |
    | Functions/Methods | `snake_case` | `get_user()`, `check_connection()` |
    | Classes           | `PascalCase` | `AnalystRouter`, `WorkflowState`   |
    | Constants         | `UPPER_CASE` | `DEFAULT_TIMEOUT`, `MAX_RETRIES`   |
    | Enum members      | `UPPER_CASE` | `ToolMode.ENFORCED`                |

    ## Data Structure Guidelines

    **Pydantic BaseModel** — Default choice for most cases:
    - API request/response models
    - Domain entities and configuration
    - LangChain tool schemas, LangGraph state validation
    - External API responses (runtime validation)

    **TypedDict** — Limited use:
    - GraphQL context (required by Strawberry framework)
    - Performance-critical dict-like structures where validation is unnecessary
    - NOT for LangGraph state — use Pydantic instead

    **@dataclass(frozen=True)** — Immutable containers:
    - Simple internal data without validation
    - Parameter Object pattern for function arguments

    ## Type Hints

    - Always use type hints for function signatures
    - Use `str | None` instead of `Optional[str]`
    - Use `list[str]` instead of `List[str]` (Python 3.14 target)
    - Import `Annotated` from `typing` for LangGraph reducers
    RULE_EOF
    cat > .cursor/rules/testing-conventions.mdc << 'RULE_EOF'
    ---
    description: Testing conventions — pytest, asyncio, testcontainers, file organization
    globs: "tests/**/*.py"
    alwaysApply: false
    ---

    # Testing Conventions

    ## Framework

    - **pytest** (>=9.0.2) with **pytest-asyncio** (auto mode)
    - **testcontainers** (>=4.0.0) for integration tests with PostgreSQL
    - Config in `pyproject.toml`: `asyncio_mode = "auto"`, `testpaths = ["tests"]`

    ## Directory Structure

    ```
    tests/
    ├── unit/
    │   └── core/
    │       └── test_*.py           # Unit tests (no external dependencies)
    └── integration/
        ├── conftest.py             # Shared fixtures (testcontainers)
        └── test_*.py               # Integration tests (requires Docker)
    ```

    ## Naming

    - Test files: `test_*.py`
    - Test functions: `test_<what_is_being_tested>`
    - Fixtures in `conftest.py` at appropriate directory level

    ## Test-Specific Relaxations

    These are acceptable in test files (configured in ruff per-file-ignores):

    - `S101` — `assert` statements allowed
    - `ANN` — Type annotations not required
    - `PLC0415` — Inline imports allowed (test-specific imports)

    ## Async Tests

    ```python
    async def test_persistence_isolation():
        """Tests run with asyncio_mode='auto' — no decorator needed."""
        result = await some_async_function()
        assert result is not None
    ```

    ## Running Tests

    ```bash
    make test-agentic         # From monorepo root
    uv run pytest             # From apps/agentic/
    uv run pytest tests/unit  # Unit tests only
    ```
    RULE_EOF
    mkdir -p .cursor/skills/git-branch-sync
    cat > .cursor/skills/git-branch-sync/SKILL.md << 'SKILL_EOF'
    ---
    name: git-branch-sync
    description: Sync local repo with remote main branch, create feature branches, and manage pre-commit/pre-push checks. Use when starting new work, before making commits, when user says "start working on", "create branch", "sync with main", "pull latest", or before submitting a PR.
    ---

    # Git Branch Sync — Branch Management & Conflict Prevention

    Use this skill at the **start of any development task** and **before submitting PRs** to ensure you're working on the latest code, on a properly named branch, and free of conflicts.

    ## Prerequisites

    - The working directory must be a git repository
    - Remote `origin` must be configured
    - Repository: `Novamind-Labs-Ltd/novie` (git root is `novie/`, not workspace root)

    ## Step-by-Step Procedure

    ### Step 1: Check Current State

    ```bash
    git status
    git branch --show-current
    git remote -v
    ```

    - If there are uncommitted changes, **stash them first** with `git stash` or ask the user to commit/discard.
    - Note the current branch name.

    ### Step 2: Sync with Main

    ```bash
    git checkout main
    git fetch origin
    git pull origin main
    ```

    - If `main` doesn't exist, try `master` as fallback.
    - If pull fails due to conflicts, alert the user and stop.

    ### Step 3: Create Feature Branch

    Branch naming convention:

    ```
    <type>/<issue-id>-<short-description>
    ```

    | Type | When to use |
    |------|------------|
    | `feat/` | New feature or capability |
    | `fix/` | Bug fix |
    | `chore/` | Maintenance, deps, config |
    | `refactor/` | Code refactor, no behavior change |
    | `docs/` | Documentation only |

    **Examples:**
    - `feat/NOV-35-hitl-interrupt-mechanism`
    - `fix/NOV-42-tenant-isolation-bug`
    - `chore/NOV-50-upgrade-langchain`

    **Rules:**
    - Use lowercase, hyphens for spaces
    - Include Linear issue ID if available (e.g., `NOV-35`)
    - Keep the description concise (3-5 words max)

    ```bash
    git checkout -b <branch-name>
    ```

    ### Step 4: Verify

    ```bash
    git branch --show-current
    git log --oneline -3
    ```

    Confirm the new branch is based on the latest main.

    ---

    ## Pre-PR Conflict Prevention Checklist

    **CRITICAL: Always perform these checks before creating a PR or pushing final changes.**

    ### Check 1: Rebase onto Latest Main

    Before submitting a PR, always rebase your branch onto the latest `origin/main` to catch conflicts early:

    ```bash
    git fetch origin main
    git log --oneline HEAD..origin/main   # see what main has that we don't
    git diff origin/main...HEAD --stat    # see our changes vs main
    ```

    If `origin/main` has new commits:

    ```bash
    git rebase origin/main
    ```

    If rebase has conflicts, resolve them locally (much easier than fixing on GitHub).

    ### Check 2: Detect Overlapping Work

    Check if any of your changed files were also modified on `main` since you branched:

    ```bash
    # Files changed in our branch
    git diff origin/main...HEAD --name-only > /tmp/our-files.txt

    # Files changed on main since we branched
    MERGE_BASE=$(git merge-base HEAD origin/main)
    git diff $MERGE_BASE..origin/main --name-only > /tmp/main-files.txt

    # Overlapping files (potential conflicts)
    comm -12 <(sort /tmp/our-files.txt) <(sort /tmp/main-files.txt)
    ```

    If there are overlapping files:
    - **Review each one** — determine if both changes are needed or if one supersedes the other
    - If changes overlap significantly, consider **rebasing and resolving** before pushing

    ### Check 3: Verify API Compatibility

    When tests reference internal functions/classes, verify they still exist on `main`:

    ```bash
    # Example: check if a function still exists at the expected import path
    python3 -c "from novie_agentic.core.llm import get_llm; print('OK')" 2>&1
    ```

    Common API drift patterns to watch for:
    - Module reorganization (e.g., `core.config.get_llm` → `core.llm.get_llm`)
    - Function signature changes (added/removed parameters)
    - Renamed or deleted functions
    - Pydantic model field changes

    ### Check 4: Local CI Simulation

    Run the same checks CI will run before pushing:

    ```bash
    # Lint + type check
    uv run ruff check apps/agentic/
    uv run mypy --config-file apps/agentic/pyproject.toml apps/agentic/

    # Unit tests
    uv run pytest apps/agentic/tests/ -v --tb=short --no-cov
    ```

    ---

    ## Scope Discipline: Avoid Duplicate Commits

    ### Problem

    When multiple branches modify the same core files, merging creates massive conflicts. This typically happens when:
    - Two branches independently refactor the same module
    - A "big refactor" branch coexists with feature branches touching the same area
    - Archived/moved files are handled differently across branches (rename vs delete)

    ### Prevention Rules

    1. **One owner per file area** — If a refactoring PR is in-flight for a module, do NOT modify the same files in another branch
    2. **Small, focused PRs** — Keep PRs scoped to a single concern. Separate tests, config, and core changes into distinct PRs when possible
    3. **Check main before starting** — Run `git log origin/main --oneline -10` to see recent merges that might overlap with your planned work
    4. **Communicate via Linear** — Before starting work on shared code areas, check if related issues are already in progress

    ### Recovery: When Duplicate Work Happens

    If you discover your branch overlaps with recently merged changes:

    1. **Identify unique additions** — What does your branch have that `main` does not?
    2. **Reset to main** — `git reset --hard origin/main`
    3. **Cherry-pick or re-apply only unique changes** — Copy unique files from backup, not the overlapping ones
    4. **Force push** — `git push --force-with-lease` to update the PR

    ---

    ## How to Derive Branch Name

    1. If user provides a **Linear issue ID** (e.g., NOV-35), use it as prefix after type
    2. If user provides a **description**, extract keywords for the short description
    3. If user provides **both**, combine them: `feat/NOV-35-short-description`
    4. If neither is provided, **ask the user** for a branch name or description

    ## Error Handling

    | Error | Action |
    |-------|--------|
    | Uncommitted changes | `git stash`, proceed, remind user to `git stash pop` later |
    | Branch already exists | Ask user: checkout existing or create with suffix? |
    | Pull conflicts on main | Alert user, do NOT force-resolve |
    | No remote configured | Alert user to set up remote first |
    | Rebase conflicts | Resolve interactively, `git rebase --continue` after each fix |
    | Duplicate work detected | Follow Recovery procedure above |
    SKILL_EOF
    mkdir -p .cursor/skills/pr-create-describe
    cat > .cursor/skills/pr-create-describe/SKILL.md << 'SKILL_EOF'
    ---
    name: pr-create-describe
    description: Commit changes, push branch, and create a GitHub Pull Request with an auto-generated description based on code diff and Linear issue context. Use when user says "create PR", "submit PR", "push and create pull request", or "open a PR".
    ---

    # PR Create & Describe — Commit, Push, and Open Pull Request

    Use this skill to **commit staged changes, push the branch, and create a well-described PR** linking code changes to the requirement context.

    ## Prerequisites

    - On a feature branch (not `main`/`master`)
    - Changes are ready to commit (or already committed)
    - Repository has a remote `origin`

    ## Step-by-Step Procedure

    ### Step 1: Identify Repository Info

    ```bash
    git remote get-url origin
    ```

    Extract `owner` and `repo` from the remote URL (e.g., `github.com/owner/repo.git`).

    ### Step 2: Ensure Changes Are Committed

    ```bash
    git status
    git diff --stat
    ```

    If there are uncommitted changes:

    1. Review all changes with `git diff` (unstaged) and `git diff --cached` (staged)
    2. Create commit(s) following **conventional commit** format from `git-conventions.mdc`:

    ```
    feat: add brainstorming workflow YAML
    fix: correct tenant isolation in analyst router
    NA-35: implement HITL interrupt mechanism
    ```

    3. Stage and commit:

    ```bash
    git add <relevant-files>
    git commit -m "<conventional-commit-message>"
    ```

    - **NEVER** use `--no-verify`
    - If pre-commit hooks fail, fix the issue first (see `git-conventions.mdc`)

    ### Step 3: Analyze Changes for PR Description

    Run these in parallel to gather context:

    **A. Git diff against base branch:**

    ```bash
    git log main..HEAD --oneline
    git diff main...HEAD --stat
    ```

    **B. GitNexus change impact analysis** (MCP: `user-gitnexus`):

    Call `detect_changes` with `scope: "compare"` and `base_ref: "main"` to understand:
    - Which symbols changed
    - Which execution flows are affected
    - Risk assessment

    **C. Linear issue context** (MCP: `user-linear`, if issue ID is available):

    Call `get_issue` with the Linear issue ID to retrieve:
    - Issue title and description
    - Acceptance criteria
    - Priority and labels

    ### Step 4: Push the Branch

    ```bash
    git push -u origin HEAD
    ```

    ### Step 5: Create the Pull Request

    Use MCP tool `user-github` → `create_pull_request` with:

    ```json
    {
      "owner": "<owner>",
      "repo": "<repo>",
      "title": "<PR title>",
      "head": "<current-branch>",
      "base": "main",
      "body": "<generated PR body>",
      "draft": false
    }
    ```

    ### PR Title Format

    Mirror the primary commit's conventional commit message, or summarize if multiple commits:

    - Single commit: `feat: add HITL interrupt mechanism`
    - Multiple commits: `feat: HITL interrupt mechanism + workflow lifecycle fixes`
    - With issue ID: `NA-35: implement HITL interrupt mechanism`

    ### PR Body Template

    Generate the body using this structure:

    ```markdown
    ## Summary

    <2-4 bullet points describing WHAT changed and WHY>

    ## Linear Issue

    - [NA-XX: Issue Title](https://linear.app/team/issue/NA-XX)

    ## Changes

    <List of key changes grouped by area, derived from git diff and GitNexus analysis>

    ### Files Changed
    - `path/to/file.py` — <brief description>
    - `path/to/other.py` — <brief description>

    ## Impact Analysis

    <Risk level from GitNexus detect_changes>
    <Affected execution flows>
    <Affected modules>

    ## Test Plan

    - [ ] <Specific test scenarios based on the changes>
    - [ ] <Edge cases to verify>
    - [ ] Existing tests pass (`uv run pytest`)
    - [ ] Lint clean (`uv run ruff check .`)
    ```

    ### Step 6: Report Result

    After PR creation, output:
    - PR URL
    - PR number (needed for CI monitoring skill)
    - Summary of what was included

    ## Notes

    - If the user doesn't specify a Linear issue ID, skip the Linear section in the PR body
    - If GitNexus is not available or returns errors, fall back to `git diff --stat` for the impact section
    - Always ensure the PR base is `main` unless the user specifies otherwise
    SKILL_EOF
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

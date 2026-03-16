---
backend: cursor

tracker:
  kind: linear
  api_key: $LINEAR_API_KEY
  project_slug: ""
  team_id: "e9628d4c-7a56-454b-964d-6276b7138652"
  assignee: ""
  active_states: [Backlog, Todo, In Progress]
  terminal_states: [Done, Cancelled, Closed]
  handoff_states: [Human Review, In Review]

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
    cat > .cursor/rules/plan-before-coding.mdc << 'RULE_EOF'
    ---
    description: Always plan before writing code — produce a concise implementation plan, get it right, then execute
    alwaysApply: true
    ---

    # Plan Before Coding

    Before writing any code, **always produce a concise implementation plan** first.

    ## Required Planning Steps

    1. **Understand the problem** — read the issue description, relevant files, and existing tests carefully.
    2. **Identify the scope** — list which files will be created or modified and why.
    3. **Design the approach** — describe the algorithm, data structure, or architecture decision chosen, and why alternatives were rejected.
    4. **List acceptance criteria** — enumerate what must be true for the implementation to be considered complete.
    5. **Start coding only after the plan is clear** — do not write production code until steps 1–4 are done.

    ## Format

    Output your plan as a short Markdown section titled `## Implementation Plan` before any code blocks.

    ## Rationale

    - Planning catches design flaws before they are expensive to fix.
    - A written plan acts as a self-review and reduces unnecessary back-and-forth.
    - Separating thinking from typing improves output quality in autonomous sessions.
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
    - Repository: `$GITHUB_OWNER/$GITHUB_REPO` (set via environment variables)

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
      "draft": true
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
    mkdir -p .cursor/skills/ci-monitor-fix
    cat > .cursor/skills/ci-monitor-fix/SKILL.md << 'SKILL_EOF'
    ---
    name: ci-monitor-fix
    description: Monitor CI/CD check status after a PR is submitted, retrieve error logs on failure, and fix issues automatically. Use when user says "check CI", "monitor PR checks", "fix CI", "CI failed", or after creating a PR.
    ---

    # CI Monitor & Fix — Automatic CI Monitoring Loop

    Use this skill **after a PR is created** to automatically monitor CI/CD pipeline status, retrieve failure logs, fix issues, and re-monitor until all checks pass.

    ## Prerequisites

    - A PR has been created (you need the PR number)
    - Repository: `$GITHUB_OWNER/$GITHUB_REPO` (set via environment variables)
    - Git credentials available in `~/.git-credentials`

    ## Step-by-Step Procedure

    ### Step 1: Extract GitHub Token & Define Helper

    All API calls use the stored git credentials. Extract the token once:

    ```bash
    TOKEN=$(cat ~/.git-credentials 2>/dev/null | grep github.com | head -1 | sed 's/.*:\/\/[^:]*:\([^@]*\)@.*/\1/')
    ```

    ### Step 2: Poll Check Runs Until All Complete

    Query the check runs for the PR's head commit SHA and poll until no checks have `status: "in_progress"` or `status: "queued"`.

    ```bash
    curl -s \
      -H "Authorization: token $TOKEN" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/$GITHUB_OWNER/$GITHUB_REPO/commits/<HEAD_SHA>/check-runs" \
      | python3 -c "
    import sys, json
    d = json.load(sys.stdin)
    total = d['total_count']
    done = sum(1 for cr in d['check_runs'] if cr['status'] == 'completed')
    failed = [cr['name'] for cr in d['check_runs'] if cr.get('conclusion') == 'failure']
    passed = [cr['name'] for cr in d['check_runs'] if cr.get('conclusion') == 'success']
    skipped = [cr['name'] for cr in d['check_runs'] if cr.get('conclusion') == 'skipped']
    running = [cr['name'] for cr in d['check_runs'] if cr['status'] in ('in_progress', 'queued')]
    print(f'Total: {total} | Done: {done} | Running: {len(running)}')
    print(f'  Passed:  {passed}')
    print(f'  Failed:  {failed}')
    print(f'  Skipped: {skipped}')
    print(f'  Running: {running}')
    print(f'ALL_DONE={\"yes\" if done == total and total > 0 else \"no\"}')
    print(f'HAS_FAILURE={\"yes\" if failed else \"no\"}')
    "
    ```

    **Polling strategy:**

    | Interval | When |
    |----------|------|
    | `sleep 30` | First 3 polls |
    | `sleep 60` | Polls 4-10 |
    | `sleep 120` | Polls 11+ |
    | **Stop** | After 20 polls (~25 min) or all checks complete |

    - If `ALL_DONE=yes` and `HAS_FAILURE=no` → **All CI passed**, go to Step 6.
    - If `ALL_DONE=yes` and `HAS_FAILURE=yes` → Go to Step 3.
    - If `ALL_DONE=no` → Wait and poll again.

    ### Step 3: On Failure — Get Error Logs

    Find the failed workflow run and download its logs:

    ```bash
    # List workflow runs for the branch
    curl -s \
      -H "Authorization: token $TOKEN" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/$GITHUB_OWNER/$GITHUB_REPO/actions/runs?branch=<BRANCH>&per_page=5" \
      | python3 -c "
    import sys, json
    d = json.load(sys.stdin)
    for run in d['workflow_runs']:
        if run.get('conclusion') == 'failure':
            print(f'FAILED_RUN_ID={run[\"id\"]}')
            print(f'  {run[\"name\"]} #{run[\"run_number\"]} - {run[\"html_url\"]}')
            break
    "

    # Get jobs for the failed run
    curl -s \
      -H "Authorization: token $TOKEN" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/$GITHUB_OWNER/$GITHUB_REPO/actions/runs/<RUN_ID>/jobs" \
      | python3 -c "
    import sys, json
    d = json.load(sys.stdin)
    for job in d['jobs']:
        if job.get('conclusion') == 'failure':
            print(f'FAILED_JOB_ID={job[\"id\"]}')
            for step in job['steps']:
                icon = '✓' if step.get('conclusion') == 'success' else '✗' if step.get('conclusion') == 'failure' else '○'
                print(f'  {icon} {step[\"name\"]}')
    "

    # Download failed job logs (last 150 lines usually contain the error)
    curl -s -L \
      -H "Authorization: token $TOKEN" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/$GITHUB_OWNER/$GITHUB_REPO/actions/jobs/<JOB_ID>/logs" \
      | tail -150
    ```

    ### Step 4: Analyze and Fix the Error

    Common CI failure categories:

    | Category | Indicators | Fix Strategy |
    |----------|-----------|-------------|
    | **Import sorting** | `I001`, `isort` | `uv run ruff check --fix <path>` |
    | **Unused imports** | `F401` | `uv run ruff check --fix <path>`, manual for unsafe fixes |
    | **Unused variables** | `F841` | Remove assignment or prefix with `_` |
    | **Type errors** | `mypy`, `pyright` | Fix type annotations |
    | **Test failures** | `pytest`, `FAILED` | Read test output, fix logic or update tests |
    | **Build errors** | `ModuleNotFoundError` | Check imports, run `uv sync` |
    | **Line too long** | `E501` | Break long lines |

    **Always verify the fix locally before pushing:**

    ```bash
    uv run ruff check apps/agentic/
    uv run pytest apps/agentic/tests/ -v --tb=short --no-cov
    ```

    ### Step 5: Commit, Push, and Re-monitor

    ```bash
    git add <fixed-files>
    git commit -m "fix(ci): <describe the fix>"
    git push
    ```

    - **NEVER** use `--no-verify`
    - After push, go back to **Step 2** and monitor again
    - Repeat the fix → push → monitor cycle up to **3 iterations**
    - If still failing after 3 attempts, report full error details to the user

    ### Step 6: Report Final Status

    When all checks pass, output:

    - Confirmation that all CI checks passed
    - PR number and URL
    - List of checks and their status
    - Summary of any fixes applied during the process

    ## Automated Monitor Loop (reference implementation)

    For quick use, the full polling loop in one command:

    ```bash
    TOKEN=$(cat ~/.git-credentials 2>/dev/null | grep github.com | head -1 | sed 's/.*:\/\/[^:]*:\([^@]*\)@.*/\1/')
    SHA="<HEAD_SHA>"
    for i in $(seq 1 20); do
      RESULT=$(curl -s -H "Authorization: token $TOKEN" -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/$GITHUB_OWNER/$GITHUB_REPO/commits/$SHA/check-runs" \
        | python3 -c "
    import sys, json
    d = json.load(sys.stdin)
    t = d['total_count']
    done = sum(1 for c in d['check_runs'] if c['status']=='completed')
    fail = [c['name'] for c in d['check_runs'] if c.get('conclusion')=='failure']
    run = [c['name'] for c in d['check_runs'] if c['status'] in ('in_progress','queued')]
    print(f'Poll {\"$i\"}: {done}/{t} done, {len(fail)} failed, {len(run)} running')
    for c in d['check_runs']:
        s = c.get('conclusion') or c['status']
        print(f'  [{s:>12}] {c[\"name\"]}')
    if done == t and t > 0:
        print('STATUS=DONE_FAIL' if fail else 'STATUS=DONE_PASS')
    else:
        print('STATUS=PENDING')
    ")
      echo "$RESULT"
      echo "$RESULT" | grep -q "STATUS=DONE_PASS" && echo "All CI passed!" && break
      echo "$RESULT" | grep -q "STATUS=DONE_FAIL" && echo "CI has failures - investigate" && break
      [ "$i" -le 3 ] && sleep 30 || { [ "$i" -le 10 ] && sleep 60 || sleep 120; }
    done
    ```

    ## Error Handling

    | Situation | Action |
    |-----------|--------|
    | Token not found | Check `~/.git-credentials` exists with github.com entry |
    | API rate limit (403) | Wait 60s and retry |
    | No check runs (total=0) | CI may not be configured for this path; check `.github/workflows/ci.yml` trigger paths |
    | Network timeout | Retry once, then inform user |
    | Flaky test (passes on retry) | Note it, suggest investigating later |
    | CI infra error (not code-related) | Inform user, suggest re-running on GitHub |
    SKILL_EOF
    mkdir -p .cursor/skills/linear-agentic-issues
    cat > .cursor/skills/linear-agentic-issues/SKILL.md << 'SKILL_EOF'
    ---
    name: linear-agentic-issues
    description: When creating or drafting Linear issues for the AI orchestration (agentic) work area, follow the team title format in English and always add the Agentic label. Use when writing Linear issue titles, creating issues for agentic/orchestrator work, or when the user asks about Linear issue format or agentic issue conventions.
    ---

    # Linear Issue Format — Agentic Work Area

    Use this format when creating or drafting Linear issues that fall under your responsibility (AI orchestration / agentic).

    ## Title Format (English)
    [Px] [type] Short descriptive title in English

    - **Priority first**: `[P0]` (urgent) through `[P2]` / `[P8]` as used by the team. Use the same P-scale as existing issues.
    - **Type second**: One of the type tags below.
    - **Title**: Clear, in English. No need to repeat [agentic] in the title.

    ## Type Tags

    | Tag       | Use for |
    |----------|---------|
    | `[feat]` | New feature or capability |
    | `[fix]`  | Bug fix or correction |
    | `[bug]`  | Bug report or fix (alternate) |
    | `[doc]`  | Documentation only |
    | `[refactor]` | Code refactor, no behavior change |
    | `[arch]` | Architecture or design decision |
    | `[design]` | UI/UX or design work |

    Use lowercase in the title, e.g. `[feat]`, `[doc]`.

    ## Label (Required for Your Area)

    - **Always add the Linear label**: `Agentic` (or the exact label name your workspace uses for the agentic work area).
    - This marks the issue as your responsibility and allows filtering "my work" in Linear.
    - Do not add `[agentic]` in the title; use the label only.

    ## Examples

    - `[P0] [feat] PM SubAgent skeleton and Registry registration`
    - `[P1] [feat] seed_skills guard against overwrite and learned namespace`
    - `[P1] [feat] In-process Event Bus for analyst workflow events`
    - `[P2] [refactor] Supervisor system prompt structured config`
    - `[P1] [doc] Agentic data pipeline and sync services guideline`
    - `[P2] [fix] Workflow lifecycle bugs — envelope, routing fast-path, artifact path`

    ## Checklist When Creating an Issue

    - [ ] Title starts with `[Px]` (priority).
    - [ ] Second segment is `[type]` (e.g. `[feat]`, `[doc]`).
    - [ ] Rest of title is in English and concise.
    - [ ] Label `Agentic` (or equivalent) is added to the issue.
    SKILL_EOF
    mkdir -p .cursor/skills/linear-update-on-pr
    cat > .cursor/skills/linear-update-on-pr/SKILL.md << 'SKILL_EOF'
    ---
    name: linear-update-on-pr
    description: Update Linear issue status and add a comment with PR link after a PR passes CI checks. Use when user says "update Linear", "mark issue done", "PR is ready", or after CI checks pass successfully.
    ---

    # Linear Update on PR — Sync Issue Status After PR Success

    Use this skill **after a PR has been created and CI checks have passed** to update the corresponding Linear issue.

    ## Prerequisites

    - A PR exists and CI checks have passed (or PR is ready for review)
    - The Linear issue ID is known (e.g., `NA-35` from the branch name or PR title)
    - MCP `user-linear` is configured

    ## Step-by-Step Procedure

    ### Step 1: Identify the Linear Issue

    Extract the issue identifier from one of these sources (in order of priority):

    1. **User explicitly provides it** (e.g., "update NA-35")
    2. **Branch name** — parse from `feat/NA-35-description` → `NA-35`
    3. **PR title** — parse from `NA-35: implement feature` → `NA-35`
    4. **Ask the user** if none of the above yields an ID

    ### Step 2: Get Current Issue State

    Use MCP `user-linear` → `get_issue`:

    ```json
    {
      "id": "NA-35"
    }
    ```

    Verify:
    - The issue exists
    - The current status (to avoid redundant updates)

    ### Step 3: Update Issue Status

    Use MCP `user-linear` → `update_issue`:

    ```json
    {
      "id": "<issue-id>",
      "state": "In Review"
    }
    ```

    **Status transition map:**

    | PR Event | Target Linear Status |
    |----------|---------------------|
    | Draft PR created, CI pending | `In Review` |
    | Draft PR created, CI passed | `In Review` |
    | PR merged | `Done` |

    - If the issue is already in the target status, skip the update.
    - If the team uses different status names, call `list_issue_statuses` first to discover available statuses, then pick the closest match.

    ### Step 4: Add PR Link as Comment

    Use MCP `user-linear` → `create_comment`:

    ```json
    {
      "issueId": "<issue-id>",
      "body": "**PR Submitted** — [#<pr-number>: <pr-title>](<pr-url>)\n\nCI Status: ✅ All checks passed\n\nChanges: <brief summary of key changes>"
    }
    ```

    ### Step 5: Attach PR Link to Issue

    Use MCP `user-linear` → `update_issue` with links:

    ```json
    {
      "id": "<issue-id>",
      "links": [
        {
          "url": "<pr-url>",
          "title": "PR #<pr-number>: <pr-title>"
        }
      ]
    }
    ```

    ### Step 6: Report Result

    Output:
    - Confirmation of Linear issue status update
    - Link to the Linear issue
    - Link to the PR

    ## Notes

    - If the user doesn't have a Linear issue for this work, **skip this skill entirely** — don't create a new issue unless explicitly asked.
    - The status names (`In Review`, `Done`, etc.) may differ between teams. Always call `list_issue_statuses` with the team name/ID if unsure.
    - If `update_issue` fails because of an invalid state name, fall back to listing available statuses and retry with the correct one.
    SKILL_EOF
    cat > .cursor/mcp.json << 'MCP_EOF'
    {
      "mcpServers": {
        "linear": {
          "command": "npx",
          "args": ["-y", "@linear/mcp-server"],
          "env": {
            "LINEAR_API_KEY": "${LINEAR_API_KEY}"
          }
        },
        "playwright": {
          "command": "npx",
          "args": ["-y", "@playwright/mcp@latest"]
        },
        "github": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-github"],
          "env": {
            "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"
          }
        },
        "gitnexus": {
          "command": "npx",
          "args": ["-y", "gitnexus@latest", "mcp"]
        },
        "greptile": {
          "type": "http",
          "url": "https://api.greptile.com/mcp",
          "headers": {
            "Authorization": "Bearer ${GREPTILE_API_KEY}"
          }
        }
      }
    }
    MCP_EOF
    # Claude Code config — mirror MCP and aggregate rules into CLAUDE.md
    mkdir -p .claude
    cp .cursor/mcp.json .claude/mcp.json
    {
      echo "# Project Rules"
      echo ""
      echo "These rules apply to all code changes in this repository."
      echo ""
      for f in .cursor/rules/*.mdc; do
        [ -f "$f" ] || continue
        # Strip YAML front matter, keep the markdown body
        sed -n '/^---$/,/^---$/!p' "$f"
        echo ""
        echo "---"
        echo ""
      done
    } > CLAUDE.md
    echo "[workspace-init] Generated .claude/mcp.json and CLAUDE.md"
    if [ -n "${GITHUB_TOKEN:-}" ]; then
      echo "[workspace-init] Cloning repository..."
      git clone --depth=1 "https://x-access-token:${GITHUB_TOKEN}@github.com/$GITHUB_OWNER/$GITHUB_REPO.git" . 2>/dev/null || true
    fi
  before_run: |
    if [ -d .git ]; then
      echo "[workspace-sync] Rebasing onto latest origin/main..."
      git fetch origin main --quiet 2>/dev/null || true
      git rebase origin/main --quiet 2>/dev/null || echo "[workspace-sync] Rebase skipped (not on a branch yet)"
    fi
    # Sync evolved Skills into the workspace's .cursor/skills directory
    if [ -n "$MAESTRO_WORKSPACE_ROOT" ] && [ -d "$MAESTRO_WORKSPACE_ROOT/.maestro/evolved_skills" ]; then
      echo "[skill-sync] Syncing evolved skills..."
      for skill_dir in "$MAESTRO_WORKSPACE_ROOT/.maestro/evolved_skills"/*/; do
        skill_name=$(basename "$skill_dir")
        target=".cursor/skills/$skill_name"
        mkdir -p "$target"
        cp -f "$skill_dir/SKILL.md" "$target/SKILL.md" 2>/dev/null || true
      done
    fi
  after_run: |
    python3 - << 'SANDBOX_EOF'
    import subprocess, sys, pathlib, os, datetime, json

    workspace = pathlib.Path(".")
    results_file = workspace / "sandbox-test-results.txt"
    fail_marker  = workspace / "SANDBOX_TEST_FAILED"
    timestamp    = datetime.datetime.now().isoformat(timespec="seconds")

    SKIP = {".venv", "venv", "__pycache__", ".git", ".mypy_cache",
            ".ruff_cache", ".pytest_cache", "node_modules"}

    def collect(pattern):
        return [p for p in workspace.rglob(pattern)
                if not any(s in p.parts for s in SKIP)]

    test_files = collect("test_*.py") + collect("*_test.py")
    if not test_files:
        results_file.write_text(f"[{timestamp}] No test files found — skipping sandbox run.\n")
        print("[sandbox-tests] No test files found — skipping.")
        sys.exit(0)

    print(f"[sandbox-tests] Found {len(test_files)} test file(s).")

    sandbox_domain = os.environ.get("SANDBOX_DOMAIN", "").strip()
    output = ""
    passed = False
    mode   = "local"

    if sandbox_domain:
        try:
            from opensandbox import SandboxSync
            from opensandbox.code_interpreter import CodeInterpreter

            py_files = {}
            for p in workspace.rglob("*.py"):
                if any(s in p.parts for s in SKIP):
                    continue
                try:
                    py_files[str(p.relative_to(workspace))] = p.read_text(errors="replace")
                except Exception:
                    pass

            runner_code = (
                "import sys, pathlib, pytest, tempfile\n"
                "files = " + json.dumps(py_files) + "\n"
                "with tempfile.TemporaryDirectory() as tmp:\n"
                "    root = pathlib.Path(tmp)\n"
                "    for rel, body in files.items():\n"
                "        dest = root / rel\n"
                "        dest.parent.mkdir(parents=True, exist_ok=True)\n"
                "        dest.write_text(body)\n"
                "    rc = pytest.main([str(root), '-v', '--tb=short', '-q', '--no-header'])\n"
                "    code = rc.value if hasattr(rc, 'value') else int(rc)\n"
                "    print('SANDBOX_EXIT=' + str(code))\n"
            )

            api_key = os.environ.get("SANDBOX_API_KEY", "")
            sandbox = SandboxSync.create(
                image="python:3.11-slim",
                env={"PYTHONUNBUFFERED": "1"},
            )
            ci = CodeInterpreter(sandbox)
            ci.run(
                "import subprocess, sys; subprocess.run("
                "[sys.executable, '-m', 'pip', 'install', 'pytest', '-q'], "
                "capture_output=True)",
                language="python",
            )
            result = ci.run(runner_code, language="python")
            sandbox.close()

            raw_out = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
            passed  = "SANDBOX_EXIT=0" in raw_out
            output  = raw_out.replace("SANDBOX_EXIT=0", "").replace(
                      "SANDBOX_EXIT=" + raw_out.split("SANDBOX_EXIT=")[-1][:2].strip(), "").strip()
            mode    = "sandbox"

        except ImportError:
            print("[sandbox-tests] opensandbox not installed — falling back to local pytest.")
        except Exception as exc:
            print(f"[sandbox-tests] Sandbox error: {exc} — falling back to local pytest.")

    if mode == "local":
        proc   = subprocess.run(
            [sys.executable, "-m", "pytest", ".", "-v", "--tb=short", "-q", "--no-header"],
            capture_output=True, text=True, cwd=str(workspace),
        )
        output = proc.stdout + (proc.stderr or "")
        passed = proc.returncode == 0

    marker = "PASSED" if passed else "FAILED"
    results_file.write_text(f"[{timestamp}] [{mode.upper()}] {marker}\n\n{output}")

    if passed:
        fail_marker.unlink(missing_ok=True)
        print(f"[sandbox-tests] ✅ {marker} — sandbox-test-results.txt updated")
    else:
        fail_marker.write_text(f"Tests failed at {timestamp}\n")
        print(f"[sandbox-tests] ❌ {marker} — check sandbox-test-results.txt")
    SANDBOX_EOF
  before_remove: ""
  timeout_ms: 60000

cursor:
  command: agent
  model: sonnet-4.6
  plan_model: opus-4.6
  sandbox: disabled
  force: true
  trust: true
  approve_mcps: true
  turn_timeout_ms: 3600000
  stall_timeout_ms: 300000

claude_code:
  command: claude
  model: claude-sonnet-4-20250514
  plan_model: claude-sonnet-4-20250514
  api_key: $ANTHROPIC_API_KEY
  skip_permissions: false
  max_turns_per_invocation: 0
  max_budget_usd: 0
  turn_timeout_ms: 3600000
  stall_timeout_ms: 300000

agent:
  auto_dispatch: false
  max_concurrent_agents: 2
  max_turns: 10
  max_retry_backoff_ms: 300000
  max_concurrent_agents_by_state: {}

github:
  token: $GITHUB_TOKEN
  owner: Novamind-Labs-Ltd
  repo: $GITHUB_REPO
  ci_watch_states: [In Review]
  ci_poll_interval_ms: 60000
  ci_max_wait_ms: 1800000
  ci_pass_target_state: Human Review
  ci_fail_target_state: In Progress

server:
  port: 8080

evolution:
  enabled: false
  min_runs_between: 10        # trigger after this many successful runs since last cycle
  min_interval_minutes: 60    # minimum wall-clock minutes between cycles
  max_addendum_tokens: 500    # soft cap on addendum length (passed as guidance to the agent)
  max_new_skills_per_cycle: 2 # maximum new Skills created per cycle
  min_pattern_occurrences: 3  # a flow pattern must appear this many times to become a Skill
  auto_apply: false           # false → new Skills land in pending_skills/ for review; true → apply directly
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
1. **Branch setup** — Read and follow `.cursor/skills/git-branch-sync/SKILL.md` to ensure you are on a dedicated feature branch based on the latest `origin/main`. This is mandatory before any code changes.
2. Read the codebase and understand the project structure.
3. Implement the changes described above.
4. Write or update tests for your changes.
5. Ensure all existing tests still pass.
6. Check `sandbox-test-results.txt` if it exists — it contains results from the automated sandbox test runner that ran after the previous turn. If it shows `FAILED`, fix the reported errors before proceeding.
7. If `SANDBOX_TEST_FAILED` file exists in the workspace root, tests have not yet passed — do not move to Human Review until it is gone.
8. **Create PR** — Read and follow `.cursor/skills/pr-create-describe/SKILL.md` to rebase onto latest `origin/main`, resolve any conflicts, and create a **Draft** Pull Request.
9. **Monitor CI** — Read and follow `.cursor/skills/ci-monitor-fix/SKILL.md` to monitor CI/CD pipeline status, fix failures, and re-push until all checks pass.
10. **Update Linear** — Read and follow `.cursor/skills/linear-update-on-pr/SKILL.md` to move the issue to **In Review** after CI passes.
    - Maestro's CI Watcher will automatically verify the result and move the issue to **Human Review** when CI is confirmed green, or back to **In Progress** if CI fails.
    - A human will then perform E2E testing and merge the PR. **You must NOT do this yourself.**

## Strictly Forbidden Actions
You MUST NOT perform any of the following — these are controlled by Maestro's automated pipeline and human reviewers:
- **Do NOT merge any Pull Request** (no merge, squash-merge, or rebase-merge).
- **Do NOT mark a Draft PR as "Ready for Review"** — the CI Watcher and E2E test gate handle this.
- **Do NOT request reviewers** on any PR.
- **Do NOT rename PR titles** after creation.
- **Do NOT enable auto-merge** on any PR.
If CI requires a non-draft PR to trigger, configure the GitHub Actions workflow to run on `pull_request` with types including `opened, synchronize, reopened` — do NOT work around it by converting the draft.

## Decision Policy
- If you encounter a problem that requires human judgment (e.g., architectural decisions, ambiguous requirements, security-sensitive changes), **stop working and update the issue state to Human Review** with a comment explaining what decision is needed.
- Do NOT ask for user input. If you cannot proceed autonomously, hand off to a human.

{% if attempt %}
## Retry Context
This is retry attempt #{{ attempt }}. Review your prior work in this workspace and fix any issues found during the previous run.
{% endif %}

{% if learning_context %}
## Execution History Insights
The following patterns have been observed from recent automated runs across this project. Use these insights to avoid known pitfalls:

{{ learning_context }}
{% endif %}

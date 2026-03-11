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

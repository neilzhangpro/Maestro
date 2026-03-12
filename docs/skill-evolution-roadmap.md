# SKILL Self-Evolution Roadmap

This document describes a four-stage plan for evolving Maestro's SKILL system
from **static instruction files** to a **self-learning, self-improving** agent
knowledge base.  Each stage is additive — later stages build on the
infrastructure introduced by earlier ones.

---

## Stage 0 — Passive Logging

**Status:** Implemented (via `RunRecorder`)

### Goal

Capture structured, per-turn execution outcomes in a shared JSONL log that
survives individual workspace lifecycle events (create / destroy).

### Architecture

```
workspace_root/
└── .maestro/
    └── run_history.jsonl   ← append-only, one JSON object per line
```

Each line is a `RunRecord`:

| Field              | Type         | Description                                  |
|--------------------|--------------|----------------------------------------------|
| `issue_identifier` | `str`        | Linear issue key (e.g. `NOV-42`)             |
| `timestamp_utc`    | `str`        | ISO 8601 timestamp                           |
| `turn`             | `int`        | Turn number within the session               |
| `attempt`          | `int | null` | Retry attempt counter                        |
| `success`          | `bool`       | Whether the turn succeeded                   |
| `error`            | `str | null` | Error category when `success=false`          |
| `duration_ms`      | `int`        | Wall-clock duration of the turn              |
| `tools_used`       | `list[str]`  | Deduplicated tool names observed via events  |
| `output_summary`   | `str`        | First 300 chars of agent output text         |

### Risks

- **Disk growth:** Unbounded append.  Mitigated by keeping records small
  (~500 bytes each).  A future compaction step can trim to the latest N
  records.
- **Concurrent writes:** Protected by `fcntl.flock` (POSIX advisory lock).

---

## Stage 1 — Learning Annotations (Prompt Injection)

**Status:** Implemented

### Goal

Inject a compact summary of recent execution history into the agent prompt so
the agent is aware of recurring failure patterns and can proactively avoid them.

### How it works

1. Before building the prompt for each turn, the `Worker` calls
   `RunRecorder.build_learning_context(limit=30)`.
2. The method reads the last 30 records, computes:
   - Overall success rate
   - Top failure patterns (grouped by error field)
   - Most-used tools
   - Last failure excerpt
3. The resulting Markdown string is passed to `render_prompt()` as the
   `learning_context` variable.
4. The `WORKFLOW.md` Liquid template contains a conditional block:

```liquid
{% if learning_context %}
## Execution History Insights
The following patterns have been observed from recent automated runs
across this project. Use these insights to avoid known pitfalls:

{{ learning_context }}
{% endif %}
```

When there is no history (first run ever), the block is invisible.

### Limitations

- The agent receives *read-only* insights; it cannot modify SKILL files.
- Context window budget: the summary is capped at a few hundred tokens.
- No per-issue filtering yet — all history is project-wide.

---

## Stage 2 — SKILL Incremental Patches (Planned)

### Goal

Automatically generate *addenda* (post-scripts) to existing SKILL files based
on accumulated execution history.  These addenda capture lessons learned without
mutating the original SKILL body.

### Prerequisites

- Stage 1 must be stable and producing useful context.
- A `SkillStore` abstraction that can enumerate, read, and append to SKILL
  files.
- A "meta-prompt" template that asks the LLM to generate addenda given a
  SKILL's current body and recent failure history.

### Proposed Architecture

```
┌──────────────┐     ┌─────────────────┐     ┌────────────────┐
│ RunRecorder  │────→│ SkillAnalyser   │────→│ LLM (meta)     │
│ (history)    │     │ (pattern match) │     │ "generate      │
└──────────────┘     └─────────────────┘     │  addendum"     │
                                              └───────┬────────┘
                                                      │
                                                      ▼
                                            ┌────────────────────┐
                                            │ SKILL.md           │
                                            │ ──────────         │
                                            │ (original body)    │
                                            │                    │
                                            │ --- LEARNED ---    │
                                            │ (auto-generated    │
                                            │  addendum section) │
                                            └────────────────────┘
```

### Addendum format

```markdown
<!-- LEARNED — auto-generated, do not edit manually -->
## Lessons Learned (auto-updated YYYY-MM-DD)

- When rebasing, always run `git fetch origin main` first to avoid
  "diverged" errors.  (observed 3 failures in last 20 runs)
- The `uv run ruff check` step often fails with `I001` if new files
  are added without updating `__init__.py`.
```

### Safeguards

- Addenda are appended after a `<!-- LEARNED -->` sentinel; the original
  body above the sentinel is never touched.
- A diff is logged before writing, enabling manual review.
- A maximum addendum size (e.g. 500 tokens) prevents unbounded growth.
- The generation runs at most once per N successful runs (e.g. every 10)
  to avoid churn.

### Risks

| Risk                    | Mitigation                                          |
|-------------------------|-----------------------------------------------------|
| Hallucinated advice     | Validate addenda against actual history records     |
| Stale addenda           | Timestamp each entry; prune entries older than 30d  |
| Breaking shell commands | Addenda are advisory text only, not executable      |
| Noise from flaky tests  | Require ≥3 occurrences before surfacing a pattern   |

---

## Stage 3 — Full Self-Mutation (Planned)

### Goal

The LLM rewrites entire SKILL bodies based on long-term performance data,
with sandbox validation and automatic rollback on regression.

### Prerequisites

- Stage 2 must demonstrate measurable improvement (success rate delta).
- A deterministic "SKILL test harness" that can dry-run a SKILL in a
  sandboxed workspace and verify expected outcomes.
- Version control for SKILL files (git-backed, with per-mutation commits).

### Proposed Workflow

```
1. Trigger: every N runs OR on manual request
2. LLM receives:
   - Current SKILL body
   - Last 100 RunRecords filtered to issues that invoked this SKILL
   - Success/failure rate before and after the last mutation
3. LLM outputs: new SKILL body (full replacement)
4. Validation:
   a. Diff against current body — reject if >60% changed (safety brake)
   b. Run SKILL test harness in sandbox
   c. If tests pass → commit new body with message "skill-mutation: <reason>"
   d. If tests fail → rollback, log failure, increment cooldown counter
5. Cooldown: after 2 consecutive failed mutations, pause self-mutation
   for this SKILL for 7 days.
```

### Safeguards

| Safeguard               | Detail                                              |
|--------------------------|-----------------------------------------------------|
| Change cap               | Max 60% diff from previous version                  |
| Sandbox validation       | New SKILL must pass test harness before commit       |
| Auto-rollback            | `git revert` on regression detected in next 5 runs  |
| Human override           | `SKILL.md` can contain `<!-- NO_AUTO_MUTATE -->` to  |
|                          | permanently opt out of self-mutation                 |
| Audit trail              | Every mutation is a git commit with structured msg   |
| Cooldown                 | 2 consecutive failures → 7-day pause                 |

### Risks

| Risk                           | Severity | Mitigation                              |
|--------------------------------|----------|-----------------------------------------|
| Security (mutated shell cmds)  | High     | SKILLs with shell blocks exempt by default; require explicit opt-in |
| Convergence (noisy data)       | Medium   | Require statistical significance (≥20 samples, p<0.05) |
| Platform drift (Cursor vs CC)  | Medium   | Backend-specific SKILL variants possible |
| Interpretability               | Medium   | Keep mutation diffs small; log rationale |
| Cascading failures             | High     | Rollback + cooldown + human override    |

---

## Appendix: Metrics for Evaluating SKILL Evolution

To measure whether self-learning is working, track these metrics across
stages:

| Metric                    | Stage 0 | Stage 1 | Stage 2 | Stage 3 |
|---------------------------|---------|---------|---------|---------|
| Turn success rate         | Baseline| +5-10%  | +10-15% | +15-25% |
| Mean turns to completion  | Baseline| -0.5    | -1.0    | -1.5    |
| Repeated error rate       | Baseline| -20%    | -40%    | -60%    |
| Human intervention rate   | Baseline| -10%    | -25%    | -40%    |

These are estimated targets.  Actual improvements depend on the project,
team conventions, and the quality of SKILL files.

---

## Timeline

| Stage   | Status        | Estimated effort |
|---------|---------------|------------------|
| Stage 0 | Done          | —                |
| Stage 1 | Done          | —                |
| Stage 2 | Not started   | 2-3 days         |
| Stage 3 | Not started   | 1-2 weeks        |

Stage 2 should not be started until Stage 1 has been running in production
for at least 1-2 weeks and the accumulated history is large enough to
produce meaningful patterns (≥50 records).

# Phase 1：编排与工作台

> 目标：引入 Pipeline 编排 parse→plan→execute→check_ci→update 全流程，并搭建 Dashboard 作为触发与监控入口。

## 1.1 阶段目标

- [ ] 使用 Pipeline 引擎构建任务编排流程，支持多步执行与条件分支
- [ ] 实现 parse_issue、plan_tasks、execute_task、check_ci、update_linear 等节点
- [ ] 搭建 Dashboard：Linear 看板、手动触发、运行状态展示
- [ ] 支持「状态改为 In Progress → 自动触发编排」的流程

## 1.2 依赖

- Phase 0 已完成：ACP 客户端、Workspace Manager、Linear 客户端可用

## 1.3 目录结构扩展

```
maestro/
├── src/
│   ├── graph/              # Pipeline 编排
│   │   ├── graph.py        # 图定义
│   │   ├── nodes/
│   │   │   ├── parse.py
│   │   │   ├── plan.py
│   │   │   ├── execute.py
│   │   │   ├── check_ci.py
│   │   │   └── update_linear.py
│   │   └── state.py       # 图状态定义
│   ├── api/                # 后端 API
│   │   ├── main.py         # FastAPI app
│   │   ├── routes/
│   │   │   ├── issues.py
│   │   │   ├── runs.py
│   │   │   └── trigger.py
│   │   └── websocket.py    # 运行状态推送
│   └── dashboard/          # 前端（或独立 repo）
│       └── ...
├── config/
│   └── maestro.yaml
└── ...
```

## 1.4 详细实现

### 1.4.1 Pipeline 状态定义

```python
from typing import Any, TypedDict

class MaestroState(TypedDict, total=False):
    issue_id: str
    issue: dict[str, Any] | None
    plan: list[dict]
    current_task_index: int
    execute_result: dict[str, Any] | None
    ci_status: str | None
    pr_url: str | None
    linear_updated: bool
    error: str | None
    status: str  # pending | running | completed | failed
```

### 1.4.2 节点实现

#### 1. parse_issue

- **输入**：`state.issue_id`
- **逻辑**：调用 Linear 客户端 `fetch_issue(issue_id)`，写入 `state.issue`
- **输出**：更新 `state.issue`
- **边**：成功 → `plan_tasks`；失败 → `END`（记录 error）

#### 2. plan_tasks

- **输入**：`state.issue`
- **逻辑**：LLM 将 issue 拆分为子任务，如 `[编码, 写测试, 提交, 跑 CI]`
- **输出**：`state.plan = [{id, description, type}, ...]`，`state.current_task_index = 0`
- **边**：→ `execute_task`

#### 3. execute_task

- **输入**：`state.plan`、`state.current_task_index`、`state.issue`
- **逻辑**：
  - 取 `plan[current_task_index]` 作为当前任务
  - 构建 prompt：包含 issue 上下文 + 当前任务描述
  - 调用 Phase 0 的 ACP 客户端执行
  - 写入 `state.execute_result`
- **输出**：`state.execute_result`
- **边**：
  - 若任务类型为「提交」或「PR」→ `check_ci`
  - 若还有更多任务 → 自循环（`current_task_index += 1`）回到 `execute_task`
  - 若全部完成且无 CI → `update_linear`

#### 4. check_ci

- **输入**：`state.issue`、`state.execute_result`（含 PR 信息）
- **逻辑**：
  - 根据 CI 系统（GitHub Actions / GitLab CI 等）查询 PR 的 CI 状态
  - 写入 `state.ci_status`、`state.pr_url`
- **输出**：`state.ci_status`、`state.pr_url`
- **边**：
  - `success` → `update_linear`
  - `failure` → `execute_task`（重试修复，可限制次数）
  - `pending` → 等待节点（轮询或 sleep 后回到 `check_ci`）

#### 5. update_linear

- **输入**：`state.issue_id`、`state.ci_status`、`state.pr_url`、`state.execute_result`
- **逻辑**：
  - 更新 Linear issue 状态为 `Human Review` 或配置的 handoff 状态
  - 添加评论：PR 链接、CI 状态、简要总结
- **输出**：`state.linear_updated = True`
- **边**：→ `END`

### 1.4.3 图结构

```
START
  │
  ▼
parse_issue ──fail──► END
  │
  ▼
plan_tasks
  │
  ▼
execute_task ◄─────────────────┐
  │                              │
  ├── 有 CI 任务 ──► check_ci     │ 重试
  │       │                      │
  │       ├── success ──► update_linear ──► END
  │       │
  │       └── failure ──────────┘
  │
  └── 无 CI / 全部完成 ──► update_linear ──► END
```

### 1.4.4 运行入口

- **同步**：`graph.invoke({"issue_id": "ABC-123"})`，用于 CLI 或 API 单次触发
- **异步**：`graph.ainvoke(...)`，用于长时间运行，配合 WebSocket 推送中间状态

## 1.5 Dashboard 实现

### 1.5.1 后端 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/issues` | GET | 列出 Linear 候选 issue（可筛选 project、state） |
| `/api/issues/:id` | GET | 获取单个 issue 详情 |
| `/api/trigger` | POST | 触发编排，body: `{ issue_id }` |
| `/api/runs` | GET | 当前运行中的任务列表 |
| `/api/runs/:id` | GET | 单个 run 的详细状态（含 graph 中间状态） |
| `/api/runs/:id/events` | WebSocket | 订阅 run 的实时事件 |

### 1.5.2 前端功能

1. **看板视图**
   - 展示 Linear issue 列表，按状态分组（Todo / In Progress / Done）
   - 支持按 project、assignee 筛选

2. **触发操作**
   - 点击「开始」或拖拽到 In Progress：调用 `POST /api/trigger`
   - 显示「运行中」状态，禁用重复触发

3. **运行监控**
   - 列表展示：issue_id、当前节点、开始时间、进度
   - 详情：节点执行历史、agent 输出片段、错误信息

4. **结果展示**
   - CI 状态、PR 链接
   - 跳转到 Linear 查看评论和更新

### 1.5.3 技术栈建议

- 后端：Python + FastAPI + Pipeline Engine
- 前端：Next.js + Tailwind + Linear SDK 或 REST 代理
- 通信：REST + WebSocket（Server-Sent Events 也可）

## 1.6 配置扩展

```yaml
# maestro.yaml 新增

graph:
  max_ci_retries: 3
  ci_poll_interval_sec: 30
  handoff_state: Human Review   # Linear 状态名

ci:
  provider: github  # github | gitlab
  # GitHub: 需要 GITHUB_TOKEN
  # 根据 PR 的 commit SHA 查询 Actions 状态

dashboard:
  port: 3001
  cors_origins: ["http://localhost:3000"]
```

## 1.7 验收清单

- [ ] `maestro run ABC-123` 或 `POST /api/trigger` 能走完 parse→plan→execute→update
- [ ] Dashboard 能展示 Linear issue 并触发编排
- [ ] WebSocket 能推送 run 的节点切换和输出片段
- [ ] CI 失败时能重试 execute（可配置次数）

## 1.8 风险与缓解

| 风险 | 缓解 |
|------|------|
| LLM plan 质量不稳定 | 提供默认 plan 模板，LLM 仅做微调；或先用规则拆分 |
| CI 系统差异大 | 抽象 CI 接口，先支持 GitHub Actions，后续扩展 |
| 长时间运行超时 | 使用异步执行 + 状态持久化（Phase 3），或拆分为多个 API 调用 |

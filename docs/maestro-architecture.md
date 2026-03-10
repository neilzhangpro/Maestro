# Maestro 项目架构讨论

> 基于 Symphony SPEC、Cursor ACP、LangGraph、Linear MCP 的架构设计文档，供后续开发参考。

## 一、需求与 Symphony 的对应关系

| 你的需求 | Symphony 对应 | 差异/适配 |
|---------|---------------|-----------|
| 无需 AI IDE | ✅ 无 IDE 依赖 | Symphony 用 Codex app-server，你计划用 Cursor CLI |
| Linear 任务管理 | ✅ 原生支持 | 可直接复用 |
| LangGraph 任务编排 | ❌ 无 | Symphony 用简单轮询 + 状态机，你要用 LangGraph 做编排 |
| Cursor CLI 执行 | ⚠️ 部分对应 | Symphony 用 Codex app-server，Cursor 有 ACP |
| MCP 连接 | ⚠️ 部分对应 | Symphony 有 `linear_graphql` 工具，你可用 Linear MCP |
| SKILL 流程 | ⚠️ 部分对应 | Symphony 用 `WORKFLOW.md`，你计划用 Cursor SKILL |
| Dashboard 工作台 | ⚠️ 可选 | Symphony 有可选 HTTP 服务，你要做成核心入口 |
| 长时间运行 | ✅ 支持 | Symphony 设计为长期运行 |
| 环境隔离 | ✅ 支持 | 每 issue 独立 workspace |

---

## 二、核心架构设计

### 2.1 整体流程

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Maestro Dashboard / 工作台                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ Linear 任务 │  │ 状态变更    │  │ 触发编排    │  │ 人类 Review 反馈    │ │
│  │ 列表/看板   │  │ → In Progress│  │             │  │ CI 结果 / PR 链接   │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
         │                    │                    │                    │
         ▼                    ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         LangGraph 编排层                                      │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   │
│  │ 任务解析     │ → │ 子任务拆分   │ → │ 执行调度     │ → │ 结果聚合     │   │
│  │ (Issue→Plan) │   │ (Plan→Tasks) │   │ (Tasks→Run)  │   │ (Run→Report)  │   │
│  └──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
         │                    │                    │                    │
         ▼                    ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Cursor CLI (ACP) 执行层                              │
│  ┌──────────────────────────────────────────────────────────────────────────┐│
│  │ agent acp + MCP(Linear/Postgres/...) + SKILL(.cursor/skills/*.md)       ││
│  │ 编码 → 测试 → git commit → CI → 反馈到 Linear                             ││
│  └──────────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 触发机制：Symphony vs 你的设计

| 维度 | Symphony | 你的设计 |
|------|----------|----------|
| 触发方式 | 轮询 Linear active states | Dashboard 中手动改状态 → 触发 |
| 优点 | 全自动，无需人工 | 人工控制何时开始，更可控 |
| 实现 | Polling loop | Webhook / 轮询 / Dashboard 主动调用 |

建议：支持两种模式——  
1）Dashboard 手动触发；2）可选轮询模式（状态变为 In Progress 时自动触发）。

---

## 三、技术选型与实现要点

### 3.1 Cursor CLI (ACP) 替代 Codex app-server

Cursor 提供 `agent acp`，通过 stdio + JSON-RPC 与外部客户端通信，可替代 Symphony 中的 Codex app-server：

```bash
# 启动 ACP 模式
agent acp
```

要点：

- 协议：JSON-RPC 2.0，newline-delimited JSON
- 认证：`--api-key` / `CURSOR_API_KEY` 或 `agent login`
- 会话：`session/new`、`session/prompt`、`session/load`
- 权限：`session/request_permission`，可配置 `allow-once` / `allow-always` 等
- MCP：`session/new` 时传入 `mcpServers`，可挂 Linear、Postgres 等

与 Symphony 的差异：

- Symphony 用 Codex app-server 协议，Cursor 用 ACP
- 需要实现一个 ACP 客户端，负责：spawn `agent acp`、发送 prompt、处理权限、接收流式输出

### 3.2 LangGraph 任务编排

LangGraph 适合做多步骤、有分支和循环的编排，例如：

```
[开始] → [解析 Issue] → [规划子任务] → [执行子任务] 
    ↑                                        │
    └──────── [检查 CI] ← [提交 PR] ←─────────┘
                    │
                    ▼
              [CI 通过?] ──No──→ [修复] ──→ 回到执行
                    │
                   Yes
                    ▼
              [更新 Linear 状态] → [等待 Review]
```

建议的 LangGraph 节点：

1. **parse_issue**：从 Linear 拉取 issue，生成结构化任务描述
2. **plan_tasks**：LLM 拆分为子任务（编码、测试、提交、CI 等）
3. **execute_task**：调用 Cursor ACP 执行单个子任务
4. **check_ci**：查询 CI 状态
5. **update_linear**：更新 issue 状态、添加评论、PR 链接等
6. **human_review**：进入人工审核节点（human-in-the-loop）

LangGraph 的 checkpoint 和持久化可以支持长时间运行和断点续跑。

### 3.3 SKILL 与 WORKFLOW.md

| 维度 | Symphony WORKFLOW.md | Cursor SKILL |
|------|----------------------|--------------|
| 位置 | 仓库根目录 | `.cursor/skills/` 或 `$CODEX_HOME/skills` |
| 内容 | YAML front matter + prompt 模板 | Markdown 技能说明 |
| 用途 | 定义 agent 行为、配置 | 定义可复用的工作流/规则 |

建议：

- 用 SKILL 描述「编码 → 测试 → 提交 → CI」等标准流程
- 在 ACP 的 `session/new` 中通过 `mcpServers` 或 rules 引用这些 SKILL
- 保留类似 `WORKFLOW.md` 的 YAML 配置（轮询间隔、workspace 根目录、并发数等）

### 3.4 MCP 集成

你已有 Linear MCP，可覆盖：

- `list_issues`、`get_issue`、`save_issue`：任务列表、详情、状态更新
- `list_comments`、`save_comment`：评论、PR 链接
- `create_attachment`：CI 报告、截图等

在 ACP 中配置 MCP 时，需要把 Linear MCP 的配置传给 `agent acp`，使 Cursor 在执行任务时能直接调用这些工具。

### 3.5 环境隔离

沿用 Symphony 思路：

- 每个 issue 对应一个 workspace 目录：`{workspace_root}/{issue_identifier}/`
- `agent acp` 的 `cwd` 设为该 workspace
- 可选：用 Docker 或类似方案做更强隔离

### 3.6 长时间运行

- 使用 LangGraph 的 checkpoint 持久化（如 Redis、Postgres）
- 支持进程重启后从 checkpoint 恢复
- 对 Cursor ACP 会话做超时和重试策略

---

## 四、Dashboard 设计建议

### 4.1 核心功能

1. **Linear 看板**：展示 issue 列表、状态、优先级
2. **状态操作**：拖拽或点击将状态改为 In Progress，触发编排
3. **运行监控**：当前运行中的任务、进度、日志
4. **结果展示**：CI 状态、PR 链接、walkthrough 等
5. **Review 入口**：人工确认、合并或打回

### 4.2 技术栈建议

- 前端：React / Next.js + Linear SDK 或 Linear MCP
- 后端：Python（LangGraph）+ FastAPI，或 Node.js
- 通信：WebSocket 推送运行状态，REST 触发任务

---

## 五、实现路线图（建议）

> 详细分阶段实现文档见 [docs/phases/](./phases/00-overview.md)。

| 阶段 | 文档 | 内容 | 优先级 |
|------|------|------|--------|
| Phase 0 | [phase-0-foundation.md](./phases/phase-0-foundation.md) | ACP 客户端、Workspace、Linear 集成 | 高 |
| Phase 1 | [phase-1-orchestration.md](./phases/phase-1-orchestration.md) | LangGraph 编排、Dashboard | 高 |
| Phase 2 | [phase-2-enhancements.md](./phases/phase-2-enhancements.md) | SKILL 集成、MCP 传递 | 中 |
| Phase 3 | [phase-3-production.md](./phases/phase-3-production.md) | Checkpoint 持久化、Docker、监控 | 中 |

---

## 六、风险与注意事项

1. **ACP 协议稳定性**：ACP 仍在演进，需要关注 Cursor 文档和变更
2. **Cursor 认证**：无头环境需用 API Key，注意安全存储
3. **权限策略**：`allow-always` 适合自动化，但需评估安全风险
4. **SKILL 加载方式**：需确认 Cursor CLI 在 ACP 模式下如何加载 SKILL（rules / MCP / 环境变量等）
5. **CI 反馈**：需要明确 CI 系统（GitHub Actions、GitLab CI 等）及如何获取状态

---

## 七、与 Symphony 的复用建议

可以直接借鉴的部分：

- Workspace 布局与生命周期（创建、复用、清理）
- Linear 的 active/terminal states 定义
- 重试与 backoff 策略
- `WORKFLOW.md` 的 YAML 配置结构（可迁移为 `maestro.yaml` 等）

需要替换的部分：

- Codex app-server → Cursor ACP 客户端
- 简单轮询调度 → LangGraph 编排
- 可选 HTTP 服务 → 作为核心的 Dashboard

---

## 八、参考资源

- [Symphony SPEC](https://github.com/openai/symphony/blob/main/SPEC.md)
- [Symphony GitHub](https://github.com/openai/symphony)
- [Cursor CLI ACP 文档](https://cursor.com/docs/cli/acp)
- [Agent Client Protocol](https://agentclientprotocol.com/)
- [LangGraph 文档](https://docs.langchain.com/langgraph)

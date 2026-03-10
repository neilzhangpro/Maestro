# Phase 0：基础设施

> 目标：建立 ACP 客户端、Workspace 管理、Linear 集成，实现「单 issue → Cursor 执行 → 更新 Linear」的最小闭环。

## 0.1 阶段目标

- [ ] 实现 Cursor ACP 客户端，能 spawn `agent acp` 并完成一次 prompt 往返
- [ ] 实现 Workspace Manager，每 issue 独立目录，cwd 正确传入
- [ ] 实现 Linear 客户端，拉取 issue、更新状态
- [ ] 打通端到端：`maestro run <issue_id>` 可完成一次完整执行

## 0.2 目录结构建议

```
maestro/
├── src/
│   ├── acp/              # ACP 客户端
│   │   ├── client.py     # 主客户端
│   │   ├── protocol.py   # JSON-RPC 消息定义
│   │   └── permissions.py # 权限策略
│   ├── workspace/        # Workspace 管理
│   │   ├── manager.py
│   │   └── hooks.py      # after_create, before_run, after_run
│   ├── linear/           # Linear 集成
│   │   ├── client.py     # GraphQL 或 REST 封装
│   │   └── models.py     # Issue 等数据模型
│   └── cli.py            # maestro run / maestro list 等
├── config/
│   └── maestro.yaml      # 工作流配置
├── workspaces/           # 默认 workspace 根目录
└── tests/
```

## 0.3 详细实现

### 0.3.1 ACP 客户端

**输入**：`cwd`、`prompt`、`mcp_servers`（可选）、`permission_policy`

**输出**：`stop_reason`、`output_text`、`session_id`

**实现步骤**：

1. **Spawn 进程**
   - 命令：`agent acp` 或 `agent --api-key $CURSOR_API_KEY acp`
   - `stdio: ["pipe", "pipe", "inherit"]`，stdout 用于协议
   - `cwd` 设为 workspace 路径

2. **握手流程**（参考 [Cursor ACP 文档](https://cursor.com/docs/cli/acp)）
   ```
   initialize → authenticate(cursor_login) → session/new → session/prompt
   ```

3. **消息处理**
   - 使用 newline-delimited JSON 解析 stdout
   - 维护 `pending` map 处理 request/response 配对
   - 处理 `session/request_permission`：根据 `permission_policy` 返回 `allow-once` / `allow-always` / `reject-once`
   - 处理 `session/update`：提取 `agent_message_chunk` 用于流式输出

4. **错误处理**
   - 进程退出非 0：包装为 `AcpProcessError`
   - 超时：可配置 `turn_timeout_ms`，超时则 kill 进程

**验收**：单元测试 mock 进程，验证握手和 prompt 发送；集成测试需真实 `agent` 和 `CURSOR_API_KEY`。

---

### 0.3.2 Workspace Manager

**输入**：`issue_identifier`、`workspace_root`

**输出**：`Workspace(path, created_now)`

**实现步骤**：

1. **Workspace Key 生成**
   - 规则：`identifier` 中非 `[A-Za-z0-9._-]` 的字符替换为 `_`
   - 路径：`{workspace_root}/{workspace_key}/`

2. **创建与复用**
   - 若目录不存在：`mkdir -p`，设置 `created_now=True`
   - 若已存在：直接返回，`created_now=False`

3. **Hooks**（可选，Phase 0 可简化）
   - `after_create`：仅在 `created_now=True` 时执行，如 `git clone` 或 `cp -r` 模板
   - `before_run`：每次执行前，如 `git pull`
   - `after_run`：每次执行后，如日志归档
   - 超时：默认 60s，可配置

4. **安全校验**
   - 确保 `workspace_path` 在 `workspace_root` 下
   - 拒绝路径穿越（`..`）

**验收**：测试 workspace 路径确定性、创建/复用逻辑、hook 执行顺序。

---

### 0.3.3 Linear 客户端

**输入**：`LINEAR_API_KEY`、`project_slug` 或 `team_id`

**输出**：`Issue` 列表、单个 `Issue`、更新后的 `Issue`

**实现步骤**：

1. **API 选择**
   - 方案 A：Linear GraphQL API（与 Symphony 一致）
   - 方案 B：Linear REST API（若已有 SDK）
   - 建议：GraphQL，便于与 Symphony 查询结构对齐

2. **必需操作**
   - `fetch_issue(id)`：获取单个 issue 详情
   - `fetch_issues(project, state)`：获取候选 issue 列表
   - `update_issue(id, state, ...)`：更新状态、添加评论等

3. **数据模型**（与 [maestro-architecture.md](../maestro-architecture.md) 对齐）
   ```python
   @dataclass
   class Issue:
       id: str
       identifier: str  # 如 ABC-123
       title: str
       description: str | None
       state: str
       priority: int | None
       labels: list[str]
       blocked_by: list[BlockerRef]
       url: str | None
   ```

4. **状态映射**
   - `active_states`: `["Todo", "In Progress"]`（可配置）
   - `terminal_states`: `["Done", "Cancelled", "Closed"]`（可配置）

**验收**：集成测试用真实 Linear 项目，验证拉取和更新。

---

### 0.3.4 端到端串联

**CLI 设计**：

```bash
maestro run <issue_id|identifier>   # 执行单个 issue
maestro list [--project X] [--state In Progress]  # 列出候选 issue
maestro workspace show <issue_id>   # 查看 workspace 路径
```

**`maestro run` 流程**：

1. 从 Linear 拉取 issue
2. Workspace Manager 创建/获取 workspace
3. 构建 prompt：`你正在处理 Linear issue {{ identifier }}: {{ title }}\n\n{{ description }}`
4. ACP 客户端 spawn，传入 `cwd=workspace.path`，发送 prompt
5. 等待完成，解析 `stop_reason`
6. 可选：根据结果更新 Linear 状态（如 `Human Review`）或添加评论

**配置**（`maestro.yaml`）：

```yaml
linear:
  api_key: $LINEAR_API_KEY
  project_slug: my-project
  active_states: [Todo, In Progress]
  terminal_states: [Done, Cancelled, Closed]

workspace:
  root: ./workspaces

acp:
  command: agent acp
  permission_policy: allow-always  # 自动化场景
  turn_timeout_ms: 3600000
```

## 0.4 验收清单

- [ ] `maestro run ABC-123` 能在独立 workspace 中执行 Cursor，并输出 agent 回复
- [ ] `maestro list` 能列出 Linear 项目中 In Progress 的 issue
- [ ] Workspace 路径符合 `{root}/{sanitized_identifier}/`
- [ ] 配置支持环境变量 `$VAR` 展开

## 0.5 风险与缓解

| 风险 | 缓解 |
|------|------|
| Cursor CLI 未安装或版本不兼容 | 启动时检查 `agent --version`，给出明确错误提示 |
| LINEAR_API_KEY 无效 | 首次调用时验证，失败时提示配置 |
| ACP 协议变更 | 锁定文档版本，增加协议版本检测 |

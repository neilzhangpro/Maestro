# Phase 2：能力增强

> 目标：在 ACP 会话中加载 SKILL 和 MCP，使 Cursor agent 能按预设流程工作并调用 Linear 等外部工具。

## 2.1 阶段目标

- [ ] 在 ACP `session/new` 中传入 MCP 配置，使 agent 能调用 Linear MCP 工具
- [ ] 在 workspace 或全局配置中加载 SKILL，指导 agent 执行「编码→测试→提交→CI」流程
- [ ] 验证 agent 能通过 MCP 更新 Linear 评论、状态等

## 2.2 依赖

- Phase 0：ACP 客户端、Workspace Manager
- Phase 1：编排流程（可选，Phase 2 可独立验证）

## 2.3 详细实现

### 2.3.1 MCP 传递

**目标**：Cursor ACP 启动时，agent 能使用 Linear MCP 的 `list_issues`、`get_issue`、`save_issue`、`save_comment` 等工具。

**实现步骤**：

1. **MCP 配置格式**
   - 参考 [Cursor MCP 文档](https://cursor.com/docs/mcp) 和 ACP `session/new` 的 `mcpServers` 参数
   - 典型结构：
   ```json
   {
     "mcpServers": {
       "linear": {
         "command": "npx",
         "args": ["-y", "@linear/mcp-server-linear"],
         "env": { "LINEAR_API_KEY": "${LINEAR_API_KEY}" }
       }
     }
   }
   ```
   - 需确认 Cursor CLI 实际支持的 MCP 配置格式（可能为 stdio 或 SSE）

2. **配置来源**
   - `maestro.yaml` 中定义 `mcp_servers`，支持环境变量展开
   - ACP 客户端在 `session/new` 时传入

3. **验证**
   - 在 prompt 中明确要求：「请使用 Linear 工具更新当前 issue 的评论，附上 PR 链接」
   - 检查 agent 是否成功调用并写入 Linear

**注意事项**：
- Linear MCP 可能需 `LINEAR_API_KEY`，需在 spawn 时注入 env
- 若 Cursor CLI 的 MCP 配置方式与预期不符，需查阅最新文档或联系 Cursor 支持

### 2.3.2 SKILL 加载

**目标**：agent 按 SKILL 描述的流程执行，如「先写代码，再写测试，最后提交并跑 CI」。

**实现步骤**：

1. **SKILL 内容**
   - 在 `maestro/skills/` 或目标仓库的 `.cursor/skills/` 下创建 `maestro-workflow.md`：
   ```markdown
   # Maestro 标准开发流程

   处理 Linear issue 时，请按以下顺序执行：
   1. 阅读 issue 描述，理解需求
   2. 编写或修改代码
   3. 编写或补充测试
   4. 运行测试确保通过
   5. 提交代码并创建 PR
   6. 使用 Linear 工具更新 issue，添加 PR 链接和简要说明
   ```

2. **加载方式**（需验证 Cursor CLI 支持）
   - 方案 A：通过 `session/new` 的 `rules` 或类似参数传入 SKILL 路径
   - 方案 B：在 workspace 的 `.cursor/rules/` 下放置规则，Cursor 自动加载
   - 方案 C：在 prompt 中内联 SKILL 内容

3. **推荐**
   - 若 A/B 不可用，采用方案 C：在构建 prompt 时，将 SKILL 内容拼接到 system 或 user prompt 前部

4. **多 SKILL 支持**
   - 支持配置 `skills: [maestro-workflow, project-specific]`，按顺序合并

### 2.3.3 配置示例

```yaml
# maestro.yaml

acp:
  command: agent acp
  permission_policy: allow-always
  turn_timeout_ms: 3600000

  mcp_servers:
    linear:
      command: npx
      args: ["-y", "@linear/mcp-server-linear"]
      env:
        LINEAR_API_KEY: $LINEAR_API_KEY

  skills:
    - maestro/skills/maestro-workflow.md
    # - .cursor/skills/project-specific.md  # 可选，从 workspace 加载
```

### 2.3.4 与编排的集成

- `execute_task` 节点在调用 ACP 时，传入：
  - `cwd`: workspace 路径
  - `mcp_servers`: 从配置读取
  - `prompt`: issue 上下文 + 当前任务 + SKILL 内容（若未通过 rules 加载）

## 2.4 验收清单

- [ ] ACP 会话中 agent 能成功调用 Linear MCP 的 `save_issue` 或 `save_comment`
- [ ] agent 行为符合 SKILL 描述的流程顺序
- [ ] 配置支持多个 MCP 和多个 SKILL

## 2.5 风险与缓解

| 风险 | 缓解 |
|------|------|
| Cursor CLI MCP 配置方式不明确 | 查阅 ACP 协议和 Cursor 源码，或先用 prompt 内联 MCP 调用说明 |
| SKILL 与 rules 加载机制不清 | 优先用 prompt 内联，后续再优化 |
| MCP 服务启动失败 | 增加健康检查，失败时降级为「仅 prompt 指导」 |

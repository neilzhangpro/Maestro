# Phase 3：生产就绪

> 目标：支持长时间运行、断点恢复、Docker 部署和基础监控，使 Maestro 可在生产环境中稳定运行。

## 3.1 阶段目标

- [ ] LangGraph 使用 checkpoint 持久化，支持进程重启后恢复
- [ ] 支持 Docker / Docker Compose 部署
- [ ] 环境隔离增强（可选 Docker 或 VM）
- [ ] 基础监控与告警（日志、指标、健康检查）

## 3.2 依赖

- Phase 0、1、2 已完成

## 3.3 详细实现

### 3.3.1 Checkpoint 持久化

**目标**：LangGraph 状态持久化到 Redis 或 Postgres，进程重启后可从上次中断处继续。

**实现步骤**：

1. **选择 Checkpointer**
   - LangGraph 支持 `SqliteSaver`、`PostgresSaver`、`RedisSaver` 等
   - 生产建议：Postgres（若已有）或 Redis

2. **配置**
   ```python
   from langgraph.checkpoint.postgres import PostgresSaver
   # 或
   from langgraph.checkpoint.redis import RedisSaver

   checkpointer = PostgresSaver.from_conn_string(DATABASE_URL)
   graph = workflow.compile(checkpointer=checkpointer)
   ```

3. **线程/进程安全**
   - 每个 run 使用唯一 `thread_id`（如 `issue_id` + `run_id`）
   - 并发 run 时确保 thread_id 不冲突

4. **恢复流程**
   - 启动时查询「未完成」的 run（如 `linear_updated=False` 且无 `error`）
   - 调用 `graph.invoke(None, config={"configurable": {"thread_id": run_id}})` 从 checkpoint 恢复

### 3.3.2 Docker 部署

**Dockerfile 要点**：

- 基础镜像：`python:3.11-slim` 或 `node:20`（若后端用 Node）
- 安装 Cursor CLI：需确认 Cursor 是否提供 headless 安装包；若无，可能需挂载 host 的 `agent` 二进制
- 安装依赖：`pip install -r requirements.txt` 或 `npm ci`
- 暴露端口：Dashboard API（如 3001）、可选健康检查端口

**Docker Compose 示例**：

```yaml
services:
  maestro:
    build: .
    ports:
      - "3001:3001"
    environment:
      - LINEAR_API_KEY=${LINEAR_API_KEY}
      - CURSOR_API_KEY=${CURSOR_API_KEY}
      - DATABASE_URL=postgresql://...
    volumes:
      - ./workspaces:/app/workspaces
      - /usr/local/bin/agent:/usr/local/bin/agent  # 挂载 host 的 agent
    depends_on:
      - redis

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

**注意**：Cursor CLI 的 license 和认证在容器内可能需额外配置，需验证无头环境支持。

### 3.3.3 环境隔离增强

**可选方案**：

1. **进程隔离**（Phase 0 已具备）
   - 每 issue 独立 workspace，`agent acp` 的 cwd 隔离

2. **Docker 隔离**
   - 每个 run 在独立容器中执行 `agent acp`
   - 需要 Docker-in-Docker 或 Kubernetes Job
   - 复杂度较高，可作为后续优化

3. **资源限制**
   - 使用 `ulimit`、`cgroups` 限制 CPU/内存
   - 防止单个 run 耗尽资源

### 3.3.4 监控与可观测性

**日志**：

- 结构化日志（JSON），包含 `issue_id`、`run_id`、`node`、`event`
- 输出到 stdout，由 Docker/ K8s 采集

**指标**（可选）：

- 使用 Prometheus 或 OpenTelemetry
- 指标示例：`maestro_runs_total`、`maestro_run_duration_seconds`、`maestro_active_runs`

**健康检查**：

- `GET /health`：检查 Linear API 连通性、Redis/Postgres 连通性
- 返回 200 表示可接受新任务

**告警**（可选）：

- 连续 N 次 run 失败 → 告警
- Linear API 不可用 → 告警

### 3.3.5 配置示例

```yaml
# maestro.yaml

checkpoint:
  backend: postgres  # postgres | redis | sqlite
  url: $DATABASE_URL  # 或 Redis URL

deployment:
  max_concurrent_runs: 5
  run_timeout_sec: 7200  # 2h

monitoring:
  log_level: info
  metrics_enabled: true
  health_check_path: /health
```

## 3.4 验收清单

- [ ] 进程重启后，未完成的 run 能从 checkpoint 恢复
- [ ] Docker Compose 能一键启动 Maestro + Redis
- [ ] `GET /health` 返回 200 且包含依赖状态
- [ ] 日志包含必要的上下文字段

## 3.5 风险与缓解

| 风险 | 缓解 |
|------|------|
| Cursor 在 Docker 中无法运行 | 使用 host 挂载 agent，或等待 Cursor 官方 headless 支持 |
| Checkpoint 数据膨胀 | 定期清理已完成 run 的 checkpoint；设置 TTL |
| 多实例部署 | 使用分布式锁（Redis）防止同一 issue 被多实例重复调度 |

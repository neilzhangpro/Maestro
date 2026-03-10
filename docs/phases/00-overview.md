# Maestro 分阶段实现总览

> 本文档为各阶段实现文档的索引，详细内容见各阶段文档。

## 阶段划分

| 阶段 | 文档 | 目标 | 预估工作量 |
|------|------|------|------------|
| Phase 0 | [phase-0-foundation.md](./phase-0-foundation.md) | 基础设施：ACP 客户端、Workspace、Linear | 2-3 周 |
| Phase 1 | [phase-1-orchestration.md](./phase-1-orchestration.md) | 编排与工作台：LangGraph、Dashboard | 3-4 周 |
| Phase 2 | [phase-2-enhancements.md](./phase-2-enhancements.md) | 能力增强：SKILL、MCP 传递 | 1-2 周 |
| Phase 3 | [phase-3-production.md](./phase-3-production.md) | 生产就绪：持久化、隔离、监控 | 2-3 周 |

## 依赖关系

```
Phase 0 (基础设施)
    │
    ├──► Phase 1 (编排与工作台) ── 依赖 Phase 0
    │
    ├──► Phase 2 (能力增强) ───── 依赖 Phase 0, 1
    │
    └──► Phase 3 (生产就绪) ───── 依赖 Phase 0, 1, 2
```

## 验收标准速查

- **Phase 0**：能通过命令行将 Linear issue 转为 prompt，spawn Cursor ACP 在独立 workspace 中执行，并更新 Linear 状态
- **Phase 1**：Dashboard 展示任务、手动触发后 LangGraph 完成 parse→plan→execute→update 全流程
- **Phase 2**：ACP 会话能加载 SKILL 和 MCP，agent 可调用 Linear 工具
- **Phase 3**：服务重启可恢复、支持 Docker 部署、具备基础监控

# ADR-0021：AgentDefinition 不可变快照与恢复绑定

- **状态**：Accepted
- **日期**：2026-08-15
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-15-010](../CHANGELOG.md#e2026-08-15-010)

## 背景

v0.7.7 可以从 Workflow snapshot 恢复编排结构，但应用重启后仍必须重新注册其中引用的 `AgentDefinition`。这意味着系统虽然保存了 Run、Step、ToolExecution 和 Workflow 定义，却没有保存执行所依赖的 System Prompt、ModelConfig、Tool 声明和运行限制。若 Agent 定义在崩溃后发生变化，恢复过程还可能意外使用新定义，破坏可重复性。

## 决策

- schema 8 新增内容寻址的 `agent_definitions` 表。
- AgentDefinition 使用规范化 JSON 表示，并以 SHA-256 checksum 作为不可变快照标识。
- `register_agent()` 和创建 Run 时持久化定义快照。
- 每个新 Agent Run 在 metadata 和 `runs.agent_definition_checksum` 中绑定确切快照。
- Workflow snapshot 的每个 Step 保存对应 `agent_definition_checksum`。
- 恢复 Agent Run 时优先加载 Run 绑定的快照，而不是当前注册表中的同名定义。
- 恢复 Workflow 时从 Step checksum 重建 AgentDefinition；应用不再需要重新注册定义对象。
- Tool Handler 和 Provider 仍属于进程能力，不能序列化进数据库；缺失时返回明确 `AgentDefinitionUnavailable`，不静默降级。
- Runtime Doctor 对仍处于活动状态但缺少定义快照的历史 Run 给出 attention。

## 影响

### 优点

- 进程重启后可仅依赖 SQLite 中的 AgentDefinition 快照恢复普通 Run 和 Workflow。
- 同名 Agent 后续升级不会改变历史 Run 的 System Prompt、ModelConfig 和执行上限。
- Crash Matrix 可以在恢复进程中不重新注册 AgentDefinition，直接验证模型、Approval、Tool 和 Workflow 恢复。
- 内容寻址避免重复保存完全相同的定义。

### 代价

- Tool Handler 代码和 Model Provider 客户端仍必须由应用进程提供。
- AgentDefinition 变更会产生新的 checksum；历史快照默认保留以保证审计与恢复。
- 旧 schema Run 没有 checksum，只能继续依赖应用注册或由 Doctor 提示人工处理。

## 被放弃的方案

- 仅保存 Agent 名称：无法保证恢复时使用相同 Prompt 和 ModelConfig。
- 始终使用最新同名 Agent：会让历史 Run 在恢复后改变执行语义。
- 序列化 Python Handler/Callable：不安全、不稳定，也不可跨版本可靠恢复。

## 后续约束

- 任何影响 AgentDefinition 规范化字段或 checksum 算法的变更都必须新增迁移和兼容测试。
- 不得删除仍被非终态 Run 或 Workflow snapshot 引用的定义快照。
- Sandbox 和分布式 Worker 必须继续使用快照 checksum 绑定执行定义。

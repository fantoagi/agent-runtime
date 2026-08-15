# ADR-0011：Context Window、Session 与 Scoped Long-term Memory 边界

- **状态**：Accepted
- **日期**：2026-08-15
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-15-001](../CHANGELOG.md#e2026-08-15-001)

## 背景

Agent 执行历史会随着模型轮次和 Tool Result 增长，但模型上下文窗口有限。Runtime 既要保留完整的可恢复执行事实，又要控制每次模型请求的输入规模；同时，多个 Run 需要在明确边界内共享可检索信息，不能把所有对话自动变成永久且全局可见的记忆。

## 决策

### 1. Context 与 Checkpoint 分离

Checkpoint 保存完整的恢复事实；`ContextBuilder` 只在模型调用前生成受 token budget 约束的输入副本。Context 裁剪不修改 Checkpoint，也不删除 Event、Step 或 ToolExecution。

### 2. Tool Call 与 Tool Result 组不可拆分

Assistant Tool Call 与对应 Tool Result 作为一个消息组选择。尚未获得完整 Tool Result 的调用组必须保留，避免模型看到孤立的 Tool Result 或缺失结果的 Tool Call。

### 3. 大 Tool Result 进入 Artifact Store

超过阈值的 Tool Result 将完整内容写入 Artifact；SQLite、ToolExecution 和 Checkpoint 保存 Artifact 引用与预览。这样保留 provenance，同时避免大文本持续挤占模型上下文和数据库记录。

### 4. Session 是 Run 的显式容器

Session 与 Run 通过 `session_runs` 持久化关联。一个 Run 当前最多属于一个 Session；Child Run 继承 Parent 的 `session_id`，使同一 Workflow 在相同 Session 边界内检索记忆。

### 5. Memory 不等于自动保存全部对话

Memory 必须由应用或用户显式调用 `remember()` 创建。Memory Record 保存 content、Scope、source Run、source Trace、TTL、软删除时间和 metadata；Runtime 不会默认把每条对话永久化。

### 6. 只允许 session 与 agent Scope

- `session` Memory 仅对指定 Session 可见。
- `agent` Memory 仅对指定 Agent 可见，可跨 Session 使用。
- v0.7 不提供 global Scope，避免无边界跨用户和跨 Agent 泄漏。

### 7. MemoryStore 协议与 SQLite FTS5 首个实现

Runtime 依赖 `MemoryStore` 协议；`SQLiteStore` 是首个实现，使用 FTS5 提供确定性关键词检索，并在查询时强制 Scope、软删除和过期过滤。向量检索可以作为后续实现加入，而不改变 Runtime 的 Memory 边界。

### 8. Search、Compaction 与 Artifactization 必须进入 Event Log

Memory Search、Context Build/Compaction 和大 Tool Result Artifact 化都写入 Runtime Event Log，记录预算、选择结果、Memory ID 和 Artifact provenance，使模型输入构造过程可审计、可测试和可派生 Metrics。

## 影响

### 优点

- 完整恢复事实与有限模型输入互不冲突。
- Context 裁剪行为确定、可测试且可追溯。
- Tool Call 与 Tool Result 不会被错误拆分。
- Session 与 Agent Memory 具有明确隔离边界。
- Memory 可以追溯到 source Run 与 source Trace。
- 可通过替换 MemoryStore 增加其他检索实现，而不侵入 Runtime Kernel。

### 代价

- Provider-neutral token 估算不是具体模型的精确 tokenizer。
- 确定性 Summary 只保留结构化摘要，不提供模型生成的语义压缩。
- SQLite schema 增加 Session、Memory 和 FTS 表，迁移与清理逻辑更复杂。
- Artifact 引用降低上下文体积，但模型不能直接读取被转存的全部 Tool Result。

## 被放弃的方案

### 直接截断最旧消息

该方案可能拆散 Tool Call 和 Tool Result，并且无法解释被省略了什么。

### 自动永久保存所有 Run 对话

该方案会制造噪音、隐私和数据治理风险，因此改为显式 `remember()`。

### 增加 global Memory Scope

当前没有租户、权限和数据治理能力，global Scope 风险过高，因此 v0.7 明确不支持。

### v0.7 同时接入向量数据库

为了先稳定 Scope、生命周期、事件和接口契约，首版只采用 SQLite FTS5；Embedding 与向量数据库留给后续演进。

## 后续约束

- 修改 token budget、消息分组或 Summary 语义时，必须更新测试、架构文档和 ADR。
- 新增 Memory Scope 必须先定义权限、隔离和删除语义。
- Memory 检索实现必须保留 Scope、TTL、软删除、source provenance 和事件记录。
- 任何自动创建 Memory 的能力都必须提供明确策略、审计事件和用户可删除机制。

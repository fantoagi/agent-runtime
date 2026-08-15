# ADR-0019：Runtime Doctor 采用只读诊断模型

- **状态**：Accepted
- **日期**：2026-08-15
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-15-008](../CHANGELOG.md#e2026-08-15-008)

## 背景

长期运行需要快速判断 SQLite、Run、Event、Approval、ToolExecution 和 Workflow snapshot 是否需要处理。自动修复可能在缺少业务上下文时覆盖有效状态或重复副作用。

## 决策

`RuntimeDoctor` 只读取持久化事实，输出 `ok`、`attention_required` 或 `unhealthy`。它检查 SQLite health/schema/foreign_keys、非终态 Run、UNKNOWN/Running ToolExecution、Pending Approval、Event sequence、孤儿记录和缺失 Workflow snapshot。CLI 和 HTTP 共用同一报告模型。

## 影响

### 优点

- 诊断可重复执行，不改变系统状态。
- 自动化和人工运维使用同一结构化结果。
- 不会绕过 Approval 或 UNKNOWN 确认。

### 代价

- Doctor 只能指出问题，不能一键修复。
- 更深入的外部系统检查仍需应用实现。

## 被放弃的方案

启动时自动修复所有异常，可能把未知副作用误判为成功或失败，因此不采用。

## 后续约束

任何自动修复能力必须独立设计、显式授权、记录审计事件，并不得改变 Doctor 的只读语义。

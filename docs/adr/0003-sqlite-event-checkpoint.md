# ADR-0003：本地 MVP 使用 SQLite Event Log 与 Checkpoint

- **状态**：Accepted
- **日期**：2026-08-11
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-11-001](../CHANGELOG.md#e2026-08-11-001)

## 背景

MVP 需要零外部服务即可运行，同时保留 Run 查询、事件审计、审批和恢复能力。纯内存状态无法满足这些需求，而直接引入 PostgreSQL、消息队列和对象存储会提高首版部署成本。

## 决策

使用 SQLite 保存 Run、Event、Checkpoint 和 Approval，启用 WAL 与 foreign keys。每个 Run 的事件使用唯一、单调递增 sequence。大型或文件型产物通过独立 `ArtifactStore` 保存，数据库只承担结构化状态。

Checkpoint 保存恢复 Agent 循环所需的消息历史、步骤数和工具调用计数。

## 影响

### 优点

- 无外部基础设施即可持久化运行。
- 单机开发、测试和事件回放简单。
- Repository 边界允许后续替换存储实现。

### 代价

- SQLite 不适合高写并发和多节点共享写入。
- 当前 schema 没有显式版本与迁移框架。
- Event、状态和外部工具副作用尚不具备分布式事务语义。

## 被放弃的方案

- 纯内存存储：无法恢复和审计。
- MVP 直接使用 PostgreSQL 和对象存储：运维成本超过当前需求。

## 后续约束

修改表结构、事件 sequence、Checkpoint 内容或恢复语义时，必须提供迁移策略并更新 ADR。引入分布式 Worker 前必须补充租约、幂等和并发写入设计。

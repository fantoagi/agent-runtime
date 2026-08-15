# ADR-0015：Runtime Shutdown、SQLite Durability 与进程恢复

- **状态**：Accepted
- **日期**：2026-08-15
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-15-006](../CHANGELOG.md#e2026-08-15-006)

## 背景

长期运行需要拒绝新工作、排空任务、协调崩溃遗留状态、关闭资源并安全升级历史数据库。

## 决策

Runtime 提供幂等 shutdown 和 async context manager；启动只协调状态，不自动重放副作用。SQLite 使用 WAL、FULL、busy timeout、quick_check、锁重试和事务 sequence；迁移保存 checksum 且只向前。

## 影响

### 优点

进程终止后可识别遗留 Run，多 Store sequence 唯一，历史 schema 可安全升级。

### 代价

FULL 增加写入成本，恢复需要显式 resume。

## 被放弃的方案

直接终止、自动重跑 running 工作或允许部分迁移都会造成损坏或重复副作用。

## 后续约束

状态机、shutdown 顺序、schema、checksum、Workflow 快照和 UNKNOWN 恢复变化必须新增 ADR。

# ADR-0018：进程强杀恢复合同与 Workflow Snapshot 恢复

- **状态**：Accepted
- **日期**：2026-08-15
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-15-008](../CHANGELOG.md#e2026-08-15-008)

## 背景

线程内取消测试无法证明操作系统强杀后的 SQLite 状态和外部副作用语义。Workflow 虽保存 snapshot，但此前 `Runtime.resume()` 仍要求调用方重新提供原 Python 对象，无法形成独立恢复合同。

## 决策

使用独立子进程、durable barrier 和 `process.kill()` 建立 Crash Matrix，至少覆盖模型请求、side-effect Tool、Approval 和部分完成 Workflow。Runtime 从 schema 5 保存的规范化 snapshot 重建 Sequential/Parallel Workflow，并依靠 delegation key 复用 Child Run。

## 影响

### 优点

- 测试覆盖真实进程边界，而非模拟异常。
- 已完成 Child 和外部副作用不会因 Parent 恢复而重复。
- Windows 与 Linux 使用同一恢复合同。

### 代价

- Crash 测试比普通单元测试更慢。
- 应用重启后仍需注册 snapshot 引用的 AgentDefinition。
- 任意 Python Workflow 控制流不能被安全反序列化。

## 被放弃的方案

只使用 mock exception、序列化 Python callable 或自动恢复所有 RUNNING 工作都无法提供安全且可移植的行为。

## 后续约束

PR 至少在 Windows/Linux Python 3.13 各执行一次 smoke；Nightly 重复完整矩阵。新增恢复状态必须进入 Crash Matrix。

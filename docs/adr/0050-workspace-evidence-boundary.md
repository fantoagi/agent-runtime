# ADR-0050：Workspace 修改使用快照与写入结果建立验证证据边界

- **状态**：Accepted
- **日期**：2026-08-22
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-22-006](../CHANGELOG.md#e2026-08-22-006)

## 背景

本地 Coding Agent 运行时可能面对用户预先存在的 dirty workspace。仅依赖最终 `git status` 或验证命令成功，无法区分用户已有改动与本轮 Agent 写入，也无法证明验证命令之后目标文件确实发生了变化。

## 决策

1. `write_text_file` 返回 `before_sha256`、`after_sha256`、`changed` 和 `created`，把实际写入事实持久化。
2. Completion Evidence 保存第一次写入前和最后一次写入后的 Git status 快照，并分类 tracked modified、untracked、deleted 和 renamed。
3. Agent 的 `changed_files` 仍只来源于成功写入 Tool，不把 workspace 快照中的用户 dirty 文件归因给 Agent。
4. 验证命令成功不能单独证明修改完成；当写入结果明确 `changed=False` 时，不得把任务标记为 `verified`。
5. 快照只作为非归因型事实展示，不自动 stage、commit、删除或恢复用户文件；保持现有 Tool Handler 签名和旧证据读取兼容。

## 影响

### 优点

- 可从 durable evidence 复现修改前后 Workspace 状态。
- 用户已有 dirty 文件不会被错误列入本轮 Agent 产物。
- no-op 写入不会因为测试命令成功而伪装成真实修改。

### 代价

- Git status 可能产生额外的只读验证调用和较大的报告字段。
- 无 Git Workspace 只能保留写入 hash 与 validation 事实，不能提供 tracked/untracked 分类。

## 被放弃的方案

- 只依据最终 `git diff` 判定 Agent 修改：无法覆盖未跟踪文件、删除和重命名，也无法隔离用户已有 dirty 状态。
- 只依据模型最终文本或验证命令退出码判定 verified：模型文本和命令成功都不能证明目标文件发生了本轮修改。

## 后续约束

任何新增修改型 Tool 都必须定义 before/after 事实、no-op 语义和用户 dirty workspace 归因边界；任何新的 Completion 判定都必须说明其依赖的写入和验证时间顺序。

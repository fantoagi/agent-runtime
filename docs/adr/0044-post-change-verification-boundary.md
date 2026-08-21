# ADR-0044：修改后的验证证据必须位于最后一次写入之后

- **状态**：Accepted
- **日期**：2026-08-21
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-21-001](../CHANGELOG.md#e2026-08-21-001)

## 背景

v0.8.20 已区分 tracked diff 和 untracked status，但 Acceptance Metrics 仍从整个 Run 统计 `git_diff`、`git_status` 和 validation。这样一条 Run 如果先运行 pytest、再写文件、最后只调用 `git_diff`，就可能因为写入前的 pytest 成功而把代码修改误标为 `verified`。同样，写入前的 Git 检查不能证明最后一次写入后的 Workspace 状态。

## 决策

1. 以 durable ToolExecution 中最后一次成功副作用写入为证据边界。
2. Acceptance 的 `diff_inspected`、`git_status_inspected`、`validation_attempts` 和 `validation_successes` 只从该写入之后的执行计算。
3. Completion Policy 与 Acceptance 继续使用相同的 post-change 语义；写入前的检查不满足修改完成证据。
4. 如果 Run 没有成功写入，修改验证状态仍为 `not_required`；只读任务不被此规则影响。
5. 不改变 Tool Handler、Provider、Event schema 或 SQLite schema；该版本只修正派生指标和 verified 判定的时间边界。

## 影响

### 优点

- 不会把写入前的成功测试错误地当成修改后的验证。
- Git diff/status 和 validation 的证据时间关系可从 durable ToolExecution 直接复现。
- Acceptance 与 Runtime Completion 的 verified 语义更加一致。

### 代价

- 某些模型可能需要在最后一次文件修改后重新运行一次窄范围测试，即使此前已经运行过测试。
- Acceptance 报告中的 validation_attempts 可能比历史版本更少，因为写入前的命令不再计入。
- 该规则仍不判断业务逻辑是否正确，只判断是否存在可追溯的 post-change 证据。

## 被放弃的方案

### 统计整个 Run 的所有验证命令

无法证明验证命令覆盖最后一次文件修改，容易产生时间顺序错误。

### 只要求最终答案声称已验证

模型自由文本不是执行事实，不能替代 durable ToolExecution 的 exit code 和顺序。

## 后续约束

- 任何新的修改型 Tool 或 Completion 指标都必须明确其写入时间边界。
- 若一次 Tool 原子修改多个文件，所有文件共享同一次 ToolExecution 的 post-change 边界。
- 未来若支持多阶段验证或增量修改，必须在 ADR 中定义每个验证阶段对应的写入边界。

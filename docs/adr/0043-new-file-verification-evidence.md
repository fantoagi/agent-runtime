# ADR-0043：新建文件验证使用 Git status 证据

- **状态**：Accepted
- **日期**：2026-08-20
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-20-002](../CHANGELOG.md#e2026-08-20-002)

## 背景

v0.8.19 的完整真实模型基线使用 `deepseek-v4-flash` 执行 5 个 Case × 3 轮，15/15 attempts 通过且 failed assertions 为 0。继续检查 `approval-lifecycle` 的 durable ToolExecution 和隔离 Git Workspace 后发现，模型创建的 `RESULT.txt` 是 untracked 文件，而默认 `git diff` 只报告 tracked changes，并返回 `No tracked differences.`。旧 Completion/Acceptance 逻辑只判断是否成功调用 `git_diff`，因此存在把尚未检查的新文件错误标记为 `verified` 的证据漏洞。

## 决策

1. 内置 `write_text_file` 的 durable result 兼容性增加布尔字段 `created`，在写入前依据目标是否存在确定。
2. `CodingCompletionPolicy` 在发现已完成的 `write_text_file(created=true)`、且 Runtime 注册了 `git_status` 时，要求最后一次写入后成功执行 `git_status`。
3. `CompletionEvidence` 增加 `git_status_required` 和 `git_status_inspected`；缺少证据时状态为 `unverified`，并保持现有的一次有界验证提醒。
4. `AcceptanceMetrics` 增加 `created_file_writes` 和 `git_status_inspected`，修改 Case 的 verified 判定与 Runtime Completion 语义一致。
5. `git_diff` 仍用于 tracked diff，代码文件仍要求 validation；`git_status` 只是补充未跟踪文件证据，不自动 stage、commit 或改变 Git 状态。
6. 只有 Runtime 实际提供 `git_status` 时才增加该要求，避免破坏未注册 Git Tool 的通用 SDK Runtime。

## 影响

### 优点

- 新建文件不会因为一次空的 `git diff` 就被错误标记为 verified。
- Completion、CLI durable evidence 和真实模型 Acceptance 使用相同的可追溯证据语义。
- `created` 保存在 Tool result 中，进程恢复和离线报告不需要重新猜测写入前状态。
- 规则与具体模型、答案文本和 Provider 无关。

### 代价

- 新建文件任务在标准本地 Runtime 中多需要一次只读 `git_status` Tool 调用。
- 旧 Run 的 `write_text_file` result 没有 `created`，不会被追溯性重分类。
- Git status 只能证明 Workspace 中存在未跟踪变化，不能判断新文件内容的业务正确性。

## 被放弃的方案

### 仅检查 `git_diff`

默认 Git 行为不会展示 untracked 文件，证据不完整。

### 自动执行 `git add` 后再检查 diff

这会改变用户 Workspace，超出只读 Git Tool 和本地 Approval 边界。

### 根据文件当前是否存在推断是否新建

完成后文件一定存在，无法可靠恢复写入前状态；进程重启或覆盖已有文件时还会产生误判。

## 后续约束

- 任何新增加的“创建文件”Tool 都必须显式持久化 created/created-path 语义，或提供等价 durable 证据。
- Completion 与 Acceptance 的 verified 规则必须保持一致。
- 若未来 Git Tool 支持 staged/untracked diff，需要通过新 ADR 明确是否替代当前 status 证据，不得隐式改变完成语义。

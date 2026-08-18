# ADR-0033：本地 Coding Run 使用一次性完成证据检查

- **状态**：Accepted
- **日期**：2026-08-18
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-18-002](../CHANGELOG.md#e2026-08-18-002)

## 背景

标准本地 Agent 已能发现、读取、修改和验证 Workspace，但 Run 的终止条件仍是“模型返回无 Tool Call 的普通文本”。Prompt 会要求模型在修改后检查 diff 和运行测试，却不能阻止模型在缺少执行证据时过早声明完成。对本地 Coding Agent 而言，“模型说完成”与“持久化 Tool 事实证明完成”需要被区分。

## 决策

Runtime 构造函数增加可选 `CompletionPolicy`。普通 Runtime 默认不启用，保持原有无 Tool Call 即完成的行为；标准本地 Runtime 注入 `CodingCompletionPolicy`。

Policy 只从当前 Run 的持久化 `ToolExecution` 派生完成证据：

- 是否发生成功的 `write_text_file`、`replace_text` 或 `apply_patch`。
- 修改文件清单。
- 最后一次写入后是否成功调用 `git_diff`。
- 代码文件修改后是否运行可识别的验证命令，以及进程退出码是否为零。
- 是否存在失败或被拒绝的 Tool。

如果发生写入但缺少可用的 diff 或验证证据，Runtime 不立即把第一份普通文本作为最终结果，而是：

1. 将当前 Model Step、草稿回答和 Checkpoint 持久化。
2. 写入 `completion.verification_requested` Event。
3. 向同一个 Run 的模型上下文追加一条 Runtime system reminder。
4. 允许模型继续调用 Tool 或明确说明无法验证。
5. 每个 Run 最多提醒一次，防止无限自主循环。

最终结束时写入 `completion.evidence` Event。证据状态为 `read_only`、`verified` 或 `unverified`；它描述执行事实，不替代模型最终回答。

## 影响

### 优点

- 修改任务不再完全依赖 Prompt 自律来检查 diff 和验证。
- CLI、Learning Console、Eval 和后续 Task Summary 可以复用同一份持久化证据。
- 测试是否通过由 `run_process` 的退出码决定，模型不能仅靠文字伪造成功。
- 一次性提醒避免把本地 Runtime 变成无限自主循环。
- 普通 SDK/API Runtime 不启用 Policy 时保持兼容。

### 代价

- 一次缺证据的修改任务最多增加一个 Model Step，产生额外延迟和 token 消耗。
- 验证命令识别采用保守 allowlist，无法理解所有项目自定义命令。
- `git_diff` 不展示未跟踪文件内容；新文件仍需要模型结合 `git_status` 或文件读取确认。
- 第二次最终回答即使仍缺证据也允许结束，但会明确记录为 `unverified`。

## 被放弃的方案

- 所有无 Tool Call 响应都强制继续：会破坏普通问答和只读任务。
- 修改后必须运行完整测试套件：对没有测试、测试昂贵或用户明确跳过验证的任务不合理。
- 无限重复提醒直到验证成功：可能形成不可控循环并耗尽 step/token budget。
- 仅通过最终文本关键词判断是否验证：不能替代 ToolExecution 和退出码事实。
- 将完成证据写入新的 SQLite 表：当前 Event 已足够表达，不值得立即增加 schema migration。

## 后续约束

任何新的自动完成门禁都必须保持有界、可观察、可关闭，并且不能把模型声明当作命令成功事实。若未来增加用户可配置验证规则，应扩展 Policy，而不是把项目特定命令硬编码进 Runtime Kernel。`completion.evidence` Event 新增字段时必须保持向后兼容。

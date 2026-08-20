# ADR-0035：Interactive CLI 执行透明度基于 Durable Event 派生

- **状态**：Accepted
- **日期**：2026-08-19
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-19-004](../CHANGELOG.md#e2026-08-19-004)、[E2026-08-19-003](../CHANGELOG.md#e2026-08-19-003)、[E2026-08-19-002](../CHANGELOG.md#e2026-08-19-002)

## 背景

v0.8.9 已能以 append-only Markdown 展示模型回答，并用 compact/verbose 投影 Tool Event。但用户仍需从 Tool 名和原始参数自行判断 Agent 正在检查、修改还是验证；Approval 也缺少针对命令、工作目录、目标文件和修改范围的人类可读预览。任务结束时，模型自由文本与 Runtime 已持久化的变更、Git diff 和 validation 事实没有清晰分层。

这些问题属于 Interactive CLI Adapter 的可理解性，不应新增数据库字段、修改 Event schema，或让 CLI 自己成为新的执行事实来源。

## 决策

### 1. 执行阶段只从既有 Tool Event 派生

`EventRenderer` 根据 `tool.requested` 与 `approval.requested` 中已有的 Tool 名和参数，将当前动作投影为：

- `Inspecting workspace`：文件发现、搜索、读取和 Git status。
- `Editing workspace`：`write_text_file`、`replace_text` 和 `apply_patch`。
- `Verifying changes`：`git_diff` 或可识别的测试、静态检查命令。
- `Executing action`：不属于上述类别的 Tool。

阶段只在发生转换时 append 一行，不写入 Run、Session、Event Log 或 SQLite。真实顺序仍以 durable Event sequence 为准。Completion Policy 与 Renderer 必须复用同一个保守 validation classifier；项目内建检查脚本可以显式加入 allowlist，但不得把任意 Python 脚本都视为验证。

### 2. Approval 使用 Tool-aware 有界预览

Approval 卡片复用 `approval.requested` 中的 `arguments` 和 `authorization`：

- `run_process` 展示 argv、cwd、timeout、环境变量名称、sandbox/capability 信息，但 compact 视图不展示环境变量值。
- 文件写入展示目标路径和内容规模，不直接展开完整文件正文。
- 精确替换和批量 Patch 展示文件、编辑数量及有界 old/new 摘要，不展开完整 Patch。
- verbose 模式仍可附加有界 JSON，满足诊断需求。

审批结果由既有 `approval.resolved` Event 投影为 `Approved` 或 `Denied`，不引入自动批准或会话级权限缓存。

### 3. 最终任务摘要以 Completion Evidence 为事实来源

模型回答继续负责解释设计和结果；Renderer 将 durable `completion.evidence` 投影为独立结构化摘要，包括：

- `verified` / `unverified` 状态。
- 已变更文件。
- Git diff 是否检查。
- 实际执行的 validation command、exit code 和结果。
- 未满足条件、失败 Tool 和拒绝 Tool。

Run 的 step、Tool 数量和耗时来自最终 `AgentRun`。CLI 不根据模型文字猜测这些事实。

### 4. Approval 恢复必须保持同一 Run 的连续投影

Runtime Approval 是副作用 Tool 的唯一确认步骤。模型可以在 Tool Call 前解释风险，但不得先停止并要求一次额外口头确认。Shell 在写入 `approval.resolved` 后启动 `runtime.resume()`，等待 Run 离开瞬时 `waiting_for_approval`，再从最后 durable sequence 继续消费 Tool 结果、验证、模型回答和终态；不得因审批记录已清空而提前返回用户输入提示。

失败且没有成功写入的任务不改变 durable `read_only` Evidence 合同，但 CLI 必须投影为 `incomplete`，并明确显示没有应用变更。若同名 Tool 在当前轮后续成功，Renderer 可以从 durable Event 顺序将旧失败投影为 recovered；若之后再次失败，则最后失败仍为 unresolved。该派生不删除 Completion Evidence 中的历史失败事实，也不得从模型自由文本推断 clarification 状态。精确替换预览应聚焦实际差异附近，以 `- old` / `+ new` 形式有界展示。

### 5. 保持 append-only 与兼容边界

阶段、Approval 和最终摘要均只追加终端内容，不使用累计区域重绘。`--print` 继续只输出最终 `AgentRun.result`，不混入阶段、审批或结构化摘要。Runtime Kernel、Provider、Tool Handler、数据库和 Event schema 均保持不变。

## 影响

### 优点

- 用户能直接看出 Agent 正在检查、修改还是验证。
- 批准命令或文件变更前能理解影响范围和安全边界。
- 模型解释与 Runtime 事实分离，最终结果更可信且便于学习。
- 所有展示都可从既有持久化事实重新构建。
- 不需要数据库迁移或公共 Runtime API 变化。

### 代价

- 阶段是基于 Tool 名和已知验证命令的确定性分类，不是模型完整意图。
- 未识别的自定义 Tool 会显示为通用 `Executing action`。
- Approval 预览为了有界和避免泄露会省略完整正文、Patch 和环境变量值。
- 只读任务没有 `completion.evidence` 时不会额外显示变更摘要。

## 被放弃的方案

### 让模型输出计划和最终总结作为唯一来源

模型文本易遗漏实际 Tool 失败、拒绝、Git diff 或 validation 结果，不能替代 Runtime 持久化事实。

### 新增 `phase.changed` Event

当前阶段只用于终端可读性，已有 Tool Event 足以派生。新增 Event 会把 Adapter 视图写入 Runtime 协议，增加兼容负担。

### 在 Approval 中展开完整参数、文件正文或环境变量

信息最完整但会占满终端，并可能暴露敏感值。默认采用有界 Tool-aware 预览，完整诊断只在 verbose 与 durable Event 中按需查看。

## 后续约束

- 新 Tool 若要获得专用阶段或 Approval 预览，必须使用确定性、无副作用的 Renderer 映射。
- compact Approval 不得展示完整文件正文、完整 Patch 或环境变量值。
- 阶段展示不得写入 Runtime Event 或影响 Tool 执行顺序。
- 最终任务摘要不得从模型自由文本推断事实。
- `--print` 必须保持只输出最终结果。
- 所有终端展示继续遵循 append-only 合同。

# ADR-0034：Interactive CLI 采用缓冲式 Streaming Markdown 与分层展示模式

- **状态**：Accepted
- **日期**：2026-08-18
- **最近修订**：2026-08-19
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-19-001](../CHANGELOG.md#e2026-08-19-001)、[E2026-08-18-003](../CHANGELOG.md#e2026-08-18-003)

## 背景

Interactive CLI 原先把每个 `model.delta` 作为普通 Text 直接追加到终端。该方式能看到 token 流，但 Markdown 围栏、列表、标题和表格在流式过程中不会被解析，最终会把 ``` 等源标记原样留在屏幕上。Tool 参数与多行结果也直接占据较大区域，真实编码任务中很难快速区分模型结论、执行动作和诊断细节。

这些问题属于 Adapter 展示层，不应改变 Runtime Event、ToolExecution、Checkpoint 或 SQLite 恢复事实。

## 决策

### 1. 按 Assistant 内容段缓冲 Markdown

`EventRenderer` 将连续 `model.delta` 先合并为当前 Assistant 内容段。Renderer 在空行或 fenced code block 闭合时识别稳定 Markdown 前缀，并将该前缀顺序追加一次；未完成尾部继续留在 Buffer。遇到 Tool、Approval、完成证据或 Run 终态时结束当前内容段并刷新剩余内容。

TTY、非 TTY、重定向输出和 `--no-color` 使用同一 append-only 语义，不通过 Rich `Live` 或 ANSI 光标回退重写旧行。`--print` 不输出中间 delta 或 Tool 状态，只在 Run 完成后输出最终 `AgentRun.result`。

### 2. 默认 compact，按需 verbose

Interactive CLI 默认使用 `compact`：

- Tool 请求只显示 Tool 名称和经过工具感知的单行参数摘要。
- Tool started 事件默认隐藏。
- Tool 完成、失败和 Artifact 引用显示有界单行摘要。
- 文件正文、Patch 内容、环境变量和大段 Tool Result 不在默认视图展开。

用户可以通过 `agent-runtime chat --verbose` 或 Shell 内 `/display verbose` 切换为 `verbose`。Verbose 模式用有界 Panel/Syntax 展示结构化参数、多行结果和失败详情。`--compact` 和 `/display compact` 可显式恢复默认模式。

### 3. 展示模式不是持久化执行事实

Display mode 仅存在于当前 InteractiveShell 进程，不写入 Session、Run metadata 或 Event Log。SQLite 中的 Runtime Event 和 ToolExecution 仍是完整执行事实；`/events`、Learning Console 和 API 不受终端折叠策略影响。

### 4. 所有动态内容保持有界

Compact 参数和结果摘要分别限制在约 180 字符。Verbose 参数、结果和失败详情限制在 4000 字符，并明确标注省略字符数。大 Tool Result 的正式读取仍使用既有 Tool Result Artifact 与 `read_artifact`，终端展示不替代 Artifact 协议。

## 影响

### 优点

- Markdown 标题、列表、强调和代码块在流式终端中保持可读。
- Tool 调用默认不再淹没模型回答和完成证据。
- 用户可以在简洁操作视图和诊断视图之间即时切换。
- `--print` 获得稳定的单一最终输出，适合脚本和管道。
- Runtime Kernel、Event schema 和数据库无需迁移。

### 代价

- 输出粒度从逐 token 原位置刷新调整为稳定 Markdown 块；没有空行的长段落需要等待内容段边界。
- 每个稳定块会独立解析 Markdown，跨块结构应依靠空行或 fenced code block 等明确边界。
- Compact 摘要可能隐藏诊断细节，排障时需要切换 verbose 或读取 durable Event/ToolExecution。
- 展示模式不跨 Shell 重启持久化。

## 被放弃的方案

### 继续逐 delta 原样打印

实现最简单，但 Markdown 围栏和结构长期不可读，也无法解决 Tool 输出占屏问题。

### 每个 delta 独立构造 Markdown

每个 token 都作为独立 Markdown 文档，无法正确解析跨 delta 的代码围栏、列表和强调，且会产生大量碎片。

### 使用 Rich Live 重绘累计 Buffer

在理想 ANSI 终端中可以获得逐 token 原位置更新，但 Windows PowerShell 等宿主可能无法可靠覆盖高度持续增长的旧帧，最终把多个累计 Buffer 都保留在屏幕上。默认本地稳定合同因此采用 append-only 块渲染。

### 将展示模式写入 Runtime Event

会把纯终端偏好混入可恢复执行事实，并使 HTTP、Learning Console 等 Adapter 被 CLI 决策污染，因此不采用。

### 默认 verbose

保留最多信息，但真实 Coding Loop 中噪声过大；完整事实已经持久化，默认视图应优先突出行动和结果。

## 后续约束

- 新增终端事件展示时必须同时定义 compact 和 verbose 行为。
- Compact 模式不得直接展开文件正文、完整 Patch、完整环境变量或无界 Tool Result。
- Streaming 展示不得修改 `model.delta` 的 durable Event 语义。
- 默认终端投影必须保持 append-only；若引入 Live 重绘，只能作为显式实验模式并验证最终屏幕状态。
- `--print` 必须保持只输出最终结果的脚本友好合同。
- 若未来增加 Approval diff/command preview，应复用同一有界展示策略，不得绕过 Runtime Approval 事实。

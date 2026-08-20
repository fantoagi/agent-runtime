# ADR-0036：当前 Run 只读 Tool 结果复用与收敛边界

- **状态**：Accepted
- **日期**：2026-08-19
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-19-005](../CHANGELOG.md#e2026-08-19-005)

## 背景

真实模型在代码解释和定位任务中可能重复调用完全相同的搜索或读取 Tool，造成额外模型步骤、重复本地执行和冗长终端输出。参数写错时，旧错误只指出未知字段；路径写错时也只报告不存在，模型需要继续猜测 Tool schema 或 Workspace 相对路径。

Runtime 需要帮助模型收敛，但不能把一般 Tool 结果变成不受约束的跨 Run cache，也不能因复用陈旧读取结果掩盖 Workspace 已发生的副作用变化。

## 决策

### 1. 仅复用固定白名单内的只读 Tool

可复用集合固定为：

- `calculator`
- `git_status`
- `git_diff`
- `list_files`
- `read_artifact`
- `read_file_lines`
- `read_text_file`
- `search_text`

候选必须来自同一个 Run，且 Tool 名和 arguments 完全相同、原 ToolExecution 状态为 completed。失败、cancelled、UNKNOWN、等待审批或副作用 Tool 永不复用。

### 2. 副作用 Tool 构成失效屏障

Runtime 只在最近一次副作用 Tool 之后查找候选。只要中间出现 side-effecting Tool，即使名称和参数相同，也必须重新执行只读 handler，防止文件或 Git 状态变化后返回陈旧结果。

### 3. 复用仍保留完整审计事实

模型第二次请求相同 Tool 时：

- 创建新的 ToolExecution，并将其持久化为 completed。
- 递增 Run 的 `tool_call_count`。
- 保存新的 Checkpoint。
- 写入 durable `tool.reused` Event，并关联来源 ToolExecution ID。
- 不再次调用 Tool Handler。
- 把原 durable result 与 Runtime convergence note 作为 tool message 返回模型。

因此观测者可以区分“模型再次请求”和“handler 再次执行”，不会因为优化而丢失 Agent 行为事实。

### 4. 错误反馈帮助模型一次修正

Tool 参数校验错误必须列出 allowed arguments。`search_text` 和 `read_file_lines` 的目标路径不存在时，在 Workspace 内执行有界文件名匹配，返回最多五个 workspace-relative 候选。候选扫描复用现有忽略目录和文件数量上限，不扩展 Workspace 安全边界。

### 5. compact 终端只保留有信息增量的行

常见 inspection Tool 在 compact 模式不显示 `tool.requested`，只显示 completed、failed 或 reused；verbose 模式继续展示 requested、started 和 terminal 生命周期。Learning Console 对 `tool.reused` 提供独立说明，durable Event 保持可追溯。

## 影响

### 优点

- 完全相同的搜索和读取不会重复执行 handler。
- 模型能从明确 schema 和路径候选更快修正调用。
- compact 终端减少重复 requested/completed 行。
- 新 ToolExecution、计数、Checkpoint 和 Event 保留完整审计事实。

### 代价

- 复用只识别完全相同 arguments，不判断两个不同参数请求是否语义等价。
- `tool.reused` 是 point event，不构造新的 requested-started-completed span。
- 白名单需要在新增可信只读 Tool 时显式评审。
- 路径候选是文件名启发式，不替代完整代码索引。

## 被放弃的方案

### 跨 Run 或全局 Tool Result Cache

缓存命中率可能更高，但失效、权限、Workspace 版本和数据生命周期复杂，不符合当前单机最短路径。

### 复用所有声明为非副作用的 Tool

第三方 Tool 的只读声明可能不完整，且网络查询结果可能随时间变化。当前只接受经过评审的本地固定白名单。

### 直接拒绝第二次重复 Tool Call

拒绝会制造新的失败路径，并迫使模型修复本可安全满足的请求。复用 durable result 能保留协议连续性，同时给出收敛提示。

## 后续约束

- 新增可复用 Tool 必须证明结果在同一 Run、无副作用屏障时可安全复用。
- `tool.reused` 必须关联来源 ToolExecution，并继续保存当前 ToolExecution 与 Checkpoint。
- 任何副作用 Tool 都必须使此前只读候选失效。
- 不得复用失败、UNKNOWN、审批中或副作用结果。
- compact 降噪不得删除 durable Event；verbose 必须保留完整生命周期。

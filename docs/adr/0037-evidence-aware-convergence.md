# ADR-0037：证据感知的只读检查收敛与无工具最终综合

- **状态**：Accepted
- **日期**：2026-08-19
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-19-006](../CHANGELOG.md#e2026-08-19-006)

## 背景

ADR-0036 可以复用 arguments 完全相同的只读 Tool 结果，但真实模型仍可能通过改变 query、路径拼写或行范围反复获得相同证据。仅依赖 `max_steps` 会让一个本可回答的只读任务最终进入 failed，终端只看到模型步骤上限而无法理解循环原因。

## 决策

Runtime 从当前 Run 的 durable ToolExecution 顺序派生证据账本和 no-progress streak，不保存额外可变内存状态，也不增加数据库迁移。

- `search_text` 使用匹配文件和行号判断新证据。
- `read_file_lines` 使用文件摘要与新增行区间判断新证据。
- `read_artifact` 使用 Artifact 摘要与新增字符区间判断新证据。
- `list_files` 使用新增路径判断新证据。
- 其他白名单只读 Tool 使用规范化结果摘要。
- 失败、拒绝、取消、UNKNOWN、空命中和完全重叠范围不增加证据。
- 副作用 Tool 是证据失效屏障：清空账本和连续计数，并阻止当前 Run 的自动 finalization。

达到 warning 边界时写入 `convergence.warning` 并通过 Checkpoint 注入 system note。达到 finalization 边界或即将耗尽模型步骤时，写入 `convergence.finalization_requested`，下一次 Model Provider 请求传入空 Tool Definition；模型必须基于已有证据形成答案。Provider 违规返回未暴露的 Tool Call 时不得执行。

## 影响

### 优点

- 不同参数的语义重复检查也可以被识别。
- 简单解释任务不会在证据已经足够时以 `max_steps` 失败。
- 收敛原因、次数和最终综合都可通过 Event Log、CLI 和 Learning Console 审计。
- 状态由 durable ToolExecution 重建，进程恢复不依赖内存计数器。
- 不改变 Tool Handler、Provider `complete/stream` 或 SQLite schema。

### 代价

- 证据模型是结构化启发式，无法证明两个自然语言问题真正等价。
- 默认阈值需要继续通过真实模型行为校准。
- 自动 finalization 对出现过副作用 Tool 的修改任务保持保守，不能解决全部修复循环。
- 最终无工具请求仍依赖模型愿意输出文本。

## 被放弃的方案

### 仅提高 `max_steps`

只会延长循环并增加费用，不能解释模型为什么没有进展。

### 对所有结果做向量或 LLM 语义比较

引入额外模型调用、非确定性和复杂持久化，不适合本地最短路径 Runtime。

### 对修改任务统一强制禁用 Tool

可能在验证或修复尚未完成时截断真实工作，因此首版只自动 finalization 无副作用检查流程。

## 后续约束

- 新 inspection Tool 必须定义可测试的新证据判定，或明确退化到稳定结果摘要。
- 收敛 Event 必须保持 durable，不能只在 CLI 内维护计数。
- 禁用 Tool 的 Provider 请求返回 Tool Call 时不得绕过 Runtime 再执行。
- 任何修改默认阈值、证据屏障或副作用 finalization 边界的变化必须更新 ADR 和回归测试。

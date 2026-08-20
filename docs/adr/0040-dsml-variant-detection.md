# ADR-0040：Unicode 兼容的 DSML 变体识别边界

- **状态**：Accepted
- **日期**：2026-08-19
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-19-009](../CHANGELOG.md#e2026-08-19-009)
- **扩展决策**：[ADR-0039](./0039-textual-tool-call-guard.md)

## 背景

v0.8.16 已在 finalization 边界拒绝文本化 Tool Call，但首版 DSML 检测依赖精确的 ASCII `<|DSML|...>` 前缀。真实 OpenAI-compatible Provider 在一次 Run 中返回了 `<｜｜DSML｜｜tool_calls>`：竖线为 Unicode 全角 U+FF5C，且 marker 两侧各重复两次。该内容未进入结构化 `tool_calls`，从而绕过字面检查，被持久化为 `model.delta`、`run.result` 并错误标记 completed。

## 决策

Runtime 保持 ADR-0039 的“不解析执行文本 Tool Call”边界，并扩展 DSML 的识别方式：

- 只对检测副本执行 Unicode NFKC 兼容归一化，原始 Provider 内容不改写。
- DSML marker 允许一个或多个竖线，并允许竖线、`DSML` 与 tag name 之间存在有限空白。
- 仅识别以 `tool_calls` 或 `invoke` 开始、包含 `invoke`，且每个非空行都以已知 DSML tag 开始并以 `>` 结束的主要或完整 envelope。
- 支持的 tag 限定为 `tool_calls`、`invoke` 和 `parameter`，不把任意包含 `DSML` 的自然语言判成调用。
- 识别成功后沿用一次 repair、第二次失败、Streaming 先缓冲校验的现有语义。
- 规范化结果绝不送入 Tool Registry 或 Tool Executor。

## 影响

### 优点

- 全角竖线、双竖线和 spaced marker 不再绕过 finalization Guard。
- 修复直接覆盖真实 durable Run 中观察到的 Provider 输出，而不是只依据终端渲染猜测。
- 不改变 Event schema、Tool Handler、Provider 协议或 SQLite schema。
- 带自然语言解释的 DSML 示例仍可作为普通答案返回。

### 代价

- 检测器需要维护一个有限 DSML 语法子集。
- Unicode 兼容归一化扩大了识别集合，因此必须持续保留协议解释反例测试。
- 未知私有 Tool 文本协议仍可能需要后续基于真实证据扩展。

## 被放弃的方案

### 仅增加 `<｜｜DSML｜｜` 字面前缀

只能修复当前字符串，无法覆盖 ASCII 双竖线或 marker 空白变体，且继续累积脆弱分支。

### 对整个模型答案先归一化再保存

会改变用户可见内容及审计事实。Runtime 只应规范化检测副本，durable 原文保持 Provider 实际输出。

### 从变体 DSML 中恢复并执行 Tool

文本不具备可信结构化调用、Approval、幂等和授权语义，执行会绕过 Runtime 安全边界。

## 后续约束

- 新增兼容字符或 DSML tag 必须由真实 Provider 证据驱动，并增加正例、反例及 Streaming 测试。
- 检测必须继续作用于完整缓冲后的 finalization 内容，不能让未验证 delta 先进入 durable Event。
- 任何文本协议都不得转换为可执行 Tool Call。
- 若误判率上升，应优先收紧 envelope 完整性，而不是关闭 Guard。

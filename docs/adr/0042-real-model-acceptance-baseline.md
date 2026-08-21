# ADR-0042：真实模型验收使用隔离合成 Workspace 与 durable 事实报告

- **状态**：Accepted
- **日期**：2026-08-20
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-20-001](../CHANGELOG.md#e2026-08-20-001)
- **扩展决策**：[ADR-0008](./0008-observability-evals.md)、[ADR-0041](./0041-fresh-finalization-context.md)

## 背景

确定性 Mock Eval 能验证 Runtime 已知合同，但不能覆盖真实模型的 Tool 选择差异、重复调用、文本化 Tool Call、收敛漂移和修改后漏验证。此前多个真实 DeepSeek Run 已证明：全部自动化测试通过时，Provider 与长 Tool Context 的组合仍可能失败。继续依赖人工截图会导致用例、配置和判断标准变化，无法比较版本回归。

真实模型验收又存在两个边界：不能默认把用户当前 Workspace 全量发送给外部模型；报告也不应复制 Prompt、源码、Tool arguments/result 或最终答案原文。

## 决策

- 增加固定 `local-real-model` Suite，覆盖解释、检查、小型修改、文件缺失恢复和 Approval 生命周期。
- 每个 Case 创建独立合成 Workspace、Git 基线、SQLite、Artifact 和日志；不读取或复制调用者 Workspace 内容。
- Case 使用与本地 Coding Agent 相同的 configured Provider、Tool、Runtime、Approval 和恢复路径，不绕过正式执行链路。
- 修改 Case 只在隔离 Fixture 中自动批准副作用 Tool，审批原因固定写入 durable Event。
- 报告从 Run、Event 和 ToolExecution 派生 completion、convergence、Tool efficiency、protocol integrity、verification 和 interaction/lifecycle 指标。
- 默认报告只包含 Run/Trace ID、模型名、Runtime 版本、Suite checksum、计数、状态、断言、最终答案长度与 SHA-256；不包含内容原文或 Tool 参数。
- 每次报告保存在唯一目录，并更新 `latest-report.json` 快捷入口。
- 相同 Case 可重复执行，用固定阈值发现 Runtime 回退；不引入 LLM-as-a-Judge。

## 影响

### 优点

- 真实失败从一次性截图变为可重复 Case 和 durable 证据。
- Runtime 修改前后可以比较步骤、Tool、重复、失败、协议和验证指标。
- 自动化验收不会修改或默认导出用户项目。
- 修改、Approval、resume 和验证使用正式 Runtime 语义。
- Suite checksum 和 Runtime version 使报告具备可追溯性。

### 代价

- 真实 Provider 调用会产生费用并受网络、模型版本和随机性影响。
- 合成 Fixture 不能代表所有真实大型项目。
- 不保存原文降低了报告泄露风险，也意味着深入诊断必须在本机读取对应 durable Run。
- 内置修改 Case 依赖本机 Git 和测试执行环境。

## 被放弃的方案

### 直接在当前项目 Workspace 运行所有验收

更接近真实任务，但会修改当前代码并把项目内容发送给 Provider，不适合作为默认自动基线。

### 只保存人工截图和最终答案

缺少 Run/Event/ToolExecution 事实，无法判断失败发生在 Provider、协议、Tool、收敛还是验证阶段。

### 第一版使用 LLM-as-a-Judge

会引入第二个模型、额外费用和新的非确定性。v0.8.19 先使用 Runtime 可确定计算的指标。

## 后续约束

- 内置 Case 必须使用合成数据，不得复制当前 Workspace、API Key 或 Session 内容。
- 报告 schema 新增内容字段前必须评估数据泄露风险并更新本 ADR。
- 自动批准只能发生在每 Case 独立的隔离 Fixture 中。
- 真实失败修复必须关联 Case、Run ID、失败断言和自动化回归测试。
- 增加 Suite 阈值或 Case 时必须递增 Suite version，并保持旧报告可读。

# ADR-0045：真实模型 Acceptance Report 使用离线回归门禁

- **状态**：Accepted
- **日期**：2026-08-21
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-21-002](../CHANGELOG.md#e2026-08-21-002)

## 背景

v0.8.19-v0.8.21 已能在隔离 Workspace 中运行真实模型并生成脱敏报告，但每次报告只能人工查看。若直接把下一次真实模型运行当作“通过/失败”，容易遗漏此前已通过 Case 的回归，也容易因换模型或版本而误报。

## 决策

增加报告层的离线比较器，以 `suite_name`、`suite_version`、`suite_checksum` 确认两个报告属于同一验收集合，再用 `case_name + attempt` 对齐执行。以前通过的 Attempt 失败、验证证据退化、协议违规增加和 UNKNOWN Tool Outcome 增加属于阻断回归；模型、Provider、Runtime 版本变化和性能变化只作为 warning。

## 影响

### 优点

- 不需要再次调用模型，适合本地重复验证和 CI/Nightly。
- 比较结果可追溯到报告中的 Case/Run/Trace ID。
- 不把 Prompt、文件内容、Tool 参数/结果或最终答案原文带入比较输出。
- 把“真实失败”与“环境/版本变化”分开，降低误修 Runtime 的风险。

### 代价

- 有意修改 Suite 后必须重新建立 baseline，不能跨 checksum 强行比较。
- 性能只做 warning，当前不提供统计显著性分析。
- 报告比较不能判断答案语义，只能复用 Case 已持久化的断言和指标。

## 被放弃的方案

### 每次比较时重新运行真实模型

成本高、结果受随机性影响，且无法隔离“模型波动”和“Runtime 回归”。

### 直接比较最终答案文本

答案原文可能包含敏感内容，且自然语言存在合法措辞变化；durable 断言和结构指标更适合作为第一层门禁。

## 后续约束

任何新增 Acceptance 指标若要成为回归门禁，必须在本 ADR 或后续 ADR 中明确其基线、容忍度和是否阻断。

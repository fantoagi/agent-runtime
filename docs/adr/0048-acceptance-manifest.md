# ADR-0048：Acceptance 报告采用非敏感运行 Manifest

- **状态**：Accepted
- **日期**：2026-08-22
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-22-004](../CHANGELOG.md#e2026-08-22-004)

## 背景

v0.8.26 的 Acceptance 报告已经包含 Suite、Model、Runtime 和 Selection 信息，但缺少统一的运行环境事实。真实模型验收需要能够复现和比较运行上下文，同时不能把 API Key、完整环境变量、Prompt 或 Tool 内容写入报告。

## 决策

1. 在报告中新增可选 `manifest` 区域，保存 Runtime 版本、Git HEAD、Python 版本、平台、Provider、Model、Suite、Case、repeat 和开始/结束时间。
2. Manifest 只允许非敏感元数据；Git 不可用写入 `null`，其他不可用环境信息写入 `unknown`。
3. `eval compare` 将 Manifest 差异作为结构化摘要输出，不把版本、模型、平台或 Git 差异自动判定为回归失败。
4. 旧报告没有 `manifest` 时，使用原有顶层字段和 `selection` 兼容读取。

## 影响

### 优点

- 每次 Acceptance 运行可关联到更完整的本地执行上下文。
- compare 结果能解释环境差异，同时不破坏 strict/partial scope 和行为回归语义。
- 报告继续保持脱敏，不依赖真实 API Key 才能序列化或比较。

### 代价

- Manifest 中的平台字符串可能随本地解释器和操作系统变化。
- Git HEAD 不包含未提交工作区 diff，Workspace 变化仍需依靠既有验证证据。

## 被放弃的方案

- 将完整环境变量、API Key、Prompt、Fixture 或 Tool 参数写入 Manifest。
- 把 Runtime/Model/Platform 差异作为自动失败条件。
- 为旧报告强制补写不可推断的 Git 或 Python 信息。

## 后续约束

任何新增 Manifest 字段必须经过敏感信息审查，并保持旧报告读取兼容；任何把 Manifest 差异升级为回归门禁的变更，都必须新增 ADR。

# ADR-0002：Runtime 通过统一 Model Provider 协议接入模型

- **状态**：Accepted
- **日期**：2026-08-11
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-11-001](../CHANGELOG.md#e2026-08-11-001)

## 背景

不同模型厂商的消息、工具调用、usage 和错误结构不同。若 Runtime Kernel 直接依赖厂商 SDK，模型切换、Mock 测试和协议升级都会侵入执行循环。

## 决策

定义 `ModelProvider.complete(messages, tools, config) -> ModelResponse` 协议。Runtime 只处理统一的 `Message`、`ToolCall`、文本、finish reason 和 usage。

首版提供：

- `MockProvider`：确定性测试与 Demo。
- `OpenAICompatibleProvider`：Chat Completions 兼容端点。

超时和重试由 Runtime 统一控制，Provider 负责协议转换和厂商错误归一化。

## 影响

### 优点

- Runtime Kernel 与模型厂商解耦。
- 可以通过 Mock Provider 精确覆盖工具调用路径。
- 新 Provider 无需修改 Agent 循环。

### 代价

- 统一模型可能无法直接暴露所有厂商特性。
- Provider 需要维护消息和工具 schema 的双向转换。
- token 级流式输出需要扩展当前协议。

## 被放弃的方案

- Runtime 直接调用单一模型 SDK。
- 将厂商原始响应直接暴露给工具和状态存储。

## 后续约束

新增流式、多模态、结构化输出或批处理能力时，应先扩展统一协议，再适配具体 Provider；不得在 Runtime Kernel 中加入厂商分支。

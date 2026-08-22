# ADR-0049：Interactive CLI Session 采用 durable Run 续接与事件序列去重

- **状态**：Accepted
- **日期**：2026-08-22
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-22-005](../CHANGELOG.md#e2026-08-22-005)

## 背景

v0.8.2 以来 Interactive CLI 已支持多轮 Session、`--continue`、`--resume`、流式事件和 Ctrl+C。长会话或 Runtime 重启后，如果 CLI 重复消费 durable Event，终端会出现重复答案；如果把继续会话误实现为重跑旧 Run，还会破坏执行事实和副作用安全语义。

## 决策

1. 每一轮交互继续创建独立 durable Run，并通过 Session 关联；`--continue` 和 `--resume` 只选择已有 Session，不重新执行已完成 Run。
2. Session 历史只加载有界的已完成 Run 摘要，默认最近 20 个，避免长会话无限膨胀上下文。
3. Renderer 在单个 Run 内按 Event sequence 单调消费，重复或回放的 sequence 直接丢弃；开始新轮次时重置游标。
4. 输入阶段 Ctrl+C 只清空当前提示并继续交互；活动 Run 的 Ctrl+C 仍调用 Runtime 协作式 `cancel()`。
5. 进程中断后的恢复继续遵守 Runtime 的 crash recovery 和副作用 Tool UNKNOWN 语义，不在 CLI 层假装成功或自动重试未知副作用。

## 影响

### 优点

- 25～50 轮本地 Session 可以进行有界回归验证。
- Runtime 重启后继续会话不会复制已完成 Run。
- 断线重放不会重复渲染 durable Model/Event 内容。
- 保持 `--print`、stdout/stderr 和既有退出码语义。

### 代价

- Renderer 只在当前 Run 内按 sequence 去重，不能替代跨进程的 Session 并发协调。
- 历史只恢复完成 Run 摘要，不重放完整 Tool 中间消息。

## 被放弃的方案

- 把整个 Session 历史保存在 CLI 内存中。
- 通过重新提交旧输入来“恢复”已完成 Run。
- 依赖模型输出文本判断某个 Event 是否已经打印。

## 后续约束

任何修改 Interactive Session 历史边界、`--continue`/`--resume` 选择语义、Event replay 或 Ctrl+C 退出码的变更，都必须更新本 ADR 或新增兼容性 ADR，并补充 Mock Provider 回归测试。

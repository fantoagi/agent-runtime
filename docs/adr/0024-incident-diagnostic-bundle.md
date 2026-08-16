# ADR-0024：故障诊断包采用允许列表与内容排除模型

- **状态**：Accepted
- **日期**：2026-08-16
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-16-003](../CHANGELOG.md#e2026-08-16-003)

## 背景

综合诊断能够在 CLI、HTTP 和 Learning Console 中查看当前状态，但跨人员排障仍需要复制多段 JSON，容易遗漏版本、范围、校验信息，也可能误把 Prompt、Tool 参数、Memory、Artifact 或数据库发给不应接收这些内容的人。直接归档 SQLite、Artifact 或完整 Event payload 虽然信息丰富，但不适合作为默认支持包。

## 决策

新增独立的 `IncidentDiagnosticsService`，从 Runtime 和 SQLite durable facts 派生只读报告，并生成 format version 1 的 ZIP。诊断包采用允许列表而不是黑名单：Run 不包含 input/result；Event 只保留 envelope 和控制字段；原始错误文本、本机路径、Tool 参数/结果、模型内容、Memory、Checkpoint、Artifact 和 SQLite 文件全部排除。

根因摘要使用确定性规则，根据 HTTP status、错误类型、`model.attempt.failed`、`tool.failed`、`tool.outcome_unknown`、`run.failed` 和 `workflow.failed` 分类。已完成 Run 的中间失败标记为 recovered，不将其视为当前未恢复事故。

CLI 写入采用同目录临时文件、flush/fsync 和 `os.replace`；默认拒绝覆盖。HTTP 直接返回内存 ZIP，并使用 `no-store`。Run/Event 收集使用显式上限，`collection.json` 和 manifest 记录截断状态。该诊断包不是备份格式，不能用于恢复。

## 影响

### 优点

- 支持信息具有固定结构、版本、范围和条目 SHA-256。
- 默认排除最敏感、最容易泄露的执行内容。
- 同一服务可复用于 CLI、HTTP 和 Learning Console。
- 根因摘要可区分 Provider 认证、限流、服务端、超时、Tool 失败与 UNKNOWN。
- 不新增 SQLite schema、外部依赖或后台采样任务。

### 代价

- 允许列表可能遗漏排障所需的低频字段。
- 报告不是跨表事务快照，运行中的 Runtime 可能在生成期间继续变化。
- 确定性分类只能解释已定义的事件和错误类别。
- 诊断包降低但不能消除对外分享风险，仍需人工复核。

## 被放弃的方案

- 默认打包 SQLite 和 Artifact：恢复信息完整，但隐私和体积风险过高，且与 `.agent-backup` 职责冲突。
- 仅对完整 payload 做敏感词替换：无法可靠识别任意业务数据或 Prompt，黑名单容易遗漏。
- 自动上传远程支持平台：引入网络、凭据、权限和数据驻留问题，超出单机稳定化范围。
- 用模型生成根因：结果不确定、增加 Provider 依赖，并可能把故障数据再次发送到外部模型。

## 后续约束

- 新增诊断字段必须先判断是否可能包含 Prompt、Tool 数据、Memory、Artifact、凭据或本机敏感路径。
- 默认诊断包必须继续使用允许列表；扩大内容范围需要新增 ADR 或更新本 ADR。
- Bundle format 发生不兼容变化时必须增加 format version。
- 诊断包不得替代 `.agent-backup`、Runtime Event Log 或结构化日志。
- 新增远程上传前必须先设计认证、授权、数据驻留、失败重试和用户确认流程。
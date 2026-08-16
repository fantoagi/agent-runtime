# Agent Runtime 故障诊断包与根因摘要

- **适用版本**：v0.7.12
- **最近更新**：2026-08-16（Asia/Shanghai）
- **适用范围**：单机 Runtime、CLI、FastAPI、Learning Console

v0.7.12 提供面向本地排障和支持协作的脱敏诊断包。它从当前 Runtime 状态和 SQLite durable facts 派生摘要，不修改 Run 状态，也不复制数据库、Artifact 或原始执行内容。

## 1. 一键生成诊断包

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
agent-runtime observe incident-bundle --output incident.zip --event-limit 5000
```

只收集某个 Root/Child Run 所在完整 Run Tree：

```powershell
agent-runtime observe incident-bundle `
  --run-id run_xxx `
  --output incident-run_xxx.zip
```

目标文件已经存在时默认拒绝覆盖；明确确认后使用：

```powershell
agent-runtime observe incident-bundle --output incident.zip --event-limit 5000 --overwrite
```

## 2. HTTP 下载

```text
GET /observability/incident-bundle
GET /observability/incident-bundle?run_id=run_xxx&limit=100&recent_failure_limit=20
```

PowerShell：

```powershell
Invoke-WebRequest `
  http://127.0.0.1:8000/observability/incident-bundle `
  -OutFile incident.zip
```

Learning Console 顶部的“诊断包”入口调用同一接口。HTTP 响应使用 `application/zip`、`Content-Disposition: attachment` 和 `Cache-Control: no-store`。

## 3. ZIP 内容

```text
manifest.json
README.txt
diagnostics.json
failure-analysis.json
runs.json
events.json
privacy.json
```

- `manifest.json`：格式版本、Runtime 版本、生成时间、范围、条目大小和 SHA-256。
- `diagnostics.json`：Runtime、容量、进程、SQLite、Doctor、Metrics 和最近失败的脱敏快照。
- `failure-analysis.json`：按 Run 和根因类别生成的严重程度、恢复状态与建议动作。
- `runs.json`：只保留 Run ID、Agent 名称、状态、时间、计数和安全 metadata。
- `events.json`：只保留 Event envelope 和允许列表中的控制字段。
- `collection.json`：记录 Run/Event 上限、观察数、包含数和是否截断。
- `privacy.json`：明确本包排除的内容和脱敏策略。

## 4. 根因分类

当前规则基于 durable Event，不使用模型猜测：

| 类别 | 典型证据 | 建议 |
| --- | --- | --- |
| `provider.authentication` | HTTP 401/403 | 修复凭据或权限，不自动重试 |
| `provider.rate_limit` | HTTP 429 | 遵循 Retry-After、降低并发或申请配额 |
| `provider.server` | HTTP 5xx | 检查 Provider 状态并有界退避 |
| `provider.timeout` | Timeout 错误类型或文本 | 检查延迟和超时，只重试幂等请求 |
| `provider.transport` | `model.attempt.failed` | 检查连接、协议和重试事件 |
| `tool.execution` | `tool.failed` | 检查 Handler；副作用安全时才重试 |
| `tool.unknown_outcome` | `tool.outcome_unknown` | 核对外部副作用并人工 resolve |
| `runtime.execution` | `run.failed` / `workflow.failed` | 从 Trace 向前检查 Provider 或 Tool 失败 |

如果 Run 最终为 `completed`，对应失败会标记 `recovered=true` 和 `severity=info`，避免把已自动恢复的中间失败误判为当前事故。

## 5. 隐私与安全边界

诊断包明确不包含：

- Run input 和 result。
- System Prompt、模型消息和 `model.delta` 内容。
- Tool arguments、result content 和 result data。
- Memory content 与 Checkpoint messages。
- Artifact 文件和 SQLite 数据库。
- Provider、Tool 和 Run 的原始错误文本。
- Runtime 数据库与 Artifact 的本机绝对路径。

常见 Bearer Token、`sk-` / `rk-` / `pk-` Token 和敏感字段仍会执行二次脱敏。诊断包适合降低支持协作风险，但在发送到组织外部前仍应按所在组织的安全流程人工复核。

## 6. 一致性和写入语义

- 报告读取当前持久化事实，不冻结 Runtime，也不保证跨表全局瞬时快照。
- ZIP 先在内存中构建，再通过同目录临时文件、flush/fsync 和 `os.replace` 原子落盘。
- 已存在目标默认不覆盖。
- Bundle format 当前为 `1`，与 SQLite schema 独立。
- 诊断包只用于排障，不可用于恢复；恢复必须使用 `.agent-backup`。

## 7. 当前限制

- 根因摘要是确定性规则，不是完整因果推理或 AIOps。
- 当前不采集 CPU、RSS、句柄、磁盘与网络趋势。
- 当前不自动上传工单系统、对象存储或远程 Collector。
- 当前不打包宿主日志；日志保留仍由宿主环境负责。
- 对外发送前仍需要人工确认组织合规要求。
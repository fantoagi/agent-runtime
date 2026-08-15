# Agent Runtime 当前状态

- **当前版本**：`0.7.9`
- **当前里程碑**：v0.7.9 AgentDefinition Snapshot Recovery
- **Runtime 构建完成时间**：2026-08-15（Asia/Shanghai）
- **文档体系构建完成时间**：2026-08-11（Asia/Shanghai）
- **当前代码基线 commit**：`pending`
- **最近演进记录**：[E2026-08-15-010](./CHANGELOG.md#e2026-08-15-010)

## 状态定义

| 状态 | 含义 |
| --- | --- |
| ✅ stable | 已完成且有自动化测试覆盖 |
| 🧪 experimental | 已实现但接口或行为可能变化 |
| 🚧 partial | 只完成部分可靠性或使用场景 |
| 📋 planned | 已规划但尚未实现 |
| ⛔ unsupported | 当前明确不支持 |

## 已实现能力

| 能力 | 状态 | 当前事实 | 演进记录 |
| --- | --- | --- | --- |
| 单 Agent Kernel、Run 状态机、Checkpoint、Approval | ✅ stable | 支持持久化执行、暂停、恢复、取消和人工审批 | E2026-08-11-001、E2026-08-13-001 |
| Model Provider 与 Token Streaming | ✅ stable | Mock、OpenAI-compatible、持久化 `model.delta` | E2026-08-11-001、E2026-08-14-001 |
| Tool Registry 与安全执行 | ✅ stable | 参数校验、有界线程池、背压、超时、取消、UNKNOWN 与原子文件写入 | E2026-08-15-005 |
| SQLite Event Log 与恢复 | ✅ stable | WAL、FULL、quick_check、busy retry、事务 sequence、AgentDefinition 快照和 schema 1–8 迁移 | E2026-08-15-006、E2026-08-15-009、E2026-08-15-010 |
| Runtime 生命周期与准入 | ✅ stable | `shutdown()`、跨进程 `wait()`、崩溃协调、顶层 Run 容量限制和模型请求并发限制 | E2026-08-15-006、E2026-08-15-008、E2026-08-15-009 |
| FastAPI 与 SSE | ✅ stable | 健康检查、heartbeat、断线恢复、Runtime 所有权、幂等提交和 429 背压 | E2026-08-15-007、E2026-08-15-009 |
| 多 Agent Workflow | ✅ stable | Parent/Child、串行/并行、幂等委派、取消传播、Workflow 与 AgentDefinition 确切快照 | E2026-08-14-007、E2026-08-15-006、E2026-08-15-010 |
| Context、Session、Memory 与 Artifact | ✅ stable | token budget、FTS5 scoped memory、TTL、软删除和大结果 Artifact 化 | E2026-08-15-001 |
| Observability、Evals 与 Learning Console | ✅ stable | Trace Tree、Metrics、Eval、动态泳道和可靠性状态 | E2026-08-15-002、E2026-08-15-003、E2026-08-15-007 |
| Runtime Doctor 与 Crash Matrix | ✅ stable | 只读一致性诊断；模型、Tool、Approval、Workflow 强杀恢复，恢复进程无需重新注册 AgentDefinition | E2026-08-15-008、E2026-08-15-010 |
| 质量与发布门禁 | ✅ stable | Ruff、Mypy strict、coverage、跨平台 CI、Wheel smoke、stress/soak/crash | E2026-08-15-004、E2026-08-15-007、E2026-08-15-008 |

## 部分实现或实验能力

| 能力 | 状态 | 当前边界 | 演进记录 |
| --- | --- | --- | --- |
| 多 Runtime 连接同一 SQLite | 🧪 experimental | 支持并发事件写入，但不提供多进程任务调度 | E2026-08-15-006 |
| 30 分钟长稳与性能基线 | 🧪 experimental | 已进入 Nightly；发布前必须跑满 | E2026-08-15-007 |

## 计划能力

| 能力 | 状态 | 说明 | 演进记录 |
| --- | --- | --- | --- |
| v0.8 Sandbox / Capability / Secret | 📋 planned | 已后置，v0.7.9 Nightly 稳定并完成备份恢复演练后再启动 | E2026-08-15-007 |
| 分布式 Worker 与 Queue | 📋 planned | 不属于当前单机可靠性范围 | E2026-08-15-007 |
| 多租户与权限治理 | 📋 planned | 等待身份、审计和隔离模型设计 | E2026-08-15-007 |

## 当前明确不支持

| 能力 | 状态 | 原因 | 演进记录 |
| --- | --- | --- | --- |
| 强杀同步 Python 线程 | ⛔ unsupported | 线程不能安全强杀；副作用结果转为 UNKNOWN | E2026-08-15-005 |
| 自动数据库降级 | ⛔ unsupported | 迁移只允许向前，避免破坏历史数据 | E2026-08-15-006 |
| 分布式调度与跨主机恢复 | ⛔ unsupported | 当前目标是单机 Runtime | E2026-08-15-007 |
| 不可信代码强隔离 | ⛔ unsupported | 需要后续 Sandbox 版本 | E2026-08-15-007 |

## 当前已知限制

- SQLite 适合单机和中等并发，不等同于分布式数据库或任务队列。
- 同步副作用 Tool 超时后只能标记 `UNKNOWN` 并等待人工确认，不能保证回滚。
- Workflow 与普通 Run 可从不可变 AgentDefinition 快照恢复；Python Tool Handler 和 Model Provider 实现仍必须由新进程提供。
- Nightly 的 30 分钟 soak 不作为每个 PR 的阻塞时长。
- Learning Console 是教学与诊断 Adapter，不是生产运维控制台。`max_inflight_runs` 是单进程容量，不是分布式全局配额。

## 当前测试状态

- 自动化测试：`146 passed`（2026-08-15，本地 Python 3.13），包含单元、集成、定义快照、幂等并发、容量背压和真实进程强杀恢复测试。
- Core line coverage：`92.01%`；core branch coverage：`80.25%`。
- PR：Ubuntu Python 3.11/3.12/3.13、Windows Python 3.13。
- Nightly：100 并发、20 轮 Crash Matrix、故障测试重复、30 分钟 soak 和性能回退检查。

## 当前运行方式

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[api,quality]"
python scripts/check_docs.py
python -m pytest
agent-runtime lab
```

### FastAPI / SSE

```powershell
uvicorn agent_runtime.api.app:app --reload
Invoke-RestMethod http://127.0.0.1:8000/health
```

### 可靠性快速验证

```powershell
python scripts/run_reliability.py --stress-runs 20 --concurrency 20
python scripts/run_crash_recovery.py
agent-runtime doctor --json
python scripts/run_reliability.py --stress-runs 100 --concurrency 20 --soak-seconds 1800
```

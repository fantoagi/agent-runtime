# Agent Runtime 当前状态

- **当前版本**：`0.8.8`
- **当前里程碑**：v0.8.8 Verified Task Completion
- **Runtime 构建完成时间**：2026-08-18（Asia/Shanghai）
- **文档体系构建完成时间**：2026-08-11（Asia/Shanghai）
- **当前代码基线 commit**：`pending`
- **最近演进记录**：[E2026-08-18-002](./CHANGELOG.md#e2026-08-18-002)、[E2026-08-18-001](./CHANGELOG.md#e2026-08-18-001)、[E2026-08-17-004](./CHANGELOG.md#e2026-08-17-004)

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
| Tool Registry 与安全执行 | ✅ stable | 参数校验、有界线程池、背压、超时、取消、UNKNOWN、原子文件写入和 capability 决策 | E2026-08-15-005、E2026-08-16-004 |
| SQLite Event Log 与恢复 | ✅ stable | WAL、FULL、quick_check、busy retry、事务 sequence、AgentDefinition 快照和 schema 1–8 迁移 | E2026-08-15-006、E2026-08-15-009、E2026-08-15-010 |
| Runtime 生命周期与准入 | ✅ stable | `shutdown()`、跨进程 `wait()`、崩溃协调、顶层 Run 容量限制和模型请求并发限制 | E2026-08-15-006、E2026-08-15-008、E2026-08-15-009 |
| 本地稳定 Runtime 入口 | ✅ stable | TOML 配置、`init/serve/status`、loopback、单 Owner Lock、轮转日志和本地验收脚本 | E2026-08-16-005 |
| Interactive CLI | ✅ stable | `agent-runtime chat`、实时 `model.delta`、Session 多轮上下文、Slash Command、终端 Approval、Ctrl+C 取消、磁盘输入历史和 `--continue/--resume/--print` | E2026-08-16-006 |
| Coding Workspace Tool Loop | ✅ stable | 文件列表、噪声过滤、截断后继续发现、文本搜索、有界可续读行读取、单文件替换、批量精确 Patch、Git status/diff、原子写入、白名单 argv 验证、一次性完成证据检查以及 `/workspace`、`/diff` | E2026-08-17-001、E2026-08-17-002、E2026-08-17-003、E2026-08-18-001、E2026-08-18-002 |
| Project-aware Workspace Context | ✅ stable | Bounded automatic `AGENTS.md`/`CLAUDE.md` loading, built-in coding protocol, AgentDefinition snapshot traceability, and source-only CLI/status projection | E2026-08-17-004 |
| FastAPI 与 SSE | ✅ stable | 健康检查、heartbeat、断线恢复、Runtime 所有权、幂等提交和 429 背压 | E2026-08-15-007、E2026-08-15-009 |
| 多 Agent Workflow | ✅ stable | Parent/Child、串行/并行、幂等委派、取消传播、Workflow 与 AgentDefinition 确切快照 | E2026-08-14-007、E2026-08-15-006、E2026-08-15-010 |
| Context、Session、Memory 与 Artifact | ✅ stable | token budget、FTS5 scoped memory、TTL、软删除、大结果 Artifact 化和同 Run Tool Result Artifact 分页读取 | E2026-08-15-001、E2026-08-18-001 |
| Observability、Evals 与 Learning Console | ✅ stable | Trace Tree、p95/失败 Metrics、综合诊断、结构化日志、确定性根因摘要、脱敏诊断包、Eval、动态泳道和可靠性状态 | E2026-08-15-002、E2026-08-15-003、E2026-08-15-007、E2026-08-16-002、E2026-08-16-003 |
| Runtime Doctor 与 Crash Matrix | ✅ stable | 只读一致性诊断；模型、Tool、Approval、Workflow 强杀恢复，恢复进程无需重新注册 AgentDefinition | E2026-08-15-008、E2026-08-15-010 |
| 在线备份与灾难恢复 | ✅ stable | SQLite Online Backup、Artifact 归档、SHA-256/quick_check 校验、离线恢复和回滚副本 | E2026-08-16-001 |
| 质量与发布门禁 | ✅ stable | Ruff、Mypy strict、coverage、跨平台 CI、Wheel smoke、stress/soak/crash | E2026-08-15-004、E2026-08-15-007、E2026-08-15-008 |

## 部分实现或实验能力

| 能力 | 状态 | 当前边界 | 演进记录 |
| --- | --- | --- | --- |
| 多 Runtime 连接同一 SQLite | 🧪 experimental | 支持并发事件写入，但不提供多进程任务调度 | E2026-08-15-006 |
| 30 分钟长稳与性能基线 | 🧪 experimental | 已进入 Nightly；发布前必须跑满 | E2026-08-15-007 |
| LocalProcessSandbox 与 Tool Capability | 🧪 experimental | argv、白名单、Workspace cwd、环境、timeout、输出、并发和进程树取消；不是容器强隔离 | E2026-08-16-004 |

## 计划能力

| 能力 | 状态 | 说明 | 演进记录 |
| --- | --- | --- | --- |
| Docker Sandbox / SecretProvider | 📋 planned | 已移出本地稳定版关键路径；只有真实本地使用产生明确需求后再启动 | E2026-08-16-004、E2026-08-16-005 |
| 分布式 Worker 与 Queue | 📋 planned | 不属于当前单机可靠性范围 | E2026-08-15-007 |
| 多租户与权限治理 | 📋 planned | 等待身份、审计和隔离模型设计 | E2026-08-15-007 |

## 当前明确不支持

| 能力 | 状态 | 原因 | 演进记录 |
| --- | --- | --- | --- |
| 强杀同步 Python 线程 | ⛔ unsupported | 线程不能安全强杀；副作用结果转为 UNKNOWN | E2026-08-15-005 |
| 自动数据库降级 | ⛔ unsupported | 迁移只允许向前，避免破坏历史数据 | E2026-08-15-006 |
| 分布式调度与跨主机恢复 | ⛔ unsupported | 当前目标是单机 Runtime | E2026-08-15-007 |
| 不可信代码强隔离 | ⛔ unsupported | LocalProcessSandbox 不是容器或虚拟机；等待后续 DockerSandbox 与攻击面测试 | E2026-08-16-004 |

## 当前已知限制

- SQLite 适合单机和中等并发，不等同于分布式数据库或任务队列。
- 同步副作用 Tool 超时后只能标记 `UNKNOWN` 并等待人工确认，不能保证回滚。
- Workflow 与普通 Run 可从不可变 AgentDefinition 快照恢复；Python Tool Handler 和 Model Provider 实现仍必须由新进程提供。
- Nightly 的 30 分钟 soak 不作为每个 PR 的阻塞时长。
- LocalProcessSandbox 不提供网络、系统调用或解释器内部行为的强隔离，不能用于执行任意不可信代码。
- `runtime.lock` 是标准 `serve` 和 embedded `chat` 的本机单 Owner 约束，两者不能在同一状态目录并行；它不是跨主机 Lease，Python SDK 仍由调用方负责实例所有权。
- 本地服务只允许 loopback，不提供认证、公网访问或后台 Windows Service 安装。
- Coding Tool 只面向可信本地 Workspace；`run_process` 仍不是容器强隔离。`apply_patch` 支持多个已有文本文件的批量精确替换，但不是 unified diff，不支持文件创建/删除/移动，也不承诺崩溃级多文件事务；Git Tool 只读，不提供自动提交、推送或自动批准。
- Interactive CLI 的 Session 历史只重建已完成 Run 的 user input 和 final assistant result，不回放旧 Tool 中间消息；终端输入历史文件也不是 Runtime 执行事实。 Completion Policy 只验证修改后的 diff/命令证据，不判断业务逻辑正确性；每个 Run 最多自动提醒一次，仍缺证据时以 `unverified` 完成。
- Learning Console 是教学与诊断 Adapter，不是生产运维控制台。`max_inflight_runs` 是单进程容量，不是分布式全局配额。`v0.7.10+` 备份只能恢复到原数据库和 Artifact 绝对路径。结构化日志不替代 SQLite 恢复事实。诊断包采用允许列表且不能用于恢复，对外发送前仍需人工复核。

## 当前测试状态

- 自动化测试：`279 passed`（2026-08-18，本地 Python 3.13），包含单元、集成、Interactive CLI、Artifact 分页、防递归、Workspace 发现、Verified Task Completion、配置边界、跨进程 Owner Lock、定义快照、幂等并发、容量背压、真实进程强杀与备份恢复测试。
- 总体 coverage：`84.22%`；Core line coverage：`91.50%`；core branch coverage：`80.67%`。
- PR：Ubuntu Python 3.11/3.12/3.13、Windows Python 3.13。
- Nightly：100 并发、20 轮 Crash Matrix、备份恢复演练、故障测试重复、30 分钟 soak 和性能回退检查。

## 当前运行方式

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[api,quality]"
python scripts/check_docs.py
python -m pytest
agent-runtime init
agent-runtime status
agent-runtime chat
# 或启动 HTTP 服务；同一状态目录下不要与 chat 同时运行
agent-runtime serve
```

### FastAPI / SSE

标准本地入口：

```powershell
agent-runtime serve
Invoke-RestMethod http://127.0.0.1:8000/health
```

确定性教学 Demo 仍可使用 `uvicorn agent_runtime.api.app:app --reload`。

### 可靠性快速验证

```powershell
python scripts/run_reliability.py --stress-runs 20 --concurrency 20
python scripts/run_crash_recovery.py
agent-runtime doctor --json
python scripts/run_reliability.py --stress-runs 100 --concurrency 20 --soak-seconds 1800
```

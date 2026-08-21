# Agent Runtime 当前状态

- **当前版本**：`0.8.24`
- **当前里程碑**：v0.8.24 Acceptance Comparator Error Containment
- **Runtime 构建完成时间**：2026-08-21（Asia/Shanghai）
- **文档体系构建完成时间**：2026-08-11（Asia/Shanghai）
- **当前代码基线 commit**：`3c26382`
- **最近演进记录**：[E2026-08-21-004](./CHANGELOG.md#e2026-08-21-004)、[E2026-08-21-003](./CHANGELOG.md#e2026-08-21-003)、[E2026-08-21-002](./CHANGELOG.md#e2026-08-21-002)、[E2026-08-21-001](./CHANGELOG.md#e2026-08-21-001)、[E2026-08-20-002](./CHANGELOG.md#e2026-08-20-002)、[E2026-08-20-001](./CHANGELOG.md#e2026-08-20-001)、[E2026-08-19-010](./CHANGELOG.md#e2026-08-19-010)、[E2026-08-19-009](./CHANGELOG.md#e2026-08-19-009)、[E2026-08-19-008](./CHANGELOG.md#e2026-08-19-008)、[E2026-08-19-007](./CHANGELOG.md#e2026-08-19-007)、[E2026-08-19-006](./CHANGELOG.md#e2026-08-19-006)、[E2026-08-19-005](./CHANGELOG.md#e2026-08-19-005)、[E2026-08-19-004](./CHANGELOG.md#e2026-08-19-004)、[E2026-08-19-003](./CHANGELOG.md#e2026-08-19-003)、[E2026-08-19-002](./CHANGELOG.md#e2026-08-19-002)、[E2026-08-19-001](./CHANGELOG.md#e2026-08-19-001)、[E2026-08-18-003](./CHANGELOG.md#e2026-08-18-003)

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
| Tool Registry 与安全执行 | ✅ stable | 参数校验、有界线程池、背压、超时、取消、UNKNOWN、原子文件写入、capability 决策、同 Run 只读结果复用、证据感知 no-progress、Fresh Finalization Context、无工具最终综合、Unicode 兼容的文本化 Tool Call 防护，以及新建文件的 durable `created`/Git status 完成证据 | E2026-08-15-005、E2026-08-16-004、E2026-08-19-005、E2026-08-19-006、E2026-08-19-008、E2026-08-19-009、E2026-08-19-010 |
| SQLite Event Log 与恢复 | ✅ stable | WAL、FULL、quick_check、busy retry、事务 sequence、AgentDefinition 快照和 schema 1–8 迁移 | E2026-08-15-006、E2026-08-15-009、E2026-08-15-010 |
| Runtime 生命周期与准入 | ✅ stable | `shutdown()`、跨进程 `wait()`、崩溃协调、顶层 Run 容量限制和模型请求并发限制 | E2026-08-15-006、E2026-08-15-008、E2026-08-15-009 |
| 本地稳定 Runtime 入口 | ✅ stable | TOML 配置、`init/serve/status`、loopback、单 Owner Lock、轮转日志和本地验收脚本 | E2026-08-16-005 |
| Interactive CLI | ✅ stable | `agent-runtime chat`、append-only Streaming Markdown、Inspecting/Editing/Verifying 阶段、单次 Tool-aware Approval、批准后连续事件流、聚焦差异预览、共享 validation 分类、可恢复 Tool 错误、`tool.reused`、no-progress 警告、无工具 finalization、inspection requested 降噪、incomplete/verified Task Summary、compact/verbose、Session 多轮上下文、Slash Command、Ctrl+C 取消和 `--continue/--resume/--print` | E2026-08-16-006、E2026-08-18-003、E2026-08-19-001、E2026-08-19-002、E2026-08-19-003、E2026-08-19-004、E2026-08-19-005、E2026-08-19-006、E2026-08-19-007、E2026-08-19-008、E2026-08-19-009、E2026-08-19-010 |
| Coding Workspace Tool Loop | ✅ stable | 文件列表、噪声过滤、截断后继续发现、文本搜索、错误参数修正提示、无扩展名路径候选、证据感知搜索/读取收敛、有界可续读行读取、单文件替换、批量精确 Patch、Git status/diff、原子写入、白名单 argv 验证、tracked diff、untracked status 与 post-change validation 分层完成证据检查，以及 `/workspace`、`/diff` | E2026-08-17-001、E2026-08-17-002、E2026-08-17-003、E2026-08-18-001、E2026-08-18-002、E2026-08-19-005 |
| Project-aware Workspace Context | ✅ stable | Bounded automatic `AGENTS.md`/`CLAUDE.md` loading, built-in coding protocol, AgentDefinition snapshot traceability, and source-only CLI/status projection | E2026-08-17-004 |
| FastAPI 与 SSE | ✅ stable | 健康检查、heartbeat、断线恢复、Runtime 所有权、幂等提交和 429 背压 | E2026-08-15-007、E2026-08-15-009 |
| 多 Agent Workflow | ✅ stable | Parent/Child、串行/并行、幂等委派、取消传播、Workflow 与 AgentDefinition 确切快照 | E2026-08-14-007、E2026-08-15-006、E2026-08-15-010 |
| Context、Session、Memory 与 Artifact | ✅ stable | token budget、当前 Run 请求 pin、finalization 原始问题重申、Fresh Finalization Context、Unicode/重复竖线 DSML 变体检测、文本化 Tool Call 一次有界修复、FTS5 scoped memory、TTL、软删除、大结果 Artifact 化和同 Run Tool Result Artifact 分页读取 | E2026-08-15-001、E2026-08-18-001、E2026-08-19-007、E2026-08-19-008、E2026-08-19-009 |
| Observability、Evals 与 Learning Console | ✅ stable | Trace Tree、p95/失败 Metrics、综合诊断、结构化日志、确定性根因摘要、脱敏诊断包、确定性 Eval、隔离真实模型 Acceptance Suite、严格 Scope compare、显式 partial compare、动态泳道、可靠性状态和 Fresh Context/finalization 协议事件解释 | E2026-08-15-002、E2026-08-15-003、E2026-08-15-007、E2026-08-16-002、E2026-08-16-003、E2026-08-19-008、E2026-08-20-001、E2026-08-20-002、E2026-08-21-001、E2026-08-21-002、E2026-08-21-003 |
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
- Interactive CLI 的 Session 历史只重建已完成 Run 的 user input 和 final assistant result，不回放旧 Tool 中间消息；终端输入历史文件也不是 Runtime 执行事实。Streaming Markdown 在稳定块边界 append，不使用累计区域重绘；没有空行的长段落可能延迟到内容段结束，display mode 和执行阶段不跨进程持久化。Completion Policy 只验证修改后的 Git/命令证据，不判断业务逻辑正确性；tracked 修改要求 `git_diff`，`write_text_file(created=true)` 的新建文件在 `git_status` 可用时还要求后续成功检查 status；所有 Git/validation 证据都必须位于最后一次成功写入之后，写入前的成功命令不计入完成证明；每个 Run 最多自动提醒一次，仍缺证据时以 `unverified` 完成。CLI 只从 durable Tool Event 判断同名错误是否已被后续成功恢复，不从模型自由文本猜测 clarification 或执行状态。只读结果复用仅覆盖固定白名单内、Tool 名与参数完全一致且中间没有副作用 Tool 的当前 Run 调用；它不会复用失败、UNKNOWN、审批中或副作用结果。
- Finalization 的流式内容会先在 Runtime 内有界缓冲并通过文本化 Tool Call 检查，再作为单个 `model.delta` 发布；因此该最后一轮牺牲逐 token 展示来避免 DSML/XML/JSON 协议文本泄漏。协议修复最多一次，第二次违规会使 Run 明确失败。DSML 检测会先做 Unicode NFKC 兼容归一化，并只在纯 envelope 边界内接受重复竖线和有限空白；归一化只用于识别，不会把文本转换成可执行 Tool Call。
- 真实模型 Acceptance Suite 使用隔离合成 Workspace，并默认只报告结构统计和哈希；它不评价答案深层语义，一次通过也不代表长期稳定。2026-08-20 的 `deepseek-v4-flash` 基线为 5 Cases × 3 repeats、15/15 attempts 通过且 failed assertions 为 0；该结果仍只代表当次 Provider/Runtime 组合。修改 Case 依赖本机 Git、启用的 `run_process` 和 pytest 环境。
- Learning Console 是教学与诊断 Adapter，不是生产运维控制台。`max_inflight_runs` 是单进程容量，不是分布式全局配额。`v0.7.10+` 备份只能恢复到原数据库和 Artifact 绝对路径。结构化日志不替代 SQLite 恢复事实。诊断包采用允许列表且不能用于恢复，对外发送前仍需人工复核。

## 当前测试状态

- 自动化测试：`370 passed`（2026-08-21，本地 Python 3.13），包含单元、集成、append-only Streaming Markdown、统一 validation 阶段、Tool-aware Approval、可恢复 Tool 错误、只读 Tool 结果复用、证据感知 no-progress、无工具 finalization、文本化 Tool Call 有界修复、全角/双竖线 DSML 变体防护、参数/路径修复提示、结构化 Task Summary、重复输出回归、compact/verbose、Interactive CLI、Artifact 分页、防递归、Workspace 发现、Verified Task Completion、配置边界、跨进程 Owner Lock、定义快照、幂等并发、容量背压、真实模型验收 Suite schema、隔离/脱敏、指标/断言、Approval lifecycle、真实进程强杀与备份恢复测试。
- 总体 coverage：`85.01%`；Core line coverage：`91.84%`；core branch coverage：`81.05%`。
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
# 调用已配置真实模型运行隔离稳定性验收；可能产生模型费用
agent-runtime eval run --suite local-real-model --case explain-project
# 或启动 HTTP 服务；同一状态目录下不要与 chat/eval run 同时运行
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

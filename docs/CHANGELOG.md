# Agent Runtime Evolution Log

> 规则：按完成时间倒序排列。每条记录必须关联代码范围、测试和 Git commit；提交前 commit 可暂记为 `pending`，合并前必须补全。

---

<a id="e2026-08-21-003"></a>
## E2026-08-21-003：v0.8.23 Acceptance Scope Integrity

- **完成时间**：2026-08-21
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/acceptance.py`
  - `src/agent_runtime/acceptance_compare.py`
  - `src/agent_runtime/cli.py`
  - `tests/test_acceptance.py`
  - `tests/test_acceptance_compare.py`
  - `src/agent_runtime/version.py`
  - `pyproject.toml`
  - `README.md`
  - `docs/REAL_MODEL_EVALS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CURRENT.md`
  - `docs/CHANGELOG.md`
  - `docs/ROADMAP.md`
  - `docs/adr/README.md`
  - `docs/adr/0046-acceptance-scope-integrity.md`
- **关联 commit**：`pending`
- **关联 ADR**：[ADR-0046](./adr/0046-acceptance-scope-integrity.md)

### 变更摘要

修正验收报告范围不一致时的“假通过”风险。严格比较现在要求 Baseline 和 Candidate 的 `case_name + attempt` 集合完全一致；只有显式使用 `--case` 时才允许部分比较。

### 系统架构

- Acceptance Runner 在报告中持久化 `selection.case_names`、`selection.repeat`、`expected_attempts` 和 `actual_attempts`。
- 比较器先校验 Suite identity 和报告 Scope，再执行 Case 级回归判断。
- 范围不同返回 `incompatible`，不再把额外 Candidate Attempt 作为普通 warning 后返回 `passed`。
- 显式 `--case` 的结果返回 `partial`，并要求被选 Case 的 Attempt 在两份报告中完整存在。

### 实现方式

- `AcceptanceReport` 使用兼容的 selection 元数据描述本次实际验收范围。
- `compare_acceptance_reports` 默认先做 Suite/Scope 合法性检查，再按 `(case_name, attempt)` 对齐执行回归判断。
- 严格模式发现缺失或额外 Attempt 时返回 `incompatible`；CLI 的 `--case` 显式选择才启用 `partial` 比较。
- 旧报告缺失 selection 时允许从结果推断，并通过 warning 提示重新生成基线。
- 比较过程只读取脱敏 JSON，不启动 Runtime、不打开验收数据库、不调用模型。

### 当前功能

```powershell
agent-runtime eval compare .\baseline.json .\candidate.json
agent-runtime eval compare .\baseline.json .\candidate.json --case approval-lifecycle
```

退出码保持兼容：`0` 表示 passed/partial 且无回归，`1` 表示真实回归，`2` 表示 incompatible 或报告格式错误。

### 已知限制

旧报告没有 selection 元数据时会从结果推断范围并给出 warning；建议新基线使用 v0.8.23 及以上生成。比较器仍不判断最终答案语义，也不做性能统计显著性分析。

### 测试与验收

新增测试覆盖 selection 元数据、`actual_attempts` 一致性、严格范围不一致、显式部分比较和不完整报告；本地 Python 3.13 全量 `369 passed`，总体 coverage `84.97%`，Core line/branch coverage `91.81% / 80.95%`，Wheel 干净安装 smoke 通过。

### 后续计划

用 v0.8.23 重新生成完整 5 Case × 3 repeats 基线，再对后续 Runtime 修改执行同范围 compare。

---

<a id="e2026-08-21-002"></a>
## E2026-08-21-002：v0.8.22 Acceptance Report Regression Gate

- **完成时间**：2026-08-21
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/acceptance_compare.py`
  - `src/agent_runtime/cli.py`
  - `tests/test_acceptance_compare.py`
  - `src/agent_runtime/version.py`
  - `pyproject.toml`
  - `README.md`
  - `docs/REAL_MODEL_EVALS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CURRENT.md`
  - `docs/CHANGELOG.md`
  - `docs/ROADMAP.md`
  - `docs/adr/README.md`
  - `docs/adr/0045-acceptance-regression-gate.md`
- **关联 commit**：`85c5dc5`
- **关联 ADR**：[ADR-0045](./adr/0045-acceptance-regression-gate.md)

### 变更摘要

增加真实模型验收报告的离线对比能力，让一次新的真实模型运行可以与历史基线进行可重复的回归判定，而不需要再次调用模型或把原始 Prompt、Tool 参数和答案写入比较结果。

### 系统架构

- `RealModelAcceptanceRunner` 继续负责隔离 Workspace、durable Run 和脱敏报告。
- 新增 `compare_acceptance_reports` 作为报告层的纯函数式 Gate，不进入 Runtime 执行路径。
- CLI 增加 `agent-runtime eval compare <baseline> <candidate>`，读取两个已落盘 JSON 并输出结构化比较结果。
- 比较以 suite identity 和 `case_name + attempt` 对齐；模型、Provider、Runtime 版本变化是 warning，不自动当作失败。

### 实现方式

- 以前通过的 Attempt 在候选报告中失败，判定为 `case_failed`。
- `verified` 退化、协议违规增加、UNKNOWN Tool Outcome 增加，分别判定为可靠性回归。
- Suite checksum/version/name 不一致时返回 `incompatible`，防止把不同验收集合误比较。
- 缺失 Attempt 是失败；新增 Attempt、模型变化和超过 20% 的单 Case 耗时增加记录为 warning。
- 比较器只读取脱敏报告，不创建 Runtime、不打开 SQLite、不发起 Provider 请求。

### 当前功能

- 可以在真实模型验收完成后离线执行回归比较。
- 退出码为 `0` 表示无回归，`1` 表示发现回归，`2` 表示报告不可比较或格式错误。
- 保留完整报告中的 Run/Trace ID，发现回归后仍可按 ID 回看对应 Case 的 durable Event。

### 已知限制

- 性能变化目前只作为 warning，不建立固定 Runner 的统计显著性门禁。
- 比较器不判断答案语义，也不替代 Case 自身的断言。
- 仍需使用同一 Suite checksum；有意修改 Case 后必须生成新的基线。

### 测试与验收

新增离线测试覆盖通过、失败、验证证据退化、协议/UNKNOWN 退化、Suite 不兼容和重复结果；不调用真实模型。

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_acceptance_compare.py
agent-runtime eval compare .\baseline.json .\candidate.json
```

### 后续计划

在得到下一轮真实模型报告后，先执行离线 compare，再只针对可复现回归进入 Runtime 修复；不因为单次答案文本波动增加 Provider 特判。

---

<a id="e2026-08-21-001"></a>
## E2026-08-21-001：v0.8.21 Post-change Verification Evidence

- **完成时间**：2026-08-21
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/acceptance.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_acceptance.py`
  - `tests/test_api.py`
  - `tests/test_incident.py`
  - `tests/test_lab_api.py`
  - `tests/test_observability.py`
  - `scripts/verify_distribution.py`
  - `README.md`
  - `docs/README.md`
  - `docs/REAL_MODEL_EVALS.md`
  - `docs/CODING_TOOLS.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/ROADMAP.md`
  - `docs/adr/README.md`
  - `docs/adr/0044-post-change-verification-boundary.md`
  - `pyproject.toml`
- **关联 commit**：`18c90e8`
- **关联 ADR**：[ADR-0044](./adr/0044-post-change-verification-boundary.md)

### 变更摘要

修正 Acceptance Metrics 的验证时间边界。此前 v0.8.20 已要求新建文件补充 `git_status`，但 Acceptance 仍可能把最后一次写入之前执行的 pytest、git diff 或 git status 计入最终修改证据。v0.8.21 让验收指标与 Completion Policy 一致，只接受最后一次成功写入之后的 post-change 证据。

### 系统架构

不改变 Runtime 状态机、Provider 协议、Tool Handler、Event schema 或 SQLite schema。变化发生在基于 durable ToolExecution 派生 Completion/Acceptance Metrics 的边界：最后一次成功副作用写入将后续执行划分为修改后的验证阶段。

### 实现方式

`AcceptanceMetrics` 先定位最后一次成功的 `write_text_file`、`replace_text` 或 `apply_patch`，然后仅从其后的 ToolExecution 统计 `git_diff`、`git_status`、validation attempts 和 validation successes。写入前的成功测试不再满足代码修改验证；写入前的 Git 检查也不再满足最终 Workspace 检查。无成功写入的只读 Run 仍为 `not_required`。

### 当前功能

对于代码修改任务，标准完成证据现在要求：最后一次成功写入之后成功检查 Git diff；创建新文件时还要在该边界之后成功执行 Git status；代码文件还要在该边界之后运行并通过可识别的 validation 命令。Acceptance 报告与 Runtime Completion Policy 使用相同的时间语义。

### 已知限制

该规则只判断证据是否发生在最后一次写入之后，不判断测试是否真正覆盖业务逻辑，也不判断 diff 内容是否满足用户意图。一个 Tool 原子修改多个文件时它们共享同一写入边界；未来若引入多阶段修改，需要单独定义阶段级证据关系。

### 测试与验收

新增回归测试验证“先 pytest、后写入、再仅 git diff”不会被标记为 verified；本地 Python 3.13 全量结果为 `360 passed`，总体 coverage 为 `85.00%`，core line coverage 为 `91.87%`，core branch coverage 为 `81.01%`；Ruff、Mypy strict、文档门禁和 coverage 门禁通过；已成功构建 `agent_runtime-0.8.21.tar.gz` 与 `agent_runtime-0.8.21-py3-none-any.whl`，并在干净虚拟环境完成 CLI、SDK、FastAPI/SSE、备份恢复、诊断和 Wheel smoke。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests scripts
.\.venv\Scripts\python.exe -m mypy src/agent_runtime
.\.venv\Scripts\python.exe -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -q
.\.venv\Scripts\python.exe scripts/check_coverage.py coverage.json
python scripts/check_docs.py
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.8.21-py3-none-any.whl
```

### 后续计划

使用同一真实模型重跑 `small-verified-edit --repeat 3` 和 `approval-lifecycle --repeat 3`，确认模型在最后一次写入后再次执行 diff/status/validation；若出现失败，只根据对应 durable Run/Event/ToolExecution 事实做下一轮最小修复。

---
<a id="e2026-08-20-002"></a>
## E2026-08-20-002：v0.8.20 New-file Verification Evidence

- **完成时间**：2026-08-20
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/tools.py`
  - `src/agent_runtime/completion.py`
  - `src/agent_runtime/acceptance.py`
  - `src/agent_runtime/eval_suites/local-real-model.json`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_completion.py`
  - `tests/test_acceptance.py`
  - `tests/test_reliability.py`
  - `tests/test_api.py`
  - `tests/test_incident.py`
  - `tests/test_lab_api.py`
  - `tests/test_observability.py`
  - `scripts/verify_distribution.py`
  - `README.md`
  - `docs/README.md`
  - `docs/REAL_MODEL_EVALS.md`
  - `docs/CODING_TOOLS.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/ROADMAP.md`
  - `docs/adr/README.md`
  - `docs/adr/0043-new-file-verification-evidence.md`
  - `pyproject.toml`
- **关联 commit**：`3c4187e`
- **关联 ADR**：[ADR-0043](./adr/0043-new-file-verification-evidence.md)

### 变更摘要

基于 v0.8.19 首次完整真实模型基线的 durable 事实，修复新建未跟踪文件只调用 `git_diff` 也可能被误标 `verified` 的证据漏洞。该基线使用 `deepseek-v4-flash` 执行 5 Cases × 3 repeats，15/15 attempts 通过且 failed assertions 为 0；修复来自通过结果背后的 ToolExecution/Git 事实检查，不是模型名称或答案文本特判。

### 系统架构

Completion Evidence 现在明确区分 tracked diff 和 untracked status。`write_text_file` 在 durable result 中记录写入前目标是否不存在；Completion Policy 与 Real-model Acceptance 从同一事实判断新文件是否需要 `git_status`。Run 状态机、Event schema、Provider 协议和 SQLite schema 均未变化。

### 实现方式

`write_text_file` 兼容性增加 `created: bool`。当最后一次成功写入包含 `created=true` 且标准 Runtime 注册了 `git_status` 时，`CodingCompletionPolicy` 要求后续成功执行 `git_status`，否则记录明确 unmet requirement 并保持 `unverified`。`AcceptanceMetrics` 同步增加 `created_file_writes` 和 `git_status_inspected`；`approval-lifecycle` Case 现在显式要求 status，`small-verified-edit` Fixture 用 `.gitignore` 排除 pytest/Python cache 噪声。

### 当前功能

标准本地 Coding Run 创建新文件后，需要同时具备 `git_diff` 和 `git_status` 证据；覆盖已有文件仍沿用 diff 证据。代码修改继续要求成功 validation。CLI、Checkpoint 和 Acceptance 报告可以从 durable result 解释为什么任务是 verified 或 unverified，不会自动 stage、commit 或修改 Git 状态。

### 已知限制

该规则只覆盖当前会创建文件的内置 `write_text_file`；旧 Run 没有 `created` 字段，不会追溯重分类。Git status 证明的是 Workspace 变化存在，不判断内容业务正确性。未注册 `git_status` 的通用 SDK Runtime 不会被强制增加不可用的要求。真实模型存在随机性，15/15 只代表该次模型与 Runtime 组合。

### 测试与验收

新增 Completion Policy 与 Acceptance 新文件证据回归，并验证原子写第一次返回 `created=true`、覆盖写返回 `created=false`。聚焦测试为 `69 passed`；本地 Python 3.13 全量结果为 `359 passed`。总体 coverage 为 `84.99%`，core line coverage 为 `91.87%`，core branch coverage 为 `81.01%`；Ruff、Mypy strict、文档门禁和 coverage 门禁通过；成功构建 `agent_runtime-0.8.20.tar.gz` 与 `agent_runtime-0.8.20-py3-none-any.whl`，并在干净虚拟环境完成 CLI、SDK、FastAPI/SSE、备份恢复、诊断和内置 Acceptance Suite 打包 smoke。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests scripts
.\.venv\Scripts\python.exe -m mypy src/agent_runtime
.\.venv\Scripts\python.exe -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -q
.\.venv\Scripts\python.exe scripts/check_coverage.py coverage.json
python scripts/check_docs.py
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.8.20-py3-none-any.whl
```

### 后续计划

使用相同真实 Provider 重跑 `approval-lifecycle --repeat 3`，确认每轮报告包含 `created_file_writes: 1`、`git_status_inspected: true` 和 `verification_status: verified`。后续继续只针对可复现失败断言和 durable 事实做最小修复。

---
<a id="e2026-08-20-001"></a>
## E2026-08-20-001：v0.8.19 Real-model Acceptance Baseline

- **完成时间**：2026-08-20
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/acceptance.py`
  - `src/agent_runtime/eval_suites/local-real-model.json`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/__init__.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_acceptance.py`
  - `tests/test_api.py`
  - `tests/test_incident.py`
  - `tests/test_lab_api.py`
  - `tests/test_observability.py`
  - `scripts/verify_distribution.py`
  - `README.md`
  - `docs/README.md`
  - `docs/REAL_MODEL_EVALS.md`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/CODING_TOOLS.md`
  - `docs/LOCAL_RUNTIME.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/ROADMAP.md`
  - `docs/adr/README.md`
  - `docs/adr/0042-real-model-acceptance-baseline.md`
  - `pyproject.toml`
- **关联 commit**：`3c4187e`
- **关联 ADR**：[ADR-0042](./adr/0042-real-model-acceptance-baseline.md)

### 变更摘要

建立真实模型稳定性验收基线，把此前依赖人工截图和单次体验的复测方式转换为固定 Suite、隔离合成 Workspace、durable Run/Event/ToolExecution 指标和可比较的脱敏报告。该版本不增加新的 Coding Tool 或编排能力，只为现有链路建立重复验收入口。

### 系统架构

新增 `RealModelAcceptanceRunner`，每个 Case 创建独立合成 Git Workspace、SQLite、Artifact 和日志，再通过 configured Local Runtime 的真实 Provider、Agent、Tool、Approval 和 resume 路径执行。验收层是 Runtime 外部 Adapter，不修改 Run 状态机、Event schema、Provider 协议或 SQLite migration。

### 实现方式

内置 `local-real-model` Suite 覆盖 explanation、inspection、small-edit、failure-recovery 和 lifecycle 五类任务。报告从 durable 事实派生步骤、Tool 计数、完全相同调用重复、失败/UNKNOWN、Approval、模型重试、收敛、协议违规、diff 和 validation 状态；只保存模型/版本、Run/Trace ID、计数、断言、最终答案长度及 SHA-256，不保存 Prompt、Fixture 内容、Tool arguments/result 或答案原文。CLI 新增 `agent-runtime eval run --suite local-real-model`，支持 `--case`、`--repeat` 和 `--output`。

### 当前功能

用户可以用当前已配置的真实模型运行五个隔离 Case，按确定性阈值检查 completion、convergence、Tool efficiency、protocol integrity、verification 和 Approval lifecycle。每次报告保存在 `<state-dir>/evals/<report-id>/acceptance-report.json`，最新报告同步到 `<state-dir>/evals/latest-report.json`；Case 的独立 SQLite 继续保留，可根据 Run ID 深入诊断。

### 已知限制

真实模型调用会产生费用并受网络、模型版本和随机性影响；一次通过不能代表长期稳定，重要变更建议 `--repeat 3`。第一版不使用 LLM-as-a-Judge，不评价答案文风和深层语义；合成小项目也不能覆盖大型真实 Workspace。修改 Case 依赖本机 Git、启用的 `run_process` 和可用 pytest 环境。

### 测试与验收

新增内置 Suite 加载、Suite schema 错误矩阵、危险 Fixture 路径拒绝、Git Fixture 初始化、隔离执行、报告内容脱敏、Case 选择/repeat、指标与断言、Tool 验证识别和 Approval lifecycle 测试。本地 Python 3.13 全量结果为 `357 passed`；总体 coverage 为 `84.96%`，core line coverage 为 `91.84%`，core branch coverage 为 `80.94%`。Ruff、Mypy strict、文档门禁和 coverage 门禁通过；sdist/wheel 构建、干净虚拟环境安装、CLI/SDK/FastAPI/SSE smoke 以及内置 `local-real-model.json` Wheel 打包检查通过。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests scripts
.\.venv\Scripts\python.exe -m mypy src/agent_runtime
.\.venv\Scripts\python.exe -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -q
.\.venv\Scripts\python.exe scripts/check_coverage.py coverage.json
python scripts/check_docs.py
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.8.19-py3-none-any.whl
```

### 后续计划

先运行 `explain-project` 和完整 `local-real-model` Suite，记录真实 Provider 的首次 baseline。后续只针对可复现的失败断言和 durable Run 事实优化 Context/Evidence、Tool 收敛或修改后验证，不扩展新 Tool。

---

<a id="e2026-08-19-010"></a>
## E2026-08-19-010：v0.8.18 Fresh Finalization Context

- **完成时间**：2026-08-19
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/lab/explanations.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/version.py`
  - `tests/test_runtime.py`
  - `tests/test_lab_api.py`
  - `tests/test_api.py`
  - `tests/test_incident.py`
  - `tests/test_observability.py`
  - `scripts/verify_distribution.py`
  - `README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/CODING_TOOLS.md`
  - `docs/LOCAL_RUNTIME.md`
  - `docs/ROADMAP.md`
  - `docs/adr/README.md`
  - `docs/adr/0041-fresh-finalization-context.md`
  - `pyproject.toml`
- **关联 commit**：`9d269c3`
- **关联 ADR**：[ADR-0041](./adr/0041-fresh-finalization-context.md)

### 变更摘要

修复真实模型在 `tools=[]` finalization 中仍因原始 Tool-heavy 消息历史连续输出 DSML 的问题。v0.8.17 Guard 已能阻止错误完成，但 durable Run `run_29c2c0fc35364adfb0e86477ae6ba70a` 证明首次最终综合和一次修复都复用了包含 14 次 Tool 调用模式的旧上下文，最终只能以 ProviderProtocolError 失败。

### 系统架构

Finalization 现在是独立的模型输入边界。Runtime 不再把原 Agent system prompt、Assistant Tool Call、`role=tool` 或 Provider 私有协议传给最终综合请求，而是从 durable ToolExecution 构建有界 evidence digest，附加必要的纯文本 Session 摘要，并把完整 `run.input` 放在最后。

### 实现方式

新增 `_build_finalization_context()`、Session 纯文本摘要、ToolExecution evidence digest、SHA-256 去重和预算截断。Evidence 被标记为不可信数据；Event `convergence.finalization_context_built` 只保存计数和字符统计。普通 finalization 与一次 repair 均通过 `_request_model(context_override=...)` 使用 Fresh Context，Streaming 仍先缓冲校验。

### 当前功能

解释型只读任务达到收敛边界后，模型只看到严格自然语言综合指令、必要 Session 摘要、去重后的 durable 工具事实和原始问题。历史 Tool syntax、Tool Result role 和鼓励继续调用工具的 Agent prompt 不再进入该请求；Evidence 中出现的伪调用也不会执行。

### 已知限制

Fresh Context 只在自动 convergence finalization 中使用，普通模型步骤仍使用完整 ContextBuilder。Evidence digest 是有界纯文本，极大结果可能被截断；若已有工具证据本身不足，最终答案仍可能保守或不完整。真实 Provider 仍需复测确认自然语言收敛效果。

### 测试与验收

新增 Tool history 隔离、Agent prompt 排除、durable evidence 保留、Session 纯文本历史、重复证据去重、超长结果截断、一次 Fresh Context repair、重复违规失败和 Streaming 不泄漏回归。完整测试结果为 `330 passed`；总体 coverage 为 `84.53%`，core line coverage 为 `91.43%`，core branch coverage 为 `80.36%`；Ruff、Mypy strict、文档门禁、coverage 门禁、sdist/Wheel 构建和干净虚拟环境发行验证通过。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests scripts
.\.venv\Scripts\python.exe -m mypy src/agent_runtime
.\.venv\Scripts\python.exe -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -q
.\.venv\Scripts\python.exe scripts/check_coverage.py coverage.json
python scripts/check_docs.py
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.8.18-py3-none-any.whl
```

### 后续计划

使用同一真实问题和真实 Provider 重新运行 Interactive CLI，确认 `convergence.finalization_context_built` 后直接得到自然语言回答；若仍失败，只依据新的 durable Event 和响应事实继续优化，不新增无证据的协议分支。

---
<a id="e2026-08-19-009"></a>
## E2026-08-19-009：v0.8.17 DSML Variant Guard

- **完成时间**：2026-08-19
- **状态**：✅ stable
- **类型**：bugfix
- **影响范围**：
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_runtime.py`
  - `tests/test_api.py`
  - `tests/test_lab_api.py`
  - `tests/test_incident.py`
  - `tests/test_observability.py`
  - `scripts/verify_distribution.py`
  - `README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/CODING_TOOLS.md`
  - `docs/LOCAL_RUNTIME.md`
  - `docs/ROADMAP.md`
  - `docs/adr/README.md`
  - `docs/adr/0040-dsml-variant-detection.md`
  - `pyproject.toml`
- **关联 commit**：`9d269c3`
- **关联 ADR**：[ADR-0040](./adr/0040-dsml-variant-detection.md)

### 变更摘要

修复 v0.8.16 DSML Guard 被真实模型的 Unicode 全角双竖线输出绕过的问题。持久化 Run `run_b29171c707834365866203386194f49d` 证明 Provider 返回的是 `<｜｜DSML｜｜tool_calls>`，而不是终端渲染产生的视觉空白；旧实现因此把伪 Tool Call 写入 `model.delta`、`run.result` 并错误标记 completed。

### 系统架构

Finalization 协议边界保持不变：文本 Tool Call 永不执行，首次违规最多修复一次，重复违规则明确失败，Streaming 内容先缓冲再校验。DSML 检测从精确 ASCII 前缀升级为“Unicode 兼容归一化 + 有限 envelope 语法”检查，仅扩大识别层，不改变 Tool、Provider、Event 或 SQLite 公共协议。

### 实现方式

检测副本先执行 Unicode NFKC，将全角 `｜` 还原为 ASCII；正则 marker 允许一个或多个竖线及有限空白，并限制 tag 为 `tool_calls`、`invoke`、`parameter`。只有响应以 DSML envelope 开始、包含 invoke，且每个非空行仍满足 DSML tag 边界时才命中。原始内容不改写、不解析、不送入 Tool Executor。

### 当前功能

`<｜｜DSML｜｜...>`、`<||DSML||...>` 和带有限 marker 空白的纯 DSML envelope 会触发既有 detection/repair 流程。连续两次变体输出会使 Run failed；带解释正文的全角 DSML 示例保持可返回；Streaming 拼接出的变体不会进入 durable `model.delta` 或成功 `run.result`。

### 已知限制

检测器仍只覆盖有真实证据或明确合同的 DSML/XML/JSON envelope，不尝试识别任意 Provider 私有协议。Finalization 成功流仍以完整缓冲后单个 delta 发布。纯协议 envelope 教学请求若恰好进入自动 finalization，仍可能被保守拦截。

### 测试与验收

新增真实全角双竖线 DSML、ASCII spaced/double-pipe DSML、连续两次变体违规、全角协议解释反例及 Streaming 分片变体回归；验证非法内容不会成为成功结果。完整测试结果为 `329 passed`；总体 coverage 为 `84.53%`，core line coverage 为 `91.46%`，core branch coverage 为 `80.53%`；Ruff、Mypy strict、文档门禁、coverage 门禁、sdist/Wheel 构建和干净虚拟环境发行验证通过。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests scripts
.\.venv\Scripts\python.exe -m mypy src/agent_runtime
.\.venv\Scripts\python.exe -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -q
.\.venv\Scripts\python.exe scripts/check_coverage.py coverage.json
python scripts/check_docs.py
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.8.17-py3-none-any.whl
```

### 后续计划

使用同一真实问题重新运行 Interactive CLI，确认模型最终返回上下文组织说明而非文本 Tool Call；在该问题稳定通过前，不扩展新的 Tool 或编排功能。

---
<a id="e2026-08-19-008"></a>
## E2026-08-19-008：v0.8.16 Textual Tool-call Guard

- **完成时间**：2026-08-19
- **状态**：✅ stable
- **类型**：bugfix
- **影响范围**：
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/interactive/renderer.py`
  - `src/agent_runtime/lab/explanations.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/version.py`
  - `tests/test_runtime.py`
  - `tests/test_interactive.py`
  - `tests/test_lab_api.py`
  - `tests/test_api.py`
  - `tests/test_incident.py`
  - `tests/test_observability.py`
  - `scripts/verify_distribution.py`
  - `README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/CODING_TOOLS.md`
  - `docs/LOCAL_RUNTIME.md`
  - `docs/ROADMAP.md`
  - `docs/adr/README.md`
  - `docs/adr/0039-textual-tool-call-guard.md`
  - `pyproject.toml`
- **关联 commit**：`9d269c3`
- **关联 ADR**：[ADR-0039](./adr/0039-textual-tool-call-guard.md)

### 变更摘要

修复模型在 no-progress finalization 中绕过结构化 Tool Call 字段、把 DSML/XML/JSON 调用协议作为普通文本输出，导致 Runtime 将伪 Tool Call 当作最终答案并错误标记 Run completed 的问题。

### 系统架构

Finalization 响应在进入 Assistant Message、Step 和 Run 终态前新增协议完整性边界。Runtime 只识别主要或完整由 Tool envelope 构成的文本，不执行其中动作；首次命中进入一次有界 repair，第二次命中转为明确 Provider 协议失败。Finalization Streaming 在校验前由 Runtime 缓冲，通过后再发布单个 durable `model.delta`，防止无效 DSML/XML/JSON 先泄漏到 CLI、SSE 或 Learning Console。

### 实现方式

新增 DSML、XML 和已知 Tool JSON envelope 的保守检测器。`_request_finalization_model()` 始终使用 `tools=[]`，检测后写入 `convergence.textual_tool_call_detected`，保存 `convergence.finalization_repair_requested` Checkpoint，追加 system repair note 和原始 user request，再请求一次纯自然语言答案。修复严格限制为一次；连续违规抛出 `ProviderProtocolError`。Interactive CLI 显示确定性修复提示，Learning Console 为 detection/repair Event 提供解释和状态投影。

### 当前功能

模型把 Tool Call 序列化为文本时，Runtime 不执行 Tool、不保存伪答案、不错误完成 Run。偶发一次协议漂移可自动恢复；持续违规会留下两个 detection Event、一个 repair Event 和明确失败原因。正文中仅解释 `<tool_call>`、普通 JSON 代码块或带额外解释字段的示例不会被误判。Streaming Provider 的无效内容在校验前不会发布。

### 已知限制

Finalization 的流式答案改为完整缓冲后一次发布，失去最后一轮逐 token 体验。自动 repair 会额外消耗一次模型调用。检测器有意保守，不尝试覆盖任意未知私有协议；纯 Tool envelope 教学请求若进入自动 finalization 可能被拦截。

### 测试与验收

新增 DSML、XML、直接 JSON、OpenAI-like JSON、连续违规、协议解释反例、普通 JSON 反例和 Streaming 缓冲修复测试；同步覆盖 CLI 修复提示和 Learning Console Event 解释。完整测试结果为 `326 passed`；总体 coverage 为 `84.52%`，core line coverage 为 `91.45%`，core branch coverage 为 `80.53%`；Ruff、Mypy strict、文档门禁、coverage 门禁、sdist/Wheel 构建和干净虚拟环境发行验证通过。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests scripts
.\.venv\Scripts\python.exe -m mypy src/agent_runtime
.\.venv\Scripts\python.exe -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -q
.\.venv\Scripts\python.exe scripts/check_coverage.py coverage.json
python scripts/check_docs.py
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.8.16-py3-none-any.whl
```

### 后续计划

继续基于真实模型复测解释任务和长检查任务；下一阶段只处理现有修改任务的有限修复回合与明确阻塞报告，不扩展新业务 Tool。

---
<a id="e2026-08-19-007"></a>
## E2026-08-19-007：v0.8.15 Finalization Context Integrity

- **完成时间**：2026-08-19
- **状态**：✅ stable
- **类型**：bugfix
- **影响范围**：
  - `src/agent_runtime/context.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_context_memory.py`
  - `tests/test_runtime.py`
  - `tests/test_api.py`
  - `tests/test_incident.py`
  - `tests/test_lab_api.py`
  - `tests/test_observability.py`
  - `scripts/verify_distribution.py`
  - `README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/CODING_TOOLS.md`
  - `docs/LOCAL_RUNTIME.md`
  - `docs/ROADMAP.md`
  - `docs/adr/README.md`
  - `docs/adr/0038-finalization-context-integrity.md`
  - `pyproject.toml`
- **关联 commit**：`9d269c3`
- **关联 ADR**：[ADR-0038](./adr/0038-finalization-context-integrity.md)

### 变更摘要

修复 no-progress finalization 已成功阻止重复检查，但最终模型偶尔声称看不到原始用户请求、并为纯解释问题输出无关文件修改状态的问题。当前 Run 的 durable `run.input` 现在具有独立的 Context 完整性合同；最终无 Tool 请求会重新聚焦确切原问题。

### 系统架构

Runtime 为当前请求和 finalization 请求重申使用专用 message name，ContextBuilder 将对应消息组固定为 pinned。它们不会被历史摘要省略，也不会在可选内容压缩时截断。该设计不增加 Event、SQLite schema 或公共 Provider/Tool 协议；旧 Checkpoint 在加载时从 durable Run 补齐标记。

### 实现方式

`_initial_messages()` 标记当前 Run 的 user message；`_build_model_context()` 在每次 Provider 请求前校验该消息存在；`_load_messages()` 兼容未带标记的历史 Checkpoint。进入 finalization 时，Runtime 先写入收敛 system note，再追加一条 role 保持为 `user`、内容等于 `run.input` 的尾部重申，并继续使用 `tools=[]`。原始用户文本不会被复制到 system role。finalization note 移除默认的 workspace-change 模板，只要求回答原问题并准确陈述实际动作。

### 当前功能

即使存在 Session 历史、多个 Tool Call/Result、Context Summary 或恢复旧 Checkpoint，最终模型请求仍包含完整当前用户问题。解释型任务不会再被 Runtime 提示诱导输出 `No files were modified`；模型也收到明确约束，不得声称用户请求不可见。ADR-0037 的证据感知 no-progress、Tool 禁用和协议绕过保护保持不变。

### 已知限制

为了不静默改变用户目标，极长 `run.input` 即使导致 Context 超预算也保持完整；Provider 的真实上下文上限仍由调用方配置。finalization 会有意重复一次原始 user message，以换取末尾聚焦和恢复确定性。Runtime 只能保证请求进入 Provider 消息，不能保证任意模型永远正确理解答案。

### 测试与验收

新增 Context 压缩下当前请求不丢失、不截断，以及 finalization 同时包含 pinned 原始请求、尾部 user 重申、`tools=[]` 和去除 workspace-change 模板的回归测试。完整测试结果为 `314 passed`；总体 coverage 为 `84.53%`，core line coverage 为 `91.51%`，core branch coverage 为 `80.66%`；Ruff、Mypy strict、文档门禁、coverage 门禁、sdist/Wheel 构建和干净虚拟环境发行验证通过。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests scripts
.\.venv\Scripts\python.exe -m mypy src/agent_runtime
.\.venv\Scripts\python.exe -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -q
.\.venv\Scripts\python.exe scripts/check_coverage.py coverage.json
python scripts/check_docs.py
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.8.15-py3-none-any.whl
```

### 后续计划

使用真实模型重跑“一次模型调用完整的请求消息是什么样的”等解释型问题，观察总 Tool 调用数和最终回答准确性；在不降低证据充分性的前提下，再通过 Eval 校准 warning/finalization 默认阈值。

---
<a id="e2026-08-19-006"></a>
## E2026-08-19-006：v0.8.14 Evidence-aware No-progress Guard

- **完成时间**：2026-08-19
- **状态**：✅ stable
- **类型**：improvement
- **影响范围**：
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/tools.py`
  - `src/agent_runtime/coding_tools.py`
  - `src/agent_runtime/interactive/renderer.py`
  - `src/agent_runtime/lab/explanations.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_runtime.py`
  - `tests/test_coding_tools.py`
  - `tests/test_contract_edges.py`
  - `tests/test_interactive.py`
  - `tests/test_lab_api.py`
  - `tests/test_api.py`
  - `tests/test_incident.py`
  - `tests/test_observability.py`
  - `scripts/verify_distribution.py`
  - `README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/CODING_TOOLS.md`
  - `docs/LOCAL_RUNTIME.md`
  - `docs/ROADMAP.md`
  - `docs/adr/README.md`
  - `docs/adr/0037-evidence-aware-convergence.md`
  - `pyproject.toml`
- **关联 commit**：`9d269c3`
- **关联 ADR**：[ADR-0037](./adr/0037-evidence-aware-convergence.md)

### 变更摘要

解决真实模型通过不同 query、不同路径或高度重叠行范围反复检查，最终触发 `Run reached its maximum ... model steps` 的问题。Runtime 现在按搜索命中、文件行区间、Artifact 字符区间和其他只读结果判断是否获得新证据；进展不足时先提示收敛，达到边界后发起一次不暴露 Tool 的最终综合。

### 系统架构

在现有 `tool.reused` 精确调用复用之上增加由 durable ToolExecution 派生的证据账本，不增加 SQLite schema。`convergence.warning` 和 `convergence.finalization_requested` 作为 durable Event 记录原因、检查次数与连续 no-progress 次数；Checkpoint 保存 Runtime 注入的 system note，因此重启后可以从持久化事实重建收敛状态。副作用 Tool 会重置当前证据账本，并阻止自动无工具 finalization。

### 实现方式

`search_text` 以 `(path, line)` 作为证据，`read_file_lines` 以 `(path, sha256)` 下的新行区间作为证据，`list_files`、`read_artifact` 和其他白名单只读 Tool 使用对应集合、区间或稳定摘要。默认在 10 次 inspection 或连续 2 次无进展时警告，在 14 次 inspection、连续 3 次无进展或即将达到 `max_steps` 时 finalization。最终请求通过 `tools_override=[]` 发送；Provider 即使违规返回 Tool Call 也不会执行。无扩展名路径可建议同 stem 文件，`search_text(max_lines=...)` 会提示改用 `max_results` 后再用 `read_file_lines`。

### 当前功能

不同参数但返回相同搜索命中、完全重叠文件区间、空结果和失败检查会累计 no-progress。Interactive CLI 显示 `Inspection is adding little new evidence` 或 `Inspection budget reached`；verbose 模式附带次数和原因。Learning Console 为两个新 Event 提供中文教学解释和 finalizing 状态投影。只读解释任务会优先基于已收集证据回答，而不是耗尽模型步骤后失败。

### 已知限制

证据判断是确定性的结构化启发式，不尝试理解自然语言语义；`search_text` 相同行号内容发生变化时依赖副作用屏障或新的 Run。自动无工具 finalization 仅用于当前 Run 尚未出现副作用 Tool 的检查流程；修改任务不会被强制截断，仍由完成证据和 `max_steps` 保护。Provider 在禁用 Tool 后仍只返回 Tool Call 且没有文本时，Run 会以协议错误失败而不是执行该调用。

### 测试与验收

新增不同 query 相同命中、重叠行范围、副作用屏障、禁用 Tool 绕过、路径 stem 建议、参数纠错、配置边界、CLI 展示和 Learning Console 教学投影测试。完整测试结果为 `313 passed`；总体 coverage 为 `84.54%`，core line coverage 为 `91.53%`，core branch coverage 为 `80.69%`；Ruff、Mypy strict、文档门禁、coverage 门禁、sdist/Wheel 构建和干净虚拟环境发行验证通过。

```powershell
python -m pytest tests/test_runtime.py tests/test_coding_tools.py tests/test_contract_edges.py tests/test_interactive.py tests/test_lab_api.py -q
python -m ruff check --no-cache src tests scripts
python -m mypy src/agent_runtime
python scripts/check_docs.py
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -q
python scripts/check_coverage.py coverage.json
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.8.14-py3-none-any.whl
```

### 后续计划

继续以真实 CLI 失败记录校准默认阈值和证据类型；下一步优先处理有副作用修改任务中的有限修复回合与明确阻塞报告，不新增业务 Tool、Provider 或分布式能力。

---
<a id="e2026-08-19-005"></a>
## E2026-08-19-005：v0.8.13 Agent Loop Convergence

- **完成时间**：2026-08-19
- **状态**：✅ stable
- **类型**：improvement
- **影响范围**：
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/tools.py`
  - `src/agent_runtime/coding_tools.py`
  - `src/agent_runtime/workspace_context.py`
  - `src/agent_runtime/interactive/renderer.py`
  - `src/agent_runtime/lab/explanations.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_runtime.py`
  - `tests/test_tools.py`
  - `tests/test_coding_tools.py`
  - `tests/test_interactive.py`
  - `tests/test_workspace_context.py`
  - `tests/test_api.py`
  - `tests/test_incident.py`
  - `tests/test_lab_api.py`
  - `tests/test_observability.py`
  - `scripts/verify_distribution.py`
  - `README.md`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/CODING_TOOLS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CURRENT.md`
  - `docs/LOCAL_RUNTIME.md`
  - `docs/ROADMAP.md`
  - `docs/adr/README.md`
  - `docs/adr/0036-read-only-tool-convergence.md`
  - `pyproject.toml`
- **关联 commit**：`9d269c3`
- **关联 ADR**：[ADR-0036](./adr/0036-read-only-tool-convergence.md)

### 变更摘要

收敛真实模型在简单代码解释任务中的重复 Tool Loop：完全相同的本地只读 Tool 调用复用当前 Run 的 durable result；错误参数返回允许字段，错误路径返回 Workspace 相对候选；compact 终端减少 inspection Tool requested/completed 双行噪声。

### 系统架构

Runtime Kernel 在 Tool Handler 前增加保守的当前 Run 只读结果复用判断。复用不引入跨 Run cache 或 SQLite migration，仍创建 ToolExecution、Checkpoint、Tool Call 计数和 durable `tool.reused` Event。任何副作用 Tool 都是失效屏障，保证 Workspace 变化后重新读取。Learning Console 将 `tool.reused` 作为独立可解释事件。

### 实现方式

仅对白名单 `calculator`、`git_status`、`git_diff`、`list_files`、`read_artifact`、`read_file_lines`、`read_text_file` 和 `search_text` 查找同名、arguments 完全相同、已 completed 的候选。复用后把原结果和 convergence note 返回模型而不调用 handler。Tool schema 校验补充 allowed arguments；不存在的 search/read 路径执行有界候选查找。内建 Coding Protocol 要求一次目标搜索、最小范围读取、复用已有证据并及时停止。compact Renderer 隐藏常见 inspection requested 行，verbose 保持完整生命周期。

### 当前功能

相同只读 Tool Call 在无副作用写入间隔时只执行一次 handler，模型第二次请求显示 `tool.reused` 并继续保留审计事实。错误 `max_lines` 等未知参数会列出合法字段；错误 `runtime.py` 路径会建议 `src/agent_runtime/runtime.py` 等候选。终端默认只显示有信息增量的 completed、failed 或 reused 行。

### 已知限制

复用只按 Tool 名和完全相同 arguments 判断，不识别语义等价请求；白名单外 Tool 不复用。任何副作用 Tool 后都重新执行读取。候选路径最多五个且只做有界文件名匹配。`tool.reused` 是独立 point event，不形成新的 requested-started-completed span。

### 测试与验收

新增只读 Tool Handler 单次执行、`tool.reused` 持久化、convergence note、allowed arguments、错误路径候选、compact inspection 降噪、reused 投影和 Coding Protocol 回归测试。完整测试结果为 `304 passed`；总体 coverage 为 `84.41%`，core line coverage 为 `91.50%`，core branch coverage 为 `80.61%`；Ruff、Mypy strict、文档门禁、coverage 门禁、sdist/Wheel 构建和干净虚拟环境发行验证通过。

```powershell
python -m pytest tests/test_runtime.py tests/test_tools.py tests/test_coding_tools.py tests/test_interactive.py tests/test_workspace_context.py -q
python -m ruff check --no-cache src tests scripts
python -m mypy src/agent_runtime
python scripts/check_docs.py
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -q
python scripts/check_coverage.py coverage.json
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.8.13-py3-none-any.whl
```

### 后续计划

继续从真实 Interactive CLI 运行记录识别 no-progress 模式；只有出现无法通过相同调用复用和明确错误反馈解决的循环时，才增加有界重复模式预算，不扩展业务 Tool。

---
<a id="e2026-08-19-004"></a>
## E2026-08-19-004：v0.8.12 Validation Phase and Recovered Tool Errors

- **完成时间**：2026-08-19
- **状态**：✅ stable
- **类型**：fix
- **影响范围**：
  - `src/agent_runtime/completion.py`
  - `src/agent_runtime/interactive/renderer.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_completion.py`
  - `tests/test_interactive.py`
  - `tests/test_api.py`
  - `tests/test_incident.py`
  - `tests/test_lab_api.py`
  - `tests/test_observability.py`
  - `scripts/verify_distribution.py`
  - `README.md`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/CODING_TOOLS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CURRENT.md`
  - `docs/LOCAL_RUNTIME.md`
  - `docs/ROADMAP.md`
  - `docs/adr/README.md`
  - `docs/adr/0035-interactive-cli-execution-transparency.md`
  - `pyproject.toml`
- **关联 commit**：`9d269c3`
- **关联 ADR**：[ADR-0035](./adr/0035-interactive-cli-execution-transparency.md)

### 变更摘要

修复已知验证脚本被错误显示为通用执行动作，以及同名 Tool 先失败后成功仍导致 `Task incomplete` 的终端误报。

### 系统架构

Runtime Kernel、ToolExecution、Completion Evidence、Event schema 和 SQLite schema 保持不变。Completion Policy 与 Interactive Renderer 复用同一个确定性 validation classifier；可恢复错误只在 CLI Adapter 中根据当前轮 durable Tool Event 顺序派生。

### 实现方式

新增公共内部 `looks_like_validation_command()`，统一识别 Python module、常见语言检查命令和四个项目内建验证脚本。Renderer 记录本轮同名 Tool 的失败与后续成功：最后结果成功时标记 recovered，之后再次失败则重新视为 unresolved。durable `failed_tools` 仍保留真实历史失败，不做迁移或回写。

### 当前功能

`python scripts/check_docs.py`、`check_coverage.py`、`verify_distribution.py` 和 `verify_local_runtime.py` 会进入 `Verifying changes`。只读任务中已被后续成功调用恢复的 Tool 错误显示为轻量 `Recovered tool error`，不会继续触发 `Task incomplete`；未恢复或最后一次仍失败的 Tool 继续显示 Needs attention。

### 已知限制

validation classifier 仍是保守 allowlist，自定义项目验证脚本需要显式加入规则。恢复判断按 Tool 名和当前轮 Event 顺序投影，不区分同名 Tool 的业务参数语义。Runtime 没有结构化 clarification Event，因此 CLI 不从模型自由文本猜测等待澄清状态。

### 测试与验收

新增直接 Python 验证脚本分类、Renderer 验证阶段、同名 Tool 失败后成功以及失败—成功—再次失败回归测试。完整测试结果为 `301 passed`；总体 coverage 为 `84.50%`，core line coverage 为 `91.49%`，core branch coverage 为 `80.64%`；Ruff、Mypy strict、文档门禁、构建和干净虚拟环境 Wheel 发行验证通过。

```powershell
python -m pytest tests/test_completion.py tests/test_interactive.py -q
python -m ruff check src tests scripts
python -m mypy src/agent_runtime
python scripts/check_docs.py
python -m pytest -q
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.8.12-py3-none-any.whl
```

### 后续计划

继续进入 Agent Loop Convergence，优先处理重复 Tool 调用、no-progress 检测和有限修复次数，不增加新的业务 Tool 或分布式能力。

---

<a id="e2026-08-19-003"></a>
## E2026-08-19-003：v0.8.11 Interactive CLI Approval Continuity

- **完成时间**：2026-08-19
- **状态**：✅ stable
- **类型**：fix
- **影响范围**：
  - `src/agent_runtime/interactive/shell.py`
  - `src/agent_runtime/interactive/renderer.py`
  - `src/agent_runtime/workspace_context.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_interactive.py`
  - `tests/test_workspace_context.py`
  - `tests/test_api.py`
  - `tests/test_incident.py`
  - `tests/test_lab_api.py`
  - `tests/test_observability.py`
  - `scripts/verify_distribution.py`
  - `README.md`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/CODING_TOOLS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CURRENT.md`
  - `docs/LOCAL_RUNTIME.md`
  - `docs/ROADMAP.md`
  - `docs/adr/README.md`
  - `docs/adr/0035-interactive-cli-execution-transparency.md`
  - `pyproject.toml`
- **关联 commit**：`9d269c3`
- **关联 ADR**：[ADR-0035](./adr/0035-interactive-cli-execution-transparency.md)

### 变更摘要

修复 Interactive CLI 在批准 Tool 后过早返回 `You >` 的竞态，并消除模型口头确认与 Runtime Approval 的重复确认。失败且未产生写入的修改尝试现在显示为 `Task incomplete`；精确替换审批使用聚焦 `- old` / `+ new` 预览。

### 系统架构

Runtime 状态机、Approval、ToolExecution、Event schema、SQLite schema 和 Completion Evidence 合同保持不变。修复位于 Interactive CLI Adapter：Shell 在 `approval.resolved` 后启动 `runtime.resume()`，等待 Run 离开瞬时 `waiting_for_approval`，再从最后 durable sequence 继续消费同一 Run。Renderer 只改变终端投影，Workspace Protocol 只改变模型使用现有 Tool Approval 的方式。

### 实现方式

新增有界的 resume activation 等待，避免 `pending_approval` 已清空但 Run 尚未切换为 running 时结束事件循环。内建 Coding Protocol 将 Runtime Approval 定义为副作用 Tool 的唯一确认步骤，要求模型直接发起 Tool Call。Renderer 对 `read_only + failed/rejected` 派生 `incomplete`，隐藏无意义的 diff/validation not required 行；old/new 预览通过公共前后缀定位实际变化，并保留有限上下文。

### 当前功能

批准或拒绝 Tool 后，CLI 会继续显示 `approval.resolved`、Tool 结果、后续验证、最终 Assistant 回答、Task Summary 和 Run footer，之后才重新显示输入提示。用户只需在 Runtime 审批卡片确认一次。失败且没有应用修改时会明确看到 `No changes applied`，长句中的细小标点或局部替换也更容易在审批前识别。

### 已知限制

Approval 仍只支持 allow once 或 deny，不提供会话级规则缓存。Coding Protocol 可以显著减少模型口头确认，但外部自定义 System Prompt 仍可能要求额外确认。聚焦 diff 是有界文本预览，不是完整 unified diff；完整变更仍应在执行后通过 `git_diff` 检查。

### 测试与验收

新增 Approval 后 `Approved → tool.completed → final answer → Run footer` 顺序回归、单次 Runtime Approval Prompt 合同、失败只读任务 incomplete 投影以及长公共前缀的聚焦差异测试。完整测试结果为 `295 passed`；总体 coverage 为 `84.40%`，core line coverage 为 `91.50%`，core branch coverage 为 `80.67%`；Ruff、Mypy strict、文档门禁、构建和干净虚拟环境 Wheel 发行验证通过。

```powershell
python -m pytest tests/test_interactive.py tests/test_workspace_context.py -q
python -m ruff check src tests scripts
python -m mypy src/agent_runtime
python scripts/check_docs.py
python -m pytest -q
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.8.11-py3-none-any.whl
```

### 后续计划

继续进入 Agent Loop Convergence，优先处理重复 Tool 调用、no-progress 检测和有限修复次数，不扩展新的 Provider 或分布式能力。

---

<a id="e2026-08-19-002"></a>
## E2026-08-19-002：v0.8.10 Interactive CLI Execution Transparency

- **完成时间**：2026-08-19
- **状态**：✅ stable
- **类型**：improvement
- **影响范围**：
  - `src/agent_runtime/interactive/renderer.py`
  - `src/agent_runtime/interactive/shell.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_interactive.py`
  - `tests/test_api.py`
  - `tests/test_incident.py`
  - `tests/test_lab_api.py`
  - `tests/test_observability.py`
  - `scripts/verify_distribution.py`
  - `README.md`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/CODING_TOOLS.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CURRENT.md`
  - `docs/ROADMAP.md`
  - `docs/adr/0035-interactive-cli-execution-transparency.md`
  - `pyproject.toml`
- **关联 commit**：`9d269c3`
- **关联 ADR**：[ADR-0035](./adr/0035-interactive-cli-execution-transparency.md)

### 变更摘要

将 Interactive CLI 从“显示 Tool 名和结果”升级为可解释的本地 Coding Task 投影。CLI 现在会以 append-only 方式展示 Inspecting、Editing、Verifying 和通用 Executing 阶段；Approval 在用户输入 y/n 前显示命令、工作目录、超时、文件、编辑数量、有限 old/new 预览及 capability/sandbox 信息；任务结束时以 durable Completion Evidence 输出变更文件、Git diff、validation 命令、失败和未满足条件。

### 系统架构

Runtime Kernel、Provider、Tool Protocol、Event schema、SQLite schema 和恢复语义均保持不变。`EventRenderer` 只消费既有 `tool.requested`、`approval.requested`、`approval.resolved`、`completion.verification_requested` 和 `completion.evidence` Event，并将它们派生为终端阶段、审批卡片和结构化摘要。展示状态不写入 Session、Run 或 Event Log，durable Event sequence 仍是事实来源。

### 实现方式

新增 `ExecutionPhase` 与确定性 Tool 分类：文件发现/搜索/读取进入 Inspecting，写入与 Patch 进入 Editing，`git_diff` 和已识别测试/静态检查命令进入 Verifying，其他 Tool 进入 Executing。阶段只在转换时输出一次。Approval 使用 Tool-aware formatter；compact 不展开完整文件正文、完整 Patch 或环境变量值，verbose 可追加有界 JSON。Renderer 通过 tool execution id 关联 `approval.resolved` 并显示 Approved/Denied。Completion Evidence 使用有界 Rich Panel 分离模型结论与 Runtime 事实，Run footer 增加执行耗时。

### 当前功能

用户可以在 `agent-runtime chat` 中直观看到 Agent 当前处于检查、修改还是验证阶段；批准 `run_process` 前能看到 argv、cwd、timeout 和安全约束，批准文件修改前能看到目标文件、编辑数量和有限预览。完成修改任务后，终端列出 changed files、Git diff 检查状态、实际 validation command/exit code，以及 unmet、failed 或 rejected Tool。`--compact`、`--verbose`、`/display` 和 `--print` 合同保持兼容。

### 已知限制

阶段是基于 Tool 名和已知 validation 命令的确定性投影，不等同于模型完整计划；自定义 Tool 默认显示 `Executing action`。compact Approval 有意省略完整正文、Patch 和环境变量值。只读任务如果没有 Completion Evidence，不额外输出变更摘要。当前审批仍只支持 allow once 或 deny，没有会话级“批准同类命令”能力。

### 测试与验收

新增执行阶段只在转换时出现、Python validation 命令识别、非 validation 进程避免误分类、process Approval 隐藏环境变量值、Patch Approval 有界预览、approval.resolved 投影和 verified/unverified Task Summary 测试。完整测试结果为 `293 passed`；总体 coverage 为 `84.21%`，core line coverage 为 `91.50%`，core branch coverage 为 `80.67%`；Ruff、Mypy strict、文档门禁、构建和干净虚拟环境 Wheel 发行验证通过。版本、API health、诊断包与 Learning Console 同步升级为 `0.8.10`。

```powershell
python -m pytest tests/test_interactive.py -q
python -m ruff check src tests scripts
python -m mypy src/agent_runtime
python scripts/check_docs.py
python -m pytest -q
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.8.10-py3-none-any.whl
```

### 后续计划

下一阶段优先建设 Agent Loop Convergence：重复 Tool 调用检测、no-progress 识别、验证失败后的有限修复次数和明确阻塞报告；不新增 Provider、多 Agent 编排或分布式能力。

---

<a id="e2026-08-19-001"></a>
## E2026-08-19-001：修复 Interactive CLI Streaming Markdown 重复输出

- **完成时间**：2026-08-19
- **状态**：✅ stable
- **类型**：fix
- **影响范围**：
  - `src/agent_runtime/interactive/renderer.py`
  - `tests/test_interactive.py`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CURRENT.md`
  - `docs/ROADMAP.md`
  - `docs/adr/0034-interactive-cli-presentation.md`
- **关联 commit**：`9d269c3`
- **关联 ADR**：[ADR-0034](./adr/0034-interactive-cli-presentation.md)

### 变更摘要

修复 Windows PowerShell 等终端中 Assistant 回答重复出现的问题。重复内容不是 Model Provider 多次生成，也不是 durable `model.delta` 重复写入，而是 Rich Live 将累计 Markdown Buffer 多次完整重绘后，终端未能可靠覆盖旧帧，导致每一帧都被追加保留。

### 系统架构

Runtime Kernel、Provider、Event schema、SQLite Event Log 和 `model.delta` durable 语义保持不变。修复仅位于 Interactive CLI 投影层：Renderer 继续缓冲跨 delta Markdown，但不再重写已经输出的终端行，而是在段落空行或完整代码围栏形成稳定 Markdown 块后按顺序追加一次；Tool、Approval、完成证据或 Run 终态会刷新剩余内容。

### 实现方式

移除 `EventRenderer` 对 Rich `Live`、刷新节流和 ANSI 光标回退的依赖。新增稳定 Markdown 前缀识别：普通内容只在空行边界刷新，fenced code block 必须等待匹配的结束围栏，未完成尾部继续留在内存。每个已完成块只调用一次 Rich Markdown 渲染，避免终端宽度、中文全角字符或宿主 ANSI 兼容性差异造成累计帧重复。

### 当前功能

Interactive CLI 仍可逐段看到模型输出，标题、列表和代码围栏继续使用 Rich Markdown 展示；完整段落可能在空行、代码围栏闭合或 Assistant 内容段结束时出现。compact/verbose Tool 展示、`--print` 最终结果合同和 durable Event 查询方式不变。

### 已知限制

为了优先保证本地终端输出确定性，当前不再提供逐 token 原位置重绘。没有空行的超长单段回答会在 Assistant 内容段结束时一次性显示；Markdown 表格和跨块结构也可能延迟到稳定边界后显示。

### 测试与验收

新增强制 TTY 下“每个 Markdown 块只出现一次”的回归断言、禁止 cursor-up/erase-line 控制序列断言，以及跨 delta fenced code block 只渲染一次的测试。针对性 Interactive CLI 测试与完整测试集均通过，当前为 `288 passed`；Ruff、Mypy strict 和文档演进检查通过。

```powershell
python -m pytest tests/test_interactive.py -q
python -m ruff check src tests scripts
python -m mypy src/agent_runtime
python scripts/check_docs.py
```

### 后续计划

继续以 append-only 输出作为默认稳定合同；未来若重新提供原位置逐 token 动态刷新，应作为显式实验模式，并使用真实终端屏幕状态模拟器验证最终屏幕，而不能只检查原始 ANSI 字节流。

---

<a id="e2026-08-18-003"></a>
## E2026-08-18-003：v0.8.9 Interactive CLI Presentation

- **完成时间**：2026-08-18
- **状态**：✅ stable
- **类型**：improvement
- **影响范围**：
  - `src/agent_runtime/interactive/renderer.py`
  - `src/agent_runtime/interactive/shell.py`
  - `src/agent_runtime/interactive/commands.py`
  - `src/agent_runtime/interactive/__init__.py`
  - `src/agent_runtime/cli.py`
  - `tests/test_interactive.py`
  - `scripts/verify_distribution.py`
  - `README.md`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CURRENT.md`
  - `docs/ROADMAP.md`
  - `docs/adr/0034-interactive-cli-presentation.md`
  - `pyproject.toml`
- **关联 commit**：`9d269c3`
- **关联 ADR**：[ADR-0034](./adr/0034-interactive-cli-presentation.md)

### 变更摘要

将 Interactive CLI 从逐 `model.delta` 原样追加升级为缓冲式 Streaming Markdown。交互式 TTY 使用 Rich Live 原位置刷新完整 Markdown，代码围栏、标题、列表和强调不再以源标记占据终端；Tool 调用默认采用 compact 单行摘要，并提供 `--verbose` 与 `/display verbose` 诊断视图。`--print` 现在只输出最终 Run result，不混入中间 delta 或 Tool 状态。

### 系统架构

Runtime Kernel、Provider、Event schema、ToolExecution 和 SQLite 均保持不变。变化严格位于 `agent_runtime.interactive` Adapter：Renderer 合并连续 delta，按 Tool/Approval/完成证据边界结束 Assistant 内容段；TTY 用 Live 刷新，非 TTY 在段边界渲染。Display mode 只属于当前 Shell，不进入持久化 Session 或 Run 事实。

### 实现方式

新增 `DisplayMode.COMPACT/VERBOSE`。Compact 通过 Tool-aware summarizer 提取路径、搜索词、行范围、argv、Patch 文件数和 Artifact 路径，参数与结果限制约 180 字符；started 事件默认隐藏。Verbose 使用有界 Rich Panel/Syntax 展示 JSON 参数、多行结果和失败详情，单块最多 4000 字符。Markdown Live 刷新按 50ms、换行或围栏边界节流，结束时强制刷新完整内容。

### 当前功能

`agent-runtime chat` 默认 compact；可用 `--compact`、`--verbose` 或 Shell 内 `/display compact|verbose` 切换。`/status` 与 Banner 显示当前模式。交互式回答支持动态 Markdown，重定向或 `--no-color` 在内容段边界输出 Markdown；`chat -p` 只输出最终可消费文本。完整 durable Event 仍可通过 `/events`、API 或 Learning Console 检查。

### 已知限制

Rich Live 需要 TTY 和 ANSI 光标控制；非 TTY 与 `--no-color` 无法同时保持原位置动态刷新，因此会缓冲到 Assistant 内容段结束。长 Markdown 每次刷新需要重新解析当前段，虽然已经节流但仍有终端渲染成本。Display mode 不跨 Shell 重启持久化；compact 摘要不是完整诊断事实。

### 测试与验收

新增 CLI 参数互斥、`/display`、Markdown 围栏跨 delta、真实 Rich Live 分支、compact 大参数隐藏、Artifact 摘要、verbose JSON/多行结果、失败详情边界以及 print-only 最终输出测试。最终全量结果为 `287 passed`，总体 coverage 为 `83.80%`，core line coverage 为 `91.50%`，core branch coverage 为 `80.67%`；Ruff、Mypy strict 和 core coverage gate 通过。

```powershell
python scripts/check_docs.py
python -m ruff check src tests scripts
python -m mypy src/agent_runtime
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -p no:cacheprovider
python scripts/check_coverage.py coverage.json
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.8.9-py3-none-any.whl
```

### 后续计划

用真实长回答和多 Tool Coding 任务观察终端重绘成本与摘要信息密度；下一阶段优先评估 Approval 的 command/diff 预览和最终变更摘要，不改变 Runtime Kernel 或增加新编排能力。

---

<a id="e2026-08-18-002"></a>
## E2026-08-18-002：v0.8.8 Verified Task Completion

- **完成时间**：2026-08-18
- **状态**：✅ stable
- **类型**：improvement
- **影响范围**：
  - `.gitignore`
  - `agent-runtime.example.toml`
  - `src/agent_runtime/completion.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/local_runtime.py`
  - `src/agent_runtime/interactive/renderer.py`
  - `src/agent_runtime/__init__.py`
  - `tests/test_completion.py`
  - `tests/test_interactive.py`
  - `scripts/verify_distribution.py`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/CODING_TOOLS.md`
  - `docs/adr/0033-verified-task-completion.md`
  - `pyproject.toml`
- **关联 commit**：`8026e8c`
- **关联 ADR**：[ADR-0033](./adr/0033-verified-task-completion.md)

### 变更摘要

标准本地 Coding Runtime 不再仅凭模型返回普通文本就判断修改任务已经完整验证。新增可选 Completion Policy，从持久化 ToolExecution 派生变更文件、diff 检查和验证命令证据；证据不足时只追加一次有界验证提醒，最终以 durable Event 标记 `read_only`、`verified` 或 `unverified`。同时将真实 `agent-runtime.toml` 排除出 Git，并提供无秘密的 example 配置。

### 系统架构

Runtime Kernel 保持原有状态机和公共 Provider/Tool 协议，新增构造期可选 `CompletionPolicy` 扩展点。标准本地 Runtime 注入 `CodingCompletionPolicy`，普通 Runtime 默认关闭。Policy 只读取当前 Run 已持久化的 ToolExecution，不执行额外文件或进程操作。第一次缺证据的最终文本被保存为已完成 Model Step，Runtime 追加 system reminder 并继续同一 Run；最终完成前写入 `completion.evidence`。

### 实现方式

成功的 `write_text_file`、`replace_text` 和 `apply_patch` 被识别为文件变更。最后一次写入之后的 `git_diff` 作为 diff 检查证据；Python pytest/ruff/mypy/unittest、以及常见语言 test/check/lint 命令通过 `run_process` argv 和真实 exit code 形成验证证据。每个 Run 通过 durable `completion.verification_requested` Event 保证最多提醒一次。CLI 显示验证继续提示和最终证据摘要。

### 当前功能

只读任务保持一步完成。修改任务缺少 diff 或代码验证时，Runtime 会继续一次，让模型补充最窄验证或明确说明无法验证。验证成功显示 `verified`；再次结束仍缺证据时允许完成但显示 `unverified`。最终证据可由 SSE、CLI、Learning Console 和 Eval 统一消费。真实本地配置不再出现在普通 Git 未跟踪列表中。

### 已知限制

验证命令识别为保守 allowlist，不理解任意项目脚本；`git_diff` 不包含未跟踪文件内容；Completion Policy 不会判断自然语言任务是否“业务上正确”，只约束修改后的执行证据。为保持有界执行，第二次普通最终回答不会再次被阻止。

### 测试与验收

新增只读不提醒、修改后自动请求 diff/validation、成功验证证据、仍无法验证但不循环、文档修改无需代码验证、验证命令 allowlist，以及 CLI 证据渲染测试。最终全量结果为 `279 passed`，总体 coverage 为 `84.22%`，core line coverage 为 `91.50%`，core branch coverage 为 `80.67%`；Ruff、Mypy strict 和文档检查通过。

```powershell
python scripts/check_docs.py
python -m ruff check src tests scripts
python -m mypy src/agent_runtime
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -p no:cacheprovider
python scripts/check_coverage.py coverage.json
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.8.8-py3-none-any.whl
```

### 后续计划

基于真实 Coding Eval 统计 false completion、额外 Model Step 和验证命令成功率；下一阶段再优化 Streaming Markdown、Tool 状态折叠和 Approval diff/command 预览，不引入分布式或新编排能力。

---

<a id="e2026-08-18-001"></a>
## E2026-08-18-001：v0.8.7 Artifact-aware Reading & Workspace Discovery

- **完成时间**：2026-08-18
- **状态**：✅ stable
- **类型**：improvement
- **影响范围**：
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/tools.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/coding_tools.py`
  - `src/agent_runtime/workspace_context.py`
  - `src/agent_runtime/local_runtime.py`
  - `src/agent_runtime/__init__.py`
  - `tests/test_artifact_tools.py`
  - `tests/test_coding_tools.py`
  - `tests/test_workspace_context.py`
  - `scripts/verify_distribution.py`
  - `docs/CODING_TOOLS.md`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/LOCAL_RUNTIME.md`
  - `docs/adr/0032-artifact-paging-workspace-discovery.md`
  - `pyproject.toml`
- **关联 commit**：`8026e8c`
- **关联 ADR**：[ADR-0032](./adr/0032-artifact-paging-workspace-discovery.md)

### 变更摘要

针对真实 `agent-runtime chat` 体验中暴露的两条退化路径进行优化：大 Tool Result 不再需要通过 `read_text_file` 或 `run_process` 打印 Artifact；根目录文件发现被测试产物占满或截断时，Agent 会缩小范围继续搜索，而不是过早反问用户或结束 Run。

### 系统架构

ArtifactStore 增加仅限当前 Run 的 Tool Result Artifact 分页读取能力，标准本地 Runtime 注册只读 `read_artifact` Tool。Runtime 仍负责首次大结果 Artifact 化，但 `read_artifact` 页面具有明确的非递归语义。Workspace Context 的内建 Coding Protocol 与 Tool description 共同约束“可推断目标继续执行、发现截断后缩小范围、禁止用通用进程打印文件或 Artifact”。

### 实现方式

`read_artifact` 接受 `_artifact.path` 或 `_artifact.relative_path`，以字符 offset 和最多 4000 字符的页面返回 `next_offset`、`total_chars`、`has_more` 与 SHA-256；路径必须落在当前 `run_id/tool-results` 下。`read_text_file` 命中同 Run Tool Result Artifact 时返回明确引导。`read_file_lines` 默认 3000 字符、硬上限 3500 字符，并返回 `next_start_line/has_more`。`list_files/search_text` 默认过滤 `.runtime-test-data`、`.coverage` 和 `coverage.json`，描述中要求截断后继续缩小搜索。

### 当前功能

真实模型看到大 Tool Result Preview 后，可以无审批地调用 `read_artifact` 分页续读；读取页面不会再次触发 `tool.result.artifactized`。已知目标文件或符号时优先搜索；根目录结果截断时继续限定 `path` 或 `pattern`。标准本地 Agent、发行包 smoke 和 CLI Tool 列表均包含 `read_artifact`。

### 已知限制

Artifact 分页 offset 以 Unicode 字符计数，每次读取仍需顺序扫描 Artifact 以计算总字符数和 SHA-256；它不是随机访问索引。Tool 只允许读取当前 Run 的 `tool-results`，不能浏览其他 Run、Eval 或任意 Artifact。Prompt 只能提高模型继续执行的概率，Runtime 不会在模型返回普通最终文本后强制追加 Tool Call。

### 测试与验收

新增 Artifact UTF-8 分页、最后一页、路径逃逸、跨 Run 拒绝、参数边界、`read_text_file` 引导和“large result → page 1 → page 2 → final answer”非递归流程测试；扩展 Workspace 噪声过滤、行读取续页字段和 Coding Protocol 断言。最终全量结果为 `257 passed`，总体 coverage 为 `83.93%`，core line coverage 为 `91.28%`，core branch coverage 为 `80.19%`；Ruff、Mypy strict、文档检查和发行验证通过。

```powershell
python scripts/check_docs.py
python -m ruff check src tests scripts
python -m mypy src\agent_runtime
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -p no:cacheprovider
python scripts/check_coverage.py coverage.json
python -m build
python scripts/verify_distribution.py dist\agent_runtime-0.8.7-py3-none-any.whl
```

### 后续计划

继续用真实编码任务观察模型的分页次数、发现策略和上下文消耗；在该闭环稳定前，不增加 LSP、PTY、自动 commit、自动批准或无限自主循环。

---

<a id="e2026-08-17-004"></a>
## E2026-08-17-004：v0.8.6 Project-aware Workspace Context

- **完成时间**：2026-08-17
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `src/agent_runtime/workspace_context.py`
  - `src/agent_runtime/local_config.py`
  - `src/agent_runtime/local_runtime.py`
  - `src/agent_runtime/interactive/shell.py`
  - `tests/test_workspace_context.py`
  - `tests/test_interactive.py`
  - `scripts/verify_distribution.py`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/LOCAL_RUNTIME.md`
  - `docs/adr/0031-project-workspace-instructions.md`
  - `pyproject.toml`
- **关联 commit**：`8026e8c`
- **关联 ADR**：[ADR-0031](./adr/0031-project-workspace-instructions.md)

### 变更摘要

The local Runtime now loads bounded project instructions from root-relative `AGENTS.md` and `CLAUDE.md` files and composes them with the configured prompt and a built-in coding execution protocol.

### 系统架构

A dedicated `workspace_context` layer runs between local configuration bootstrap and AgentDefinition registration. The final prompt remains part of the immutable AgentDefinition snapshot. CLI and status projections expose only source metadata, never instruction content.

### 实现方式

The backward-compatible `[workspace_context]` section defaults to enabled, two instruction files, and a 50000-character shared budget. Paths must remain relative to the Workspace and cannot contain `..`. Only UTF-8 files are loaded. The built-in protocol requires inspection before edits, Git diff review, focused validation, and evidence-backed completion claims. Banner, `/workspace`, and `status` show loaded sources; `/workspace` also now lists all v0.8.4/v0.8.5 tools.

### 当前功能

Supports ordered project rules, disable/configure controls, bounded truncation, SHA-256 summaries, invalid-path rejection, invalid UTF-8 reporting, CLI visibility, prompt snapshots, and clean-wheel verification. Existing TOML files use defaults without being overwritten.

### 已知限制

Only explicitly configured root-relative files are loaded. Hierarchical directory overrides are not implemented. Changes require Runtime restart, and conflicting files are supplied in configured order without semantic merging.

### 测试与验收

Added tests for ordering, budgets, truncation, hashing, invalid paths, invalid UTF-8, prompt composition, Runtime registration, redacted status, configuration disablement, and Interactive CLI display. Full result: `253 passed`; Ruff, Mypy strict, documentation, coverage, and clean-wheel gates pass.

```powershell
python scripts/check_docs.py
python -m ruff check src tests scripts
python -m mypy src\agent_runtime
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -p no:cacheprovider
python scripts/check_coverage.py coverage.json
python -m build
python scripts/verify_distribution.py dist\agent_runtime-0.8.6-py3-none-any.whl
```

### 后续计划

Validate the prompt behavior with real coding tasks and improve the existing loop before adding hierarchical rules, LSP, PTY, automatic commits, or unbounded autonomy.

---

<a id="e2026-08-17-003"></a>
## E2026-08-17-003：v0.8.5 有界文件读取与批量精确 Patch

- **完成时间**：2026-08-17
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `src/agent_runtime/coding_tools.py`
  - `src/agent_runtime/local_runtime.py`
  - `src/agent_runtime/interactive/shell.py`
  - `src/agent_runtime/version.py`
  - `tests/test_coding_tools.py`
  - `scripts/verify_distribution.py`
  - `docs/CODING_TOOLS.md`
  - `docs/adr/0030-bounded-read-batch-patch.md`
  - `pyproject.toml`
- **关联 commit**：`8026e8c`
- **关联 ADR**：[ADR-0030](./adr/0030-bounded-read-batch-patch.md)

### 变更摘要

增加 `read_file_lines` 和 `apply_patch`，让模型可以按行范围读取大型文件，并在一次 Approval 中完成多个已有 UTF-8 文件的结构化精确替换。

### 系统架构

两个 Tool 继续注册到同一个 ToolRegistry，不修改 Runtime Kernel。读取走 `file.read` capability；批量修改走 `file.write`、Durable Approval、ToolExecution、Checkpoint、Event Log 和 UNKNOWN 处理链路。

### 实现方式

`read_file_lines` 限制起始行、最大行数和最大字符数，返回稳定行号、截断状态和文件 SHA-256。`apply_patch` 最多接受 20 条 edit，先验证全部路径、字段和匹配数，再逐文件执行原子 replace；失败后尽力回滚，回滚不完整时报告 UNKNOWN。`/diff` 可以展开批量 Patch 中每个文件的前后 SHA-256。

### 当前功能

支持大型文件局部读取、多个文件一次精确修改、一次人工审批、修改结果审计以及后续 `run_process` 验证。

### 已知限制

`apply_patch` 不是 unified diff parser，不支持创建、删除、移动或二进制文件。多文件操作不承诺进程崩溃级事务原子性，也不自动执行 Git commit 或 push。

### 测试与验收

新增行范围、字符限制、SHA-256、多文件修改、全量预验证、Approval 持久化结果和 `/diff` 展开测试。本地 Python 3.13 全量结果为 `248 passed`，总体 coverage 为 `83.95%`，core line coverage 为 `91.42%`，core branch coverage 为 `80.27%`；Ruff、Mypy strict 和文档检查通过。

```powershell
python scripts/check_docs.py
python -m ruff check src tests scripts
python -m mypy src\agent_runtime
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -p no:cacheprovider
python scripts/check_coverage.py coverage.json
python -m build
python scripts/verify_distribution.py dist
```

### 后续计划

通过真实模型观察大文件读取范围和批量 Patch 参数质量，优先改进失败提示与上下文效率，不立即增加自动提交、自动批准或无限自治循环。

---

<a id="e2026-08-17-002"></a>
## E2026-08-17-002：v0.8.4 Git-aware Workspace Review

- **完成时间**：2026-08-17
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `src/agent_runtime/git_tools.py`
  - `src/agent_runtime/local_runtime.py`
  - `src/agent_runtime/__init__.py`
  - `tests/test_git_tools.py`
  - `tests/test_coding_tools.py`
  - `scripts/check_docs.py`
  - `scripts/verify_distribution.py`
  - `docs/CODING_TOOLS.md`
  - `docs/adr/0029-read-only-git-workspace-tools.md`
- **关联 commit**：`8026e8c`
- **关联 ADR**：[ADR-0029](./adr/0029-read-only-git-workspace-tools.md)

### 变更摘要

增加只读 `git_status` 和 `git_diff` Tool，使本地 Agent 可以在修改前后检查分支、工作区状态和 tracked diff，而不必通过通用进程 Tool 猜测 Git 参数。

### 系统架构

Git Tool 复用标准 LocalProcessSandbox，只在 Git 已进入允许可执行文件列表时注册。Tool 声明 `process.exec` 和 `file.read`，由 CapabilityPolicy 要求受管 Sandbox，但不声明副作用和 Approval。

### 实现方式

`git_status` 固定使用 short/branch 格式并可控制 untracked 展示；`git_diff` 禁用 external diff、textconv 和颜色，限制 context line、输出字符数和 Workspace 路径。非 Git 仓库、非法范围和非零退出码转换为明确 Tool 错误。

### 当前功能

模型可以调用 `git_status` 判断工作区是否已有改动，调用 `git_diff` 查看 staged 或 unstaged tracked diff，再决定是否继续编辑和验证。

### 已知限制

不展示 untracked 文件正文，不提供 commit、push、reset、checkout 或 branch 写操作。Git 不在 Sandbox allowlist 时两个 Tool 不注册。

### 测试与验收

新增注册条件、只读授权、命令参数、clean/changes 判断、路径限制、输出截断、非法参数和 Git 错误映射测试。本地 Python 3.13 全量结果为 `248 passed`，总体 coverage 为 `83.95%`，core line coverage 为 `91.42%`，core branch coverage 为 `80.27%`；Ruff、Mypy strict 和文档检查通过。

```powershell
python scripts/check_docs.py
python -m ruff check src tests scripts
python -m mypy src\agent_runtime
python -m pytest tests\test_git_tools.py -p no:cacheprovider
```

### 后续计划

保持 Git 写操作走需要人工批准的 `run_process`；不增加自动 commit、push 或破坏性工作区操作。

---

<a id="e2026-08-17-001"></a>
## E2026-08-17-001：v0.8.3 Coding Workspace Tool Loop

- **完成时间**：2026-08-17
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `src/agent_runtime/coding_tools.py`
  - `src/agent_runtime/local_config.py`
  - `src/agent_runtime/local_runtime.py`
  - `src/agent_runtime/sandbox.py`
  - `src/agent_runtime/interactive/commands.py`
  - `src/agent_runtime/interactive/shell.py`
  - `src/agent_runtime/__init__.py`
  - `tests/test_coding_tools.py`
  - `scripts/check_docs.py`
  - `scripts/verify_distribution.py`
  - `docs/CODING_TOOLS.md`
  - `docs/adr/0028-coding-workspace-tools.md`
  - `pyproject.toml`
- **关联 commit**：`8026e8c`
- **关联 ADR**：[ADR-0028](./adr/0028-coding-workspace-tools.md)

### 变更摘要

完成标准本地编码 Tool Loop：Agent 可以在可信 Workspace 内列出文件、搜索文本、读取文件、通过精确匹配修改已有文件，并在人工批准后运行白名单进程完成测试或语法验证。Interactive CLI 新增 `/workspace` 和 `/diff`，帮助用户观察当前编码边界和最近 Tool 文件变更。

### 系统架构

新增独立 `coding_tools.py`，通过 `register_coding_tools()` 向既有 ToolRegistry 注册 `list_files`、`search_text` 和 `replace_text`；标准 `create_configured_local_runtime()` 将它们与已有 `read_text_file`、`write_text_file`、`LocalProcessSandbox` 和 `run_process` 一起装配进本地 AgentDefinition。所有调用继续经过 Model Tool Call、CapabilityPolicy、Approval、ToolExecution、Event Log 和 Checkpoint，不在 Interactive CLI 内建立第二套执行循环。

### 实现方式

`list_files` 使用有界目录遍历、默认忽略目录、相对路径和稳定排序；`search_text` 使用纯 Python 扫描 UTF-8 文本，限制文件数、文件大小、结果数和行长，并跳过二进制文件；`replace_text` 要求实际匹配数等于 `expected_replacements`，使用同目录临时文件、flush/fsync 和 `os.replace` 原子替换，成功后返回修改前后 SHA-256。`[tools]` 新增可选的进程开关、白名单、timeout、输出和并发配置；旧配置缺少字段时使用默认值。

### 当前功能

标准本地 Agent 现在提供 `list_files`、`search_text`、`read_text_file`、`replace_text`、`write_text_file` 和 `run_process`。文件写入和进程执行仍要求人工批准；`run_process` 只接受 argv，不经过 Shell。Interactive CLI Banner 和 `/tools` 展示新 Tool，`/workspace` 展示 Workspace 与进程状态，`/diff` 展示当前 Session 最近 20 条持久化 Tool 文件变更摘要。

### 已知限制

`replace_text` 不是完整 unified diff parser，不支持多文件事务、文件移动或自动 Git commit。纯 Python 搜索没有索引，性能低于 ripgrep。`LocalProcessSandbox` 不是容器或虚拟机，只适合可信本地 Workspace；允许的 Python 解释器仍可能访问本机资源。当前不提供自动批准、交互式 PTY、Shell 管道、LSP、多 Workspace 或无限自主循环。

### 测试与验收

新增文件列表、忽略目录、Workspace 逃逸、文本搜索、二进制/大文件跳过、精确替换、匹配冲突、SHA-256、本地 Runtime 注册、进程白名单、Interactive CLI 命令和完整“发现 → 读取 → 修改 → 进程验证 → 最终回答”测试。本地 Python 3.13 全量结果为 `231 passed`，总体 coverage 为 `84.12%`，core line coverage 为 `91.62%`，core branch coverage 为 `80.37%`；Ruff、Mypy strict、文档检查、sdist/wheel 构建和干净虚拟环境发行验证全部通过。

```powershell
python scripts/check_docs.py
python -m ruff check src tests scripts
python -m mypy src\agent_runtime
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -p no:cacheprovider
python scripts/check_coverage.py coverage.json
python -m build
python scripts/verify_distribution.py dist
```

### 后续计划

先使用真实模型完成多个小型本地编码任务，重点修正 Tool 选择、失败恢复、Approval 可读性和上下文消耗；不立即扩展 Git 自动提交、完整 Patch、LSP、Docker 或分布式执行。

---

<a id="e2026-08-16-006"></a>
## E2026-08-16-006：v0.8.2 Interactive CLI 与持久化多轮 Session

- **完成时间**：2026-08-16
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `src/agent_runtime/interactive/__init__.py`
  - `src/agent_runtime/interactive/commands.py`
  - `src/agent_runtime/interactive/renderer.py`
  - `src/agent_runtime/interactive/shell.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_interactive.py`
  - `tests/test_api.py`
  - `tests/test_incident.py`
  - `tests/test_lab_api.py`
  - `tests/test_observability.py`
  - `scripts/check_docs.py`
  - `scripts/check_coverage.py`
  - `scripts/verify_distribution.py`
  - `docs/INTERACTIVE_CLI.md`
  - `docs/adr/0027-interactive-cli-session-history.md`
  - `pyproject.toml`
- **关联 commit**：`8026e8c`
- **关联 ADR**：[ADR-0027](./adr/0027-interactive-cli-session-history.md)

### 变更摘要

新增 `agent-runtime chat` 终端 Agent Shell，使用户可以直接通过 Prompt Toolkit 输入、多轮 Session、实时模型输出、Tool 状态、终端审批和 Slash Command 使用真实 Runtime。新增 `--continue`、`--resume`、`--print` 和 `--no-color`，并通过显式 Session 历史装配为终端对话提供跨进程上下文。

### 系统架构

Interactive CLI 作为 Runtime 外部 Adapter，复用 v0.8.1 的本地配置 Bootstrap、Provider、ToolRegistry、SQLite 和单 Owner Lock。每条用户消息创建独立 Run，同一对话通过 Session 关联；Rich Renderer 消费 durable Runtime Event，Prompt Toolkit 输入历史与 Runtime Session 明确分离。Runtime Kernel 只新增受 metadata 控制的历史消息装配，不依赖终端库。

### 实现方式

`InteractiveShell` 使用 `PromptSession.prompt_async()`、`FileHistory`、Slash Command Registry 和 `Runtime.stream()`。`model.delta` 直接追加到终端，Tool 与 Approval 以紧凑状态展示，内部 Context/Checkpoint 噪音默认隐藏；Approval 通过 `resolve_approval()` 和原 Run `resume()` 完成，活动 Run 的 `Ctrl+C` 调用协作式 `cancel()`。CLI 显式提交 `include_session_history=true` 和 20 Run 上限；Runtime 只加载同 Session、同 Agent、已完成 Run 的 user input/final assistant result，并写入 `session.history.loaded` Event。发行验证新增干净 Wheel 环境的 `chat --print` smoke。

### 当前功能

支持新建、继续和指定恢复 Interactive Session，支持 `/help`、`/new`、`/continue`、`/sessions`、`/resume`、`/status`、`/model`、`/tools`、`/events`、`/cancel`、`/clear` 和退出命令。支持终端输入历史、模型 token 流、Tool 调用状态、`y/n` Tool Approval、Ctrl+C 取消、Ctrl+D 退出和单次脚本输出。`chat` 与 `serve` 对同一状态目录继续保持单执行 Owner。

### 已知限制

当前 CLI 是 embedded Runtime，不会 attach 到已经运行的 HTTP 服务；因此同一状态目录下不能同时运行 `chat` 和 `serve`。Session 历史只重建 user input 和 final assistant result，不回放旧 Tool Call/Result、Approval 或 Checkpoint。默认历史窗口按 20 个 Run 控制，不是单独的 token-aware 对话摘要。同步副作用 Tool 取消后仍遵循既有 `UNKNOWN` 语义。

### 测试与验收

新增 Interactive CLI 命令解析、真实 Runtime 单次执行、Session 管理、终端 Tool Approval、显式 Session 历史和 Event Renderer 测试。本地 Python 3.13 全量结果为 `220 passed`，总体 coverage 为 `84.06%`，core line coverage 为 `91.85%`，core branch coverage 为 `80.37%`。sdist/wheel 构建通过，干净虚拟环境已完成 `chat --print`、本地服务、SDK、FastAPI/SSE、诊断和备份恢复 smoke。

```powershell
python scripts/check_docs.py
python -m ruff check src tests scripts
python -m mypy src\agent_runtime
python -m pytest -q -p no:cacheprovider
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -p no:cacheprovider
python scripts/check_coverage.py coverage.json
python -m build
python scripts/verify_distribution.py dist
agent-runtime chat -p "19 * 23" --no-color
```

### 后续计划

先用 v0.8.2 执行真实本地任务，收集文件搜索、补丁、测试执行、上下文摘要和交互稳定性缺口。Coding Tool Loop 与 HTTP attach 仅保留为需求驱动候选，不立即进入分布式、多租户或更多模型厂商扩展。

---

<a id="e2026-08-16-005"></a>
## E2026-08-16-005：v0.8.1 本地稳定 Runtime 启动闭环

- **完成时间**：2026-08-16
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/local_config.py`
  - `src/agent_runtime/local_runtime.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/telemetry.py`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/__init__.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/lab/static/index.html`
  - `scripts/verify_local_runtime.py`
  - `scripts/verify_distribution.py`
  - `scripts/check_docs.py`
  - `tests/test_local_runtime.py`
  - `tests/test_api.py`
  - `tests/test_incident.py`
  - `tests/test_lab_api.py`
  - `tests/test_observability.py`
  - `docs/LOCAL_RUNTIME.md`
  - `docs/adr/0026-local-runtime-bootstrap-single-owner.md`
  - `pyproject.toml`
- **关联 commit**：`6532120`
- **关联 ADR**：[ADR-0026](./adr/0026-local-runtime-bootstrap-single-owner.md)

### 变更摘要

停止把 DockerSandbox、SecretProvider、分布式调度等扩展能力作为当前阻塞项，将支持目标收敛到单机、单用户、本地 SQLite 和可信 Tool/脚本。新增配置驱动的 `init/serve/status` 主路径、状态目录单 Owner Lock、轮转日志和一键本地验收脚本，使现有 Runtime 能以明确且可重复的方式独立启动、运行、停止和重启。

### 系统架构

新增 `LocalRuntimeSettings` 配置层和 `create_configured_local_runtime()` Bootstrap 层。标准本地服务先获取 `LocalRuntimeLock`，再创建 Provider、ToolRegistry、Runtime 和 FastAPI Adapter；shutdown 完成后才释放 Lock。FastAPI 默认 ASGI `app` 改为惰性构造，避免仅 import HTTP Adapter 就创建隐藏 Runtime、SQLite 或状态目录。

### 实现方式

`agent-runtime init` 生成 `agent-runtime.toml`；`serve` 合并 CLI、`AGENT_RUNTIME_*` 环境变量、TOML 和默认值，并只允许 loopback Host。日志同时写入 stderr 和有界 `RotatingFileHandler`。`runtime.lock` 在整个服务生命周期内持有操作系统级非阻塞排他文件锁，并保存 PID、hostname、版本、启动时间和随机 token；强杀后由操作系统自动释放锁，下一次启动覆盖遗留元数据，Windows Process Handle 仅用于只读状态判断。`status` 以只读方式展示锁、SQLite quick_check/schema/WAL、Artifact 和日志状态。

### 当前功能

支持 Mock 和 OpenAI-compatible Provider 的配置化选择，支持 Workspace、State、容量、API、模型和日志参数。标准本地 Agent 注册 `calculator`、`read_text_file` 与 `write_text_file`。`verify_local_runtime.py` 覆盖配置、单实例、并发 Run、Event sequence、API health、备份校验、shutdown 幂等、同目录重启、历史保留以及线程和 asyncio Task 回归基线。

### 已知限制

Owner Lock 是单机进程所有权声明，不是分布式 Lease；Python SDK 仍可由调用方直接构造多个 Store。API 只支持 loopback，尚未提供认证和远程访问。API Key 仍由本机环境变量提供。本版本不实现 SecretProvider、DockerSandbox、Windows Service、分布式 Worker、多租户或 RBAC，也不承诺执行任意不可信代码。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
python scripts/check_docs.py
python -m ruff check src tests scripts
python -m mypy src\agent_runtime
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -p no:cacheprovider
python scripts/check_coverage.py coverage.json
python scripts/verify_local_runtime.py --runs 100 --concurrency 8
python scripts/run_reliability.py --stress-runs 20 --concurrency 20
python -m build
python scripts/verify_distribution.py dist
```

### 后续计划

先在真实本地任务中持续使用 v0.8.1，收集启动、模型调用、Tool、恢复和运维问题；只修复影响本地稳定运行的缺口。Docker、Secret、分布式和多租户能力进入候选或延期状态，不再预设为紧接着开发的版本。

<a id="e2026-08-16-004"></a>
## E2026-08-16-004：v0.8.0 本地进程 Sandbox 与 Tool Capability 策略

- **完成时间**：2026-08-16
- **状态**：🧪 experimental
- **类型**：security
- **影响范围**：
  - `src/agent_runtime/sandbox.py`
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/tools.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/__init__.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/scenarios.py`
  - `src/agent_runtime/lab/explanations.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/app.js`
  - `scripts/check_docs.py`
  - `scripts/verify_distribution.py`
  - `tests/test_sandbox.py`
  - `tests/test_runtime.py`
  - `tests/test_api.py`
  - `tests/test_lab_api.py`
  - `tests/test_lab_scenarios.py`
  - `docs/SANDBOX.md`
- **关联 commit**：`0ccff33`
- **关联 ADR**：[ADR-0025](./adr/0025-local-process-sandbox-capability-policy.md)

### 变更摘要

开始 v0.8 安全执行阶段，先建立可替换的 Sandbox 协议、受限本地进程实现和 Tool Capability 决策模型。Runtime 不再把“Tool 已注册”直接视为“Tool 可执行”，而是在模型 ToolCall 进入审批或 handler 前合并 allow、deny、require_approval 和 sandbox_only 策略。

### 系统架构

新增 `SandboxExecutor`、`LocalProcessSandbox`、`CapabilityPolicy` 和 `ToolAuthorization`。`ToolDefinition` 增加向后兼容的 `capabilities` 与 `sandbox_only` 字段，并进入 AgentDefinition 不可变快照。Capability 决策新增 durable `tool.policy.evaluated` Event；活动进程与 Sandbox 容量属于 transient operational state，通过 Runtime、CLI、HTTP 和 Learning Console 快照读取，不占用 Event sequence。

### 实现方式

`LocalProcessSandbox` 只使用 argv 和 `asyncio.create_subprocess_exec()`，不使用 `shell=True`；执行前校验可执行文件白名单、Workspace cwd、环境变量白名单、timeout、总输出和并发。Run Cancel、输出超限、超时和 Runtime shutdown 会终止进程树。内置 `run_process` 同时声明 `process.exec`、`file.read` 和 `file.write`，默认要求 Sandbox 与人工审批；网络和 Secret capability 默认拒绝。

### 当前功能

支持 Python `LocalProcessSandbox`、`register_process_tool()`、`runtime.sandbox_snapshot()`、CLI `agent-runtime observe sandbox`、HTTP `GET /observability/sandbox`，以及 Learning Console“受限进程沙箱”审批场景。安全测试覆盖 argv 执行、可执行文件/环境/cwd 拒绝、timeout、输出上限、取消、进程树终止、Capability deny、Sandbox 强制、Approval 和 durable Event。

### 已知限制

`LocalProcessSandbox` 是受限本地进程适配器，不是容器或虚拟机；当前不提供网络强隔离、CPU/内存/PID 限制、系统调用过滤和不可信代码安全承诺。v0.8.0 尚未实现 DockerSandbox、SecretProvider、Secret 临时注入或 Secret 输出脱敏。包含 ToolCall 的事件序列新增一条 additive Event。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
python scripts/check_docs.py
python -m ruff check src tests scripts
python -m mypy src\agent_runtime
python -m pytest
agent-runtime observe sandbox
agent-runtime lab
Invoke-RestMethod http://127.0.0.1:8000/observability/sandbox
```

### 后续计划

继续 v0.8.x：优先实现 SecretProvider 与值级脱敏合同，再评估可选 DockerSandbox 的非 root、只读根文件系统、资源限制和默认禁网；在攻击面测试完成前不宣称强隔离。
<a id="e2026-08-16-003"></a>
## E2026-08-16-003：v0.7.12 脱敏故障诊断包与根因摘要

- **完成时间**：2026-08-16
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/incident.py`
  - `src/agent_runtime/__init__.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/app.js`
  - `scripts/verify_distribution.py`
  - `scripts/check_docs.py`
  - `tests/test_incident.py`
  - `tests/test_api.py`
  - `tests/test_lab_api.py`
  - `docs/INCIDENTS.md`
- **关联 commit**：`89c8fcd`
- **关联 ADR**：[ADR-0024](./adr/0024-incident-diagnostic-bundle.md)

### 变更摘要

在 v0.7.11 综合诊断基础上增加可直接交给维护者的脱敏 ZIP 诊断包，以及基于 durable Event 的确定性失败根因摘要。诊断包默认采用允许列表并排除原始执行内容，避免为了排障直接复制 SQLite、Artifact、Prompt、Tool 数据或 Memory。

### 系统架构

新增 `IncidentDiagnosticsService` 作为 Observability 的只读派生层。它组合 Runtime 生命周期、SQLite health、Doctor、Metrics、Run/Event 安全摘要与 FailureDiagnosis，但不写入 Event Log、不修改 Run 状态，也不承担恢复。`.agent-backup` 继续负责恢复，Incident Bundle 只负责排障和支持协作。

### 实现方式

Bundle format 1 包含 manifest、diagnostics、failure analysis、runs、events、collection 和 privacy 文档；Run/Event 数量使用显式上限并记录截断状态；每个数据条目记录大小和 SHA-256。Run input/result、原始错误、Model 内容、Tool 参数/结果、Memory、Checkpoint、Artifact、SQLite 和本机数据路径均被排除。CLI 使用临时文件、flush/fsync 和 `os.replace` 原子落盘并默认拒绝覆盖；HTTP 返回 `application/zip` 和 `Cache-Control: no-store`。

### 当前功能

支持 `agent-runtime observe incident-bundle`、HTTP `GET /observability/incident-bundle` 和 Learning Console 顶部下载入口。根因规则区分 Provider 401/403、429、5xx、Timeout/Transport、Tool Failure、Tool UNKNOWN 和 Runtime Failure；最终完成的 Run 会标记 recovered，避免把已经恢复的中间重试误认为当前事故。

### 已知限制

根因摘要是确定性规则而非完整因果推理；不采集 CPU/RSS/句柄/磁盘/网络趋势；不自动打包宿主日志、不上传远程工单或对象存储；报告读取运行中的多个持久化对象时不是跨表全局冻结快照；对外发送前仍需人工复核组织安全要求。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
python -m pytest
agent-runtime observe incident-bundle --output incident.zip
Invoke-WebRequest http://127.0.0.1:8000/observability/incident-bundle -OutFile incident-api.zip
python scripts/verify_distribution.py dist
```

### 后续计划

继续在 v0.7.x 观察诊断包字段是否足够支持真实排障，再评估有界 CPU/RSS 趋势采样和可选宿主日志附加；在数据边界、保留和用户确认机制明确前，不增加自动远程上传。
<a id="e2026-08-16-002"></a>
## E2026-08-16-002：v0.7.11 结构化日志与综合运行诊断

- **完成时间**：2026-08-16
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/tools.py`
  - `src/agent_runtime/observability.py`
  - `src/agent_runtime/telemetry.py`
  - `src/agent_runtime/version.py`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/app.js`
  - `tests/test_observability.py`
  - `tests/test_telemetry.py`
  - `tests/test_api.py`
  - `tests/test_lab_api.py`
  - `docs/OBSERVABILITY.md`
- **关联 commit**：`62abc09`
- **关联 ADR**：[ADR-0023](./adr/0023-operational-observability.md)

### 变更摘要

在不增加 Agent 能力和外部监控依赖的前提下，将 Runtime 生命周期、容量、进程资源、SQLite、Doctor、持久化 Metrics 与最近失败汇总为统一诊断快照，并增加显式启用、自动脱敏和有界输出的 JSON Lines 结构化日志。

### 系统架构

保持 SQLite durable facts 与进程级 transient signals 分层：Run、Event、Checkpoint、ToolExecution 和 Approval 继续承担恢复与审计；Runtime 启停、进程 PID、线程数和 asyncio Task 数通过日志和 `OperationalSnapshot` 暴露。Provider attempt 失败与重试决策会影响 Run 解释，因此新增 durable `model.attempt.failed` 和 `model.retry.scheduled` Event。

### 实现方式

`ObservabilityService.diagnostics()` 聚合 `Runtime.lifecycle_snapshot()`、Tool executor 容量、进程采样、SQLite health、Runtime Doctor、扩展 Metrics 与最近失败。Metrics 增加 Model/Tool p95、Provider attempt failure/retry、Tool failure、UNKNOWN 和失败分类。`StructuredLogFormatter` 输出单行 JSON，对常见凭据字段脱敏，并限制字符串长度和嵌套深度；CLI 通过 `--json-logs` 显式启用。

### 当前功能

支持 CLI `observe diagnostics`、HTTP `GET /observability/diagnostics`、Python `configure_structured_logging()`、Prometheus 新增失败和 p95 指标，以及 Learning Console Operational Diagnostics 面板。API 和 `/health` 使用统一 `__version__`，避免适配层版本漂移。

### 已知限制

当前进程采样只包含 PID、线程和 asyncio Task，不包含 CPU、RSS、句柄、磁盘与网络；Metrics 按最近 N 个 Run 派生而非严格时间窗口；日志滚动、保留、上传和 Collector 由宿主环境负责；Learning Console 仍不是生产监控平台。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
python -m pytest
agent-runtime observe diagnostics
agent-runtime --json-logs demo "19 * 23" 2> runtime.jsonl
Invoke-RestMethod http://127.0.0.1:8000/observability/diagnostics
python scripts/check_coverage.py coverage.json
```

### 后续计划

先通过 Nightly 和真实本地运行观察失败分类、p95 与资源计数是否稳定，再决定是否引入时间窗口指标、CPU/RSS 采样或 OpenTelemetry Exporter；在 v0.7.x 可靠性结论明确前继续后置 v0.8。
<a id="e2026-08-16-001"></a>
## E2026-08-16-001：v0.7.10 在线备份、校验与灾难恢复演练

- **完成时间**：2026-08-16
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/backup.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/app.js`
  - `scripts/run_backup_recovery.py`
  - `scripts/verify_distribution.py`
  - `tests/test_backup.py`
  - `tests/test_lab_api.py`
  - `.github/workflows/quality.yml`
  - `.github/workflows/nightly-reliability.yml`
  - `docs/OPERATIONS.md`
  - `docs/LEARNING.md`
- **关联 commit**：`03cfae4`
- **关联 ADR**：[ADR-0022](./adr/0022-runtime-backup-restore.md)

### 变更摘要

补齐独立于进程崩溃恢复的状态备份能力，为 SQLite、Artifact、migration checksum 和恢复前回滚状态建立可执行的灾难恢复合同，并将真实恢复演练加入持续质量门禁。

### 系统架构

新增 Runtime 外部运维组件 `RuntimeBackupManager`。创建路径使用 SQLite Online Backup API 获取一致性数据库快照，再复制 Artifact 并生成内容校验 Manifest；恢复路径要求 Runtime 离线，先校验、保存当前状态、安装恢复点，失败时回滚。该能力不进入 Agent 执行循环，也不改变 schema 8。

### 实现方式

`.agent-backup` ZIP 归档包含 `runtime.sqlite3`、`artifacts/` 和 `manifest.json`；Manifest 记录 format version、schema version、migration checksum、表记录数、字节数和 SHA-256。校验执行 ZIP 路径检查、重复 Entry 检查、hash、`quick_check`、`foreign_key_check` 和 migration 验证。恢复使用同目录 staging、WAL checkpoint、排他锁检查和 `pre-restore-*` 回滚副本。

### 当前功能

支持运行中创建在线备份、离线校验、原路径恢复、默认保留恢复前状态、可选丢弃回滚副本，以及 CLI `backup create/verify/restore`。PR、Nightly 和 Wheel smoke 均执行备份路径；独立演练验证备份前 Run 与 Artifact 恢复、备份后 Run 不越过恢复点。

### 已知限制

现有持久化记录包含绝对 Artifact 路径，因此 v0.7.10 不支持跨目录或跨机器恢复；归档未内建加密、远程上传、自动调度和保留清理；恢复前必须停止所有连接目标 SQLite 的进程。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
python -m pytest
python scripts/run_backup_recovery.py
agent-runtime backup create --output runtime.agent-backup
agent-runtime backup verify runtime.agent-backup
python scripts/check_coverage.py coverage.json
```

### 后续计划

先在 Nightly 和真实本地状态目录持续执行恢复演练；进入 v0.8 前，评估 Artifact 相对标识、跨目录恢复、备份加密和保留策略，但不扩大 Agent 执行能力。
<a id="e2026-08-15-010"></a>
## E2026-08-15-010：v0.7.9 AgentDefinition 快照与确定性恢复

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/doctor.py`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/console.py`
  - `scripts/run_crash_recovery.py`
  - `tests/test_agent_snapshots.py`
  - `tests/test_crash_recovery.py`
  - `tests/test_doctor.py`
  - `tests/test_contract_edges.py`
- **关联 commit**：`9167990`
- **关联 ADR**：[ADR-0021](./adr/0021-agent-definition-snapshots.md)

### 变更摘要

持久化不可变 AgentDefinition 快照，并将普通 Run 与 Workflow Step 绑定到确切 checksum，使进程重启后无需应用重新注册 AgentDefinition，也不会因同名 Agent 后续升级而改变历史任务的恢复语义。

### 系统架构

schema 8 新增内容寻址的 `agent_definitions` 表和 `runs.agent_definition_checksum`；Runtime 在注册、创建 Run 和冻结 Workflow 时保存定义；恢复路径优先读取确切快照；Doctor 检查活动历史 Run 是否缺少定义绑定。

### 实现方式

AgentDefinition 被规范化为包含 System Prompt、ToolDefinition、ModelConfig、`max_steps` 和 `max_tool_calls` 的 JSON，并使用 SHA-256 去重；Workflow snapshot 为每个 Step 保存 checksum；若定义引用的 Tool Handler 在新进程不可用，则抛出 `AgentDefinitionUnavailable`，HTTP 返回不可重试的稳定 409 错误。

### 当前功能

支持普通 Run 无重新注册恢复、历史定义与最新同名定义隔离、串行/并行 Workflow 按 Step 快照恢复、缺失 Tool Handler 明确失败、Learning Console 显示定义快照数量，以及 schema 1–7 向 8 升级。

### 已知限制

Python Tool Handler 和 Provider 连接不能持久化，重启进程仍需提供对应实现；schema 7 以前已存在的活动 Run 没有定义 checksum，Doctor 会提示但不会自动猜测或修复；快照暂不自动清理。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
python -m pytest
python scripts/run_crash_recovery.py
python scripts/run_reliability.py --stress-runs 100 --concurrency 20
python scripts/check_coverage.py coverage.json
```

### 后续计划

继续观察 v0.7.8/v0.7.9 Nightly 稳定性；在进入 v0.8 Sandbox 前，优先补充备份恢复演练和运行手册，不扩展新的 Agent 能力。
<a id="e2026-08-15-009"></a>
## E2026-08-15-009：v0.7.8 Durable Submission 与过载保护

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/static/app.js`
  - `tests/test_api.py`
  - `tests/test_reliability.py`
  - `tests/test_contract_edges.py`
- **关联 commit**：`ab73a38`
- **关联 ADR**：[ADR-0020](./adr/0020-run-submission-idempotency-admission.md)

### 变更摘要

为顶层 Run 提交增加跨进程重启可保持的幂等键，并为活动 Run 和模型请求建立有界准入，避免客户端重试造成重复副作用，也避免突发请求无限消耗本机和 Provider 资源。

### 系统架构

schema 7 在 `runs` 表保存幂等键和规范化请求指纹；Runtime 新增 `RunSubmission`、活动任务容量快照和模型请求 Semaphore；FastAPI 将 `Idempotency-Key`、409 冲突、429 背压和 `Idempotent-Replayed` 映射为稳定 HTTP 合同。

### 实现方式

相同 Key 与相同 SHA-256 指纹在 SQLite `BEGIN IMMEDIATE` 事务内复用同一个 Run，多连接竞态由唯一索引收敛；同 Key 不同请求返回 `idempotency_conflict`；`max_inflight_runs` 达到上限时拒绝新顶层任务，`max_concurrent_model_requests` 同时约束普通和流式模型请求。

### 当前功能

支持 Python SDK 与 HTTP 幂等提交、并发提交去重、请求冲突检测、可重试容量错误、模型并发限制、Health/Learning Console 容量展示，以及 schema 1–6 向 7 升级。

### 已知限制

活动任务配额只覆盖当前 Runtime 进程，不是多进程全局队列；幂等重放只返回 durable Run，不会隐式 resume 崩溃后遗留任务；当前没有自动清理幂等键。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
python -m pytest
python -m pytest tests/test_api.py tests/test_reliability.py
python -m ruff check src tests scripts
python -m mypy src\agent_runtime
```

### 后续计划

v0.7.9 持久化 AgentDefinition 快照，消除恢复 Workflow 时必须由应用重新注册 AgentDefinition 的限制。
<a id="e2026-08-15-008"></a>
## E2026-08-15-008：v0.7.7 崩溃恢复与运维闭环

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：reliability
- **影响范围**：
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/doctor.py`
  - `src/agent_runtime/orchestration.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/api/app.py`
  - `scripts/run_crash_recovery.py`
  - `tests/crash_worker.py`
  - `tests/test_crash_recovery.py`
  - `.github/workflows/quality.yml`
  - `.github/workflows/nightly-reliability.yml`
- **关联 commit**：`0025941`
- **关联 ADR**：[ADR-0017](./adr/0017-unknown-outcome-confirmation.md)、[ADR-0018](./adr/0018-crash-recovery-contract.md)、[ADR-0019](./adr/0019-runtime-doctor.md)

### 变更摘要

完成 UNKNOWN 副作用人工确认、Workflow 快照恢复、只读 Runtime Doctor 和真实进程强杀恢复矩阵，使“检测异常—人工确认—显式恢复—验证无重复副作用”形成闭环。

### 系统架构

schema 6 为 ToolExecution 增加确认审计字段；Runtime 从规范化 Workflow snapshot 重建串行/并行编排；Doctor 只读取 SQLite 持久化事实；Crash Matrix 使用独立子进程和真实 `process.kill()` 验证恢复合同。

### 实现方式

UNKNOWN 只允许确认成功或失败，禁止直接 retry，确认后 Run 保持 PAUSED 并等待显式 `resume()`；`tool.outcome_confirmed` 保存 reason、actor 和时间；CLI/API 提供确认和 Doctor 入口；CI 在 Windows/Linux Python 3.13 执行 crash smoke，Nightly 重复完整矩阵。

### 当前功能

支持模型请求中断恢复、副作用 Tool 崩溃不重复执行、Approval 恢复、串行/并行 Workflow 部分完成恢复、Doctor 数据一致性诊断，以及 schema 1–5 向 6 升级。

### 已知限制

恢复依赖应用重新注册 snapshot 中引用的 AgentDefinition；Doctor 只诊断不自动修复；当前仍不提供跨主机调度和不可信代码隔离。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
python scripts/run_crash_recovery.py
agent-runtime doctor --json
python -m pytest -m crash
python -m pytest
```

### 后续计划

持续运行 v0.7.7 Nightly，稳定后再决定是否进入 v0.8 Sandbox。
<a id="e2026-08-15-007"></a>
## E2026-08-15-007：v0.7.6 FastAPI/SSE 长稳与发布验证

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：milestone
- **影响范围**：
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/static/app.js`
  - `scripts/run_reliability.py`
  - `scripts/check_performance.py`
  - `scripts/verify_distribution.py`
  - `benchmarks/reliability-baseline.json`
  - `.github/workflows/nightly-reliability.yml`
  - `tests/test_api.py`
  - `tests/test_reliability.py`
- **关联 commit**：`a16de5e`
- **关联 ADR**：[ADR-0016](./adr/0016-fastapi-runtime-ownership-sse.md)

### 变更摘要

完成 HTTP、SSE、压力、长稳和安装产物验证，使现有能力可以反复启停和断线恢复。

### 系统架构

FastAPI lifespan 区分 Runtime 所有权；SSE 以 SQLite durable event 为事实来源；Nightly 负责长稳与性能门禁。

### 实现方式

保留 `shutdown_runtime=False` 兼容默认值；SSE 增加 heartbeat 和 Last-Event-ID；可靠性脚本混合单 Run、Tool、Workflow 和事件消费。

### 当前功能

支持真实 health、受控关闭、SSE 清理、20/100 并发、可配置 soak，以及干净环境 CLI/SDK/Uvicorn/SSE smoke。

### 已知限制

30 分钟 soak 由 Nightly 和发布前流水线执行。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
python scripts/run_reliability.py --stress-runs 100 --concurrency 20
python -m build
python scripts/verify_distribution.py dist/agent_runtime-0.7.6-py3-none-any.whl
```

### 后续计划

v0.7.6 门禁稳定前不启动 v0.8。

<a id="e2026-08-15-006"></a>
## E2026-08-15-006：v0.7.5 Runtime 生命周期、SQLite 与恢复语义

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：architecture
- **影响范围**：
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/orchestration.py`
  - `tests/test_runtime.py`
  - `tests/test_orchestration.py`
  - `tests/test_reliability.py`
- **关联 commit**：`a16de5e`
- **关联 ADR**：[ADR-0015](./adr/0015-runtime-shutdown-sqlite-recovery.md)

### 变更摘要

定义 Runtime 安全启动、等待、暂停、恢复和关闭合同，并强化 SQLite durability、并发 sequence 和向前迁移。

### 系统架构

生命周期统一管理 Task、token、Provider、Tool executor 和 Store；schema 5 保存 Workflow 快照和 migration checksum。

### 实现方式

新增 `shutdown()`、async context manager 和带 timeout 的 `wait()`；SQLite 启用 WAL、FULL、busy retry、quick_check 和事务 sequence；pause 保持 PAUSED。

### 当前功能

shutdown 幂等；schema 1–4 升级到 5；多 Store sequence 唯一；启动协调 running Run 和副作用 Tool。

### 已知限制

不自动降级 schema，也不提供多进程调度。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
python -m pytest tests/test_runtime.py tests/test_orchestration.py tests/test_reliability.py
```

### 后续计划

所有 Adapter 统一使用生命周期合同。

<a id="e2026-08-15-005"></a>
## E2026-08-15-005：v0.7.4 Tool 与 Model Provider 执行安全

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：architecture
- **影响范围**：
  - `src/agent_runtime/tools.py`
  - `src/agent_runtime/providers.py`
  - `src/agent_runtime/runtime.py`
  - `tests/test_providers.py`
  - `tests/test_reliability.py`
  - `tests/test_contract_edges.py`
- **关联 commit**：`a16de5e`
- **关联 ADR**：[ADR-0013](./adr/0013-tool-isolation-unknown-outcome.md)、[ADR-0014](./adr/0014-provider-async-transport-retry.md)

### 变更摘要

隔离同步 Tool 和模型网络 I/O，修复 Event Loop 阻塞、资源无界、重试分类和副作用超时假成功。

### 系统架构

Runtime 使用有界 Tool 线程池；Provider 使用 `httpx.AsyncClient`；副作用不确定结果使用 `UNKNOWN`。

### 实现方式

默认 8 workers/32 pending，原子文件写入，严格校验响应/SSE，并按网络错误、408、429、5xx 和确定性 4xx 分类重试。

### 当前功能

长同步 Tool 不阻塞其他 Run；401/403 不重试；Retry-After 优先；取消关闭 HTTP stream；`aclose()` 幂等。

### 已知限制

同步 Python 线程不能安全强杀，迟到副作用结果必须人工确认。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
python -m pytest tests/test_providers.py tests/test_reliability.py tests/test_contract_edges.py
```

### 后续计划

保持 Tool Handler 和 Provider 公共协议兼容。

<a id="e2026-08-15-004"></a>
## E2026-08-15-004：v0.7.3 质量基线与行为合同

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：governance
- **影响范围**：
  - `pyproject.toml`
  - `scripts/check_coverage.py`
  - `.github/workflows/quality.yml`
  - `tests/test_contract_edges.py`
  - `tests/test_reliability.py`
  - `docs/CHANGELOG.md`
- **关联 commit**：`a16de5e`
- **关联 ADR**：[ADR-0012](./adr/0012-quality-gates.md)

### 变更摘要

将既有行为固化为兼容性合同，建立 lint、typing、coverage、跨平台测试和安装产物门禁。

### 系统架构

CI 分为 PR 快速严格门禁和 Nightly 长稳门禁；异常增加稳定分类且 API 保持 `detail` 兼容。

### 实现方式

启用 Ruff、Mypy strict、pytest-cov；core line ≥ 90%、branch ≥ 80%，combined ≥ 80%；支持 unit/integration/stress/soak marker。

### 当前功能

126 个测试覆盖单/多 Agent、Workflow、Memory、Approval、Streaming、API、Learning Console 和可靠性边界。

### 已知限制

CLI/SDK/API Adapter 不计入 core 聚合，但仍受 combined coverage 和 smoke test 约束。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
python -m ruff check src tests scripts
python -m mypy src\agent_runtime
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json
python scripts/check_coverage.py coverage.json
```

### 后续计划

可靠性阈值只允许保持或提高。
<a id="e2026-08-15-003"></a>
## E2026-08-15-003：Learning Console 多 Agent 独立泳道与流程语义连线

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/app.js`
  - `src/agent_runtime/lab/static/styles.css`
  - `tests/test_api.py`
  - `tests/test_lab_api.py`
  - `README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/LEARNING.md`
  - `docs/CONTEXT_MEMORY.md`
  - `docs/ROADMAP.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/0009-learning-console.md`
  - `docs/adr/README.md`
- **关联 commit**：`5e779a7`
- **关联 ADR**：[ADR-0009](./adr/0009-learning-console.md)

### 变更摘要

发布 v0.7.2 Learning Console 泳道可视化优化。修复串行和并行 Workflow 的全部 Child Event 被压入同一条 Agent 泳道、不同并行分支被全局相邻事件连线误表示为串行依赖的问题。

### 系统架构

- Timeline 从固定 8 条泳道改为 Snapshot 驱动的动态泳道模型。
- 多 Agent 场景生成 1 条 Workflow Parent 泳道，并为每个 Child Run 生成独立 Agent 泳道。
- Child 泳道按 `workflow_step` 或 `workflow_branch` 排序，顺序直接来自持久化 Run metadata。
- 非 Child 事件仍按 Memory、Context、Model、Tool、Approval 和 State 领域映射，但只显示当前 Snapshot 实际使用的领域泳道。
- 连线由跨 Run 全局相邻关系改为 Run 内部、Parent 委派和 Child 汇聚三类显式语义。

### 实现方式

- `buildSwimlanes()` 从 `snapshot.runs` 读取 Root/Child、Agent Name、Workflow Step/Branch、状态和 metadata，动态构造标签、顺序、颜色和画布高度。
- Child Event 使用 `agent:<run_id>` 作为稳定泳道 ID，因此同一 Child 的 Run、Context、Model、Tool 与 Checkpoint 事件保持在同一行。
- `buildTimelineLinks()` 先按 `run_id` 分组，再按 `local_sequence` 建立 Run 内部连线。
- `delegation.created → Child run.created` 建立委派分叉线；Child 终态事件到对应 `delegation.completed/failed/cancelled` 建立汇聚线。
- 页面增加连线图例；实线表示 Run 内部，长虚线表示委派，点线表示汇聚。
- 泳道标签和画布高度由 JavaScript 动态计算，移除 HTML/CSS 中固定 8 行和固定 660px 高度。

### 当前功能

- 串行 Workflow 显示 Workflow Parent、Planner、Worker、Reviewer 独立泳道。
- 并行 Workflow 显示 Workflow Parent、Research、Test、Risk 独立泳道。
- 每条 Agent 泳道显示 Step/Branch 编号、Agent Name 和当前状态，并使用独立颜色。
- 单 Run 场景继续按实际出现的 Context、Model、Tool、Approval 和 State 领域展示。
- 顶部时间轴使用跨 Run `timeline_sequence`，Child 节点同时展示 `local_sequence`。
- 回放、节点点击、自动跟随、Inspector 和 Root SSE + Snapshot 轮询行为保持不变。

### 已知限制

- 横轴仍按 `timeline_sequence` 等距排列，不是按真实耗时比例缩放。
- 确定性并行教学场景用于展示逻辑并发与分支关系，不用于衡量真实并行性能。
- Root SSE 仍不直接转发全部 Child Event，页面运行期间继续使用 450ms Snapshot 轮询。
- Learning Console 仍是本地单用户教学工具，不是生产 Workflow Designer 或分布式 Trace UI。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
cd D:\AICoding\Agent
$env:PYTHONPATH='src'
python scripts/check_docs.py
python -m pytest -p no:cacheprovider -o addopts= -q
python -m compileall -q src tests
node --check src\agent_runtime\lab\static\app.js
git diff --check
agent-runtime lab
```

验收覆盖：

- 静态页面使用动态 `swimlaneLabels` 容器，不再硬编码 Agent/Context 等 8 行标签。
- JavaScript 包含 `buildSwimlanes()` 和 `buildTimelineLinks()`。
- Child Event 映射为 `agent:<run_id>`。
- 串行 Agent 按 Planner、Worker、Reviewer 排序。
- 并行 Agent 按 Research、Test、Risk 排序。
- 每个 Child 均存在独立 `run.created`，可建立 Parent 委派和 Child 汇聚连线。
- 自检确认两个 Workflow 均生成 3 条 Agent 泳道、3 条委派线和 3 条汇聚线。
- 当前全量测试：`55 passed`。

### 后续计划

继续进入 v0.8 Sandbox；届时 Learning Console 为 Sandbox Policy、Capability、Secret Redaction 和 Tool Isolation 增加对应教学泳道或 Inspector，而不改变本次多 Agent 映射规则。

<a id="e2026-08-15-002"></a>
## E2026-08-15-002：Learning Console 覆盖 v0.6 多 Agent 与 v0.7 Context/Memory

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/scenarios.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/explanations.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/app.js`
  - `src/agent_runtime/lab/static/styles.css`
  - `tests/test_lab_api.py`
  - `tests/test_lab_scenarios.py`
  - `README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/LEARNING.md`
  - `docs/ROADMAP.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/0009-learning-console.md`
- **关联 commit**：`c9c5f4c`
- **关联 ADR**：[ADR-0009](./adr/0009-learning-console.md)

### 变更摘要

发布 v0.7.1 Learning Console 教学增强版。场景从 4 个单 Run 扩展到 9 个，新增 v0.6 串行/并行多 Agent，以及 v0.7 Session/Memory、Context Compaction 和大 Tool Result Artifact 场景。所有流程继续通过真实 Runtime、SQLite、Workflow、MemoryStore、ContextBuilder 和 ArtifactStore 执行。

### 系统架构

- Learning Console Snapshot 从单 Run 投影升级为 Root + Child Run 聚合投影。
- 使用 `RunRelation` 和 `TraceTree` 展示 Parent/Child 拓扑，不向 Runtime Kernel 注入 UI 状态。
- 增加 Session/Memory、Context、Agent 专用泳道和 Inspector。
- Root SSE 继续提供主事件通知；页面在运行期间补充轻量 Snapshot 轮询，以动态读取 Child Run 独立事件。
- Timeline 为跨 Run 事件生成展示用 `timeline_sequence`，同时保留每个 Run 的持久化 `local_sequence`。

### 实现方式

- `SequentialWorkflow` 真实执行 Planner → Worker → Reviewer 三个 Child Run。
- `ParallelWorkflow` 真实并发执行 Research、Test 和 Risk 三个 Child Run，并按 `ALL` 策略聚合。
- Session 场景创建 Session Memory 与 Agent Memory，模型请求前执行作用域检索和 Context 注入。
- Context 场景使用小 token budget 和四轮大工具结果稳定触发 `context.compacted`。
- Artifact 场景使用真实 ArtifactStore 保存完整大文本，Checkpoint 只保留引用和 Preview。
- Snapshot 聚合 Runs、Relations、Events、Checkpoints、Steps、ToolExecutions、Approvals、Memories、Context Builds 和 Artifacts。

### 当前功能

- Learning Console 共 9 个确定性教学场景。
- v0.6：串行多 Agent、并行多 Agent、Parent/Child Trace Tree、RunRelation 与聚合策略展示。
- v0.7：Session/Memory 检索、Context 构建/压缩、大 Tool Result Artifact 化展示。
- 8 条泳道：Run、Agent、Session/Memory、Context、Model、Tool、Approval、State。
- Inspector 新增 Context、Memory、Artifact，并强化 Trace Parent/Child Topology 与 v0.6/v0.7 Metrics。
- 自动验收新增 Child、Memory、Compaction 和 Artifact 数量检查。

### 已知限制

- 跨 Run Timeline 的 `timeline_sequence` 是 Snapshot 展示序号，不替代各 Run Event Log 的本地 sequence。
- Root SSE 不直接转发 Child Run 的每个事件，Learning Console 在本地运行期间使用 450ms Snapshot 轮询补充动态展示。
- Learning Console 仍面向本地单用户教学，不提供认证、远程多租户或 Workflow Designer。
- 场景使用确定性 Mock Provider，目的是学习 Runtime 语义，不评估真实模型质量。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
cd D:\AICoding\Agent
$env:PYTHONPATH='src'
python scripts/check_docs.py
python -m pytest -p no:cacheprovider -o addopts= -q
python -m compileall -q src tests
node --check src\agent_runtime\lab\static\app.js
git diff --check
agent-runtime lab
```

验收覆盖：

- 9 个场景目录和静态页面入口。
- 串行/并行 Workflow 均生成 1 个 Parent、3 个 Child 和 3 条 RunRelation。
- Session 场景检索 Session/Agent 两类 Memory，并在 Context 中记录命中 ID。
- Context 场景稳定产生 `context.compacted`，完整 Checkpoint 消息多于模型实际选择消息。
- Artifact 场景产生真实文件和 `tool.result.artifactized` provenance。
- 聚合 Event 同时包含 `timeline_sequence`、`local_sequence`、`run_id`、`agent_name` 和 `run_role`。

### 后续计划

继续进入 v0.8 Sandbox；Learning Console 后续可增加安全策略、Capability 和 Secret Redaction 教学场景。

<a id="e2026-08-15-001"></a>
## E2026-08-15-001：完成 Context、Session 与长期记忆 v0.7

- **完成时间**：2026-08-15
- **状态**：✅ stable
- **类型**：milestone
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/context.py`
  - `src/agent_runtime/memory.py`
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/observability.py`
  - `src/agent_runtime/evals.py`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/sdk.py`
  - `src/agent_runtime/__init__.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_context_memory.py`
  - `tests/test_runtime.py`
  - `tests/test_api.py`
  - `scripts/check_docs.py`
  - `README.md`
  - `docs/README.md`
  - `docs/CONTEXT_MEMORY.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CURRENT.md`
  - `docs/LEARNING.md`
  - `docs/ROADMAP.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/0011-context-session-memory.md`
  - `docs/adr/README.md`
- **关联 commit**：`1086c5e`
- **关联 ADR**：[ADR-0011](./adr/0011-context-session-memory.md)

### 变更摘要

完成 v0.7 Context、Session 与 Scoped Long-term Memory。Runtime 在每次模型调用前构建可追溯的受预算 Context；多个 Run 可以显式归入 Session，并在 Session 或 Agent Scope 内检索、注入、删除和过期 Memory。大 Tool Result 自动进入 Artifact Store，避免无限扩张 Checkpoint 和模型输入。

### 系统架构

- 新增 `ContextBuilder`，在完整 Checkpoint 与 Provider 请求之间建立受预算的 Context Build 层。
- 新增 `Session`、`session_runs`、`MemoryRecord`、`MemoryScope` 和 `MemoryStore`。
- SQLite schema 从 version 3 升级到 version 4，新增 Session、Memory 和 FTS5 索引表。
- Runtime 在模型调用前检索允许 Scope 的 Memory，再构造 Context 并写入可审计事件。
- 大 Tool Result 的完整内容写入 Artifact Store，ToolExecution 和 Checkpoint 保存引用与预览。
- Observability 和 Eval 增加 Memory Search、Context Compaction 与 Memory 数量验证。

### 实现方式

- 使用 Provider-neutral 的确定性近似 token 估算，使 Context 行为不绑定某个模型 SDK。
- System Prompt、最近消息和未完成 Tool Call 优先保留；Assistant Tool Call 与 Tool Result 作为不可拆分消息组。
- 被省略的旧消息生成确定性 Summary，并通过 `context.compacted` 记录。
- Memory 只允许 `session` 和 `agent` Scope，不提供 global Scope。
- SQLite FTS5 提供关键词检索，并强制 Scope、TTL、软删除和 source provenance 过滤。
- `source_run_id` 和 `source_trace_id` 使 Memory 可以反查来源执行。
- `MemoryEvalRunner` 复用现有 Eval Report 和 Artifact 机制。

### 当前功能

- 支持 Context token budget、消息分组、裁剪和确定性 Summary。
- 支持大 Tool Result Artifact 化和预览引用。
- 支持创建 Session、关联多个 Run，并让 Child Run 继承 Session。
- 支持 Session Memory 与 Agent Memory。
- 支持 SQLite FTS5 搜索、TTL、软删除和过期清理。
- 支持 `memory.search.started/completed`、`context.built/compacted` 和 `tool.result.artifactized` 事件。
- 支持 Context/Memory Metrics、Prometheus 指标和 Memory Eval。
- 支持 Session/Memory HTTP API 和 `agent-runtime memory demo`。

### 已知限制

- token 估算不是具体模型厂商的精确 tokenizer。
- Summary 是确定性文本摘要，不是模型生成的语义摘要。
- 当前只提供 SQLite FTS5，不提供 Embedding 或向量检索。
- Memory 必须显式创建，不会自动永久保存所有对话。
- 当前不提供 global Memory Scope。
- Learning Console 暂无 Session/Memory 专用管理画布。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
cd D:\AICoding\Agent
$env:PYTHONPATH='src'
python scripts/check_docs.py
python -m pytest -p no:cacheprovider -o addopts= -q
python -m compileall -q src tests
node --check src\agent_runtime\lab\static\app.js
agent-runtime memory demo "Which Python language do I prefer?" --remember "The user prefers Python for examples."
```

验收覆盖：

- `50 passed`。
- Context Change ID、日期顺序、路径和 ADR 关联通过文档门禁。
- Session Scope 与 Agent Scope 隔离、TTL 和软删除行为正确。
- Context 不拆分 Tool Call 组，并可对旧消息生成可追溯 Summary。
- Memory 检索、source trace、Metrics 和 Eval 结果可查询。
- 大 Tool Result 可写入 Artifact 并以引用进入 Checkpoint。
- Memory CLI Demo 能检索并使用显式写入的偏好。

### 后续计划

进入 v0.8：实现 Sandbox、Tool Capability 与 Secret 安全边界。

---
<a id="e2026-08-14-007"></a>
## E2026-08-14-007：完成持久化多 Agent 编排基础 v0.6

- **完成时间**：2026-08-14
- **状态**：✅ stable
- **类型**：milestone
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/__init__.py`
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/orchestration.py`
  - `src/agent_runtime/observability.py`
  - `src/agent_runtime/evals.py`
  - `src/agent_runtime/sdk.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/static/index.html`
  - `tests/test_orchestration.py`
  - `tests/test_runtime.py`
  - `tests/test_api.py`
  - `tests/test_lab_api.py`
  - `scripts/check_docs.py`
  - `README.md`
  - `docs/README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/LEARNING.md`
  - `docs/MULTI_AGENT.md`
  - `docs/ROADMAP.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/0010-parent-child-run-delegation.md`
  - `docs/adr/README.md`
- **关联 commit**：`e7ab51a`
- **关联 ADR**：[ADR-0010](./adr/0010-parent-child-run-delegation.md)

### 变更摘要

完成 v0.6 单机多 Agent 编排基础。Parent 可以通过持久化 RunRelation 和稳定 delegation key 委派独立 Child Run；系统提供顺序与并行 Workflow、并发与超时控制、三种汇聚策略、取消传播、幂等恢复、Trace Tree、Multi-Agent Metrics 和 Workflow Eval。

### 系统架构

- 新增 `agent_runtime.orchestration`，包含 AgentRegistry、SequentialWorkflow、ParallelWorkflow、WorkflowStep、WorkflowExecution 和 AggregationStrategy。
- 每个 Workflow Parent 和 Agent Child 都是独立 AgentRun，继续使用既有状态机、Runtime Event、Checkpoint、ToolExecution 和 Approval 语义。
- SQLite schema 从 version 2 升级到 version 3，新增 `run_relations`。
- Observability 从单 RunTrace 扩展为可从任意节点查询的 Parent/Child TraceTree。
- Learning Console Snapshot 增加 `trace_tree`，但 UI 内核不感知多 Agent 展示逻辑。

### 实现方式

- `AgentRegistry` 校验 Agent 名称和工具定义，同名不同定义会被拒绝。
- `Runtime.delegate()` 使用 `parent_run_id + delegation_key` 查重；首次委派原子写入 Child Run、RunRelation 和双方事件，恢复时复用原 Child。
- Child 使用独立 `trace_id`，并保存 Parent、Root、root_trace_id 和 delegation key metadata。
- `SequentialWorkflow` 用稳定 step key 顺序传递结果。
- `ParallelWorkflow` 使用 Semaphore 控制最大并发，通过 timeout 收敛超时，并支持 `all`、`best_effort` 和 `first_success`。
- Parent Cancel 递归取消活动 Child，已完成 Child 不改写终态。
- Workflow Parent 保存独立初始与终态 Checkpoint。
- `WorkflowEvalRunner` 复用 EvalCase / EvalSuite，可额外断言 Child Run 数量并写入 JSON Artifact。

### 当前功能

- 支持 Parent / Child / Root 关系查询。
- 支持手工 `Runtime.delegate()`。
- 支持 Planner → Worker → Reviewer 顺序 Workflow。
- 支持并行分支、最大并发数和 Workflow timeout。
- 支持 `all`、`best_effort`、`first_success` 汇聚。
- 支持 Parent 取消传播。
- 支持稳定 delegation key 幂等恢复。
- 支持 Trace Tree、Root/Child/Workflow/Delegation Metrics 和 Prometheus 导出。
- 支持 `GET /agents`、关系查询、Trace Tree 和委派 API。
- 支持 `agent-runtime workflow demo` 与 `agent-runtime observe trace-tree`。
- 项目版本更新为 `0.6.0`。

### 已知限制

- Child 仍在同一 Python 进程和 SQLite Store 中执行，没有跨机器 Worker。
- Workflow 定义和 AgentRegistry 仍由应用代码提供，数据库不持久化可执行定义。
- Workflow 不支持通用 pause/resume；恢复必须重新使用原 Workflow 定义和 parent_run_id。
- ParallelWorkflow 不提供通用 DAG 依赖表达式或图形化 Designer。
- Learning Console 尚未提供专用的跨 Run Trace Tree 画布。
- 尚无长期记忆、Docker Sandbox、多租户和 OpenTelemetry Collector。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
cd D:\AICoding\Agent
$env:PYTHONPATH='src'
python -m pytest -p no:cacheprovider -q
python scripts/check_docs.py
python -m compileall -q src tests
node --check src\agent_runtime\lab\static\app.js
agent-runtime workflow demo "设计一个可靠的恢复机制"
```

当前测试：`44 passed`。

新增覆盖：AgentRegistry 冲突校验、RunRelation migration、委派幂等、独立 Trace ID、顺序结果传递、恢复复用、并发上限、严格/尽力汇聚、first_success、Parent Cancel 传播、Trace Tree、Multi-Agent Metrics、Workflow Eval、API 和 Learning Console Snapshot。

### 后续计划

进入 v0.7，增加 Context、Session、短期上下文窗口与长期记忆接口；分布式 Worker、Queue 和 Lease 继续保留到 v0.9。

<a id="e2026-08-14-006"></a>
## E2026-08-14-006：修复 Learning Console 空状态占用大片空间

- **完成时间**：2026-08-14
- **状态**：✅ stable
- **类型**：fix
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/styles.css`
  - `tests/test_api.py`
  - `tests/test_lab_api.py`
  - `README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/LEARNING.md`
  - `docs/ROADMAP.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/0009-learning-console.md`
  - `docs/adr/README.md`
- **关联 commit**：`b8fad6d`
- **关联 ADR**：[ADR-0009](./adr/0009-learning-console.md)

### 变更摘要

修复 Learning Console 在已有 Runtime Event 时仍显示“运行一个场景”空状态的问题。该区域的用途只是在尚未启动 Run 时提示 SQLite / SSE 数据来源，不应在泳道图已出现时继续占用页面高度。

### 系统架构

- Runtime Kernel、SQLite、Snapshot API、SSE 和泳道事件数据保持不变。
- 变更仅位于 Learning Console Static UI 展示层。
- 空状态和泳道图继续由 Snapshot 中是否存在 Event 决定，两者必须互斥。

### 实现方式

- 根因是 `.empty-state { display: grid; }` 的作者样式覆盖了浏览器默认 `[hidden] { display: none; }`。
- 新增 `.empty-state[hidden] { display: none; }`，使 `timelineEmpty.hidden = true` 可靠生效。
- 将未运行时的空状态从 390px 纯居中大区域改为 164px 紧凑横向引导，并缩小轨道动画。
- 390px 移动宽度使用 138px 紧凑空状态，不引入页面横向溢出。

### 当前功能

- 未创建 Run 时显示紧凑引导提示。
- 一旦 Snapshot 存在 Event，空状态立即完全隐藏。
- 泳道图直接紧跟图例显示，不再有额外 390px 空白区域。
- 原有 SSE 追加、回放、节点连线、Inspector 和审批功能保持不变。
- API 和项目版本更新为 `0.5.3`。

### 已知限制

- 空状态仍只用于“尚无 Event”，尚未区分首次访问、场景启动中和加载失败等更细状态。
- Learning Console 仍为本地单用户教学界面，不是生产运维控制台。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
cd D:\AICoding\Agent
node --check src\agent_runtime\lab\static\app.js
python -m pytest -p no:cacheprovider -q
python scripts/check_docs.py
```

当前自动化测试：`37 passed`。

浏览器回归结果：

- 运行前空状态为 `display: grid`，高度为 164px。
- 运行前泳道图为 `display: none`。
- 运行后空状态为 `hidden=true`、`display: none`，高度为 0。
- 运行后泳道图为 `display: grid`。
- 图例与泳道图之间的间距为 12px。
- Tool Calling 教学场景生成 18 个泳道节点。

静态回归检查：CSS 必须包含 `.empty-state[hidden]` 和 164px 紧凑高度。

### 后续计划

可在后续版本将“初始引导”、“Run 启动中”和“加载失败”拆分为独立紧凑状态，同时保持泳道为中间区域的主视图。


<a id="e2026-08-14-005"></a>
## E2026-08-14-005：Learning Console 改用动态事件泳道图

- **完成时间**：2026-08-14
- **状态**：✅ stable
- **类型**：improvement
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/app.js`
  - `src/agent_runtime/lab/static/styles.css`
  - `tests/test_api.py`
  - `tests/test_lab_api.py`
  - `README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/LEARNING.md`
  - `docs/ROADMAP.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/0009-learning-console.md`
  - `docs/adr/README.md`
- **关联 commit**：`3559243`
- **关联 ADR**：[ADR-0009](./adr/0009-learning-console.md)

### 变更摘要

将 Learning Console 原有的单列纵向事件时间线升级为横向动态泳道图。用户现在可以同时看到事件顺序、执行角色、跨泳道跳转、相对时间和当前回放位置，减少从长列表中理解 Runtime 协作流程的认知成本。

### 系统架构

- Runtime Kernel、Event Envelope、SQLite schema、SSE API 和 Snapshot API 保持不变。
- 泳道图仍是 `agent_runtime.lab` 的浏览器展示 Adapter，只消费已持久化 RuntimeEvent。
- Run / Model / Tool / Approval / State 五条泳道由 Event type 前缀确定，`RuntimeEvent.sequence` 仍是唯一时序依据。
- 事件节点和 Inspector 继续共享同一个选中游标。

### 实现方式

- Vanilla JavaScript 根据事件数量动态计算泳道画布宽度、节点坐标和泳道坐标。
- SVG Bezier 曲线连接相邻 sequence，流动虚线用于表达从左向右的执行方向。
- 每个节点展示 sequence、相对时间、Event type 和教学标题；详细 summary 通过 title 和 Inspector 提供。
- SSE 刷新、从头回放、上一步、下一步和自动播放复用原有游标，并自动滚动到当前节点。
- 泳道标签保持在左侧，事件画布可横向滚动，移动端缩减标签文案但保留领域名。

### 当前功能

- 事件按 Run、Model、Tool、Approval 和 State 五类动态分道。
- 泳道节点通过曲线按 sequence 串联，选中节点和对应连线同步高亮。
- 时间轴显示事件 sequence 与相对耗时。
- 新 SSE 事件会追加到右侧，实时跟随时自动居中当前节点。
- 原有回放速度、事件详情、状态 diff、审批和验收功能保持可用。
- API 和项目版本更新为 `0.5.2`。

### 已知限制

- 当前泳道是五个固定领域，尚未支持用户自定义分组。
- 长 Run 需要横向滚动，尚未增加缩放、鸟瞰图、事件折叠和类型过滤。
- 连线表示持久化 sequence 关系，不是方法调用栈或分布式 Trace 因果图。
- 回放仍是浏览器展示回放，不是 Runtime Kernel 协程单步调试。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
cd D:\AICoding\Agent
node --check src\agent_runtime\lab\static\app.js
python -m pytest -p no:cacheprovider -q
python scripts/check_docs.py
agent-runtime lab --no-browser
```

当前测试：37 passed。验收覆盖泳道 DOM 骨架、CSS 领域样式、JavaScript 分道与坐标函数、API 版本、现有 4 个真实 Runtime 场景、SSE、回放、审批和 Snapshot 聚合数据。另使用无界面 Chrome 验收 1440px 桌面布局和 390px 移动布局：Tool Calling 场景生成 18 个节点、17 条连线和 5 条泳道，Token Streaming 场景的 `model.delta` 均落入 Model 泳道，移动端无 body 横向溢出。

### 后续计划

在泳道事件数较多时增加类型过滤、密度切换、鸟瞰图和按 Step 折叠；v0.6 引入 Parent/Child Run 后再扩展 Agent 与 Child Run 泳道。


<a id="e2026-08-14-004"></a>
## E2026-08-14-004：增加可视化 Learning Console v0.5.1

- **完成时间**：2026-08-14
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/api/app.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/lab/__init__.py`
  - `src/agent_runtime/lab/console.py`
  - `src/agent_runtime/lab/scenarios.py`
  - `src/agent_runtime/lab/explanations.py`
  - `src/agent_runtime/lab/routes.py`
  - `src/agent_runtime/lab/static/index.html`
  - `src/agent_runtime/lab/static/app.js`
  - `src/agent_runtime/lab/static/styles.css`
  - `tests/test_lab_api.py`
  - `tests/test_lab_scenarios.py`
  - `tests/test_api.py`
  - `docs/LEARNING.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/ROADMAP.md`
  - `docs/CHANGELOG.md`
  - `docs/README.md`
  - `docs/adr/README.md`
  - `docs/adr/0009-learning-console.md`
  - `README.md`
  - `scripts/check_docs.py`
- **关联 commit**：`19a0b20`
- **关联 ADR**：[ADR-0009](./adr/0009-learning-console.md)

### 变更摘要

增加面向初学者的本地可视化 Learning Console，将 Run、Event、Step、ToolExecution、Checkpoint、Approval、Trace、Metrics 和自动验收集中到一个浏览器页面。用户只需执行 `agent-runtime lab`，即可运行确定性场景、实时观察持久化事件、逐步回放并映射到源码处理链。

### 系统架构

- 新增 `agent_runtime.lab` 教学 Adapter，挂载到现有 FastAPI Demo App 的 `/lab`。
- Learning Console 的场景通过真实 Runtime、Provider、ToolRegistry 和 SQLiteStore 执行，不建立第二套模拟状态。
- 不同场景使用独立确定性 Provider/AgentDefinition，并共享现有 SQLite 执行事实。
- 页面复用 `/runs/{run_id}/events/stream` SSE、ObservabilityService 和持久化 Checkpoint。
- Runtime Kernel、Run 状态机、Event Envelope、Provider Protocol 和 SQLite schema 均未改变。

### 实现方式

- `ScenarioRegistry` 定义默认输入、预期事件、学习点、自动验收和人工动作提示。
- `LearningConsole` 根据 Run metadata 将场景 Run 路由回正确 Runtime，支持审批后从原 Checkpoint 恢复。
- Snapshot API 聚合 Run、Event、Step、ToolExecution、Approval、Checkpoint、Trace、Metrics 和 SQLite 记录统计。
- 每个 Event 附带教学解释、源码方法映射和 before/after 状态投影。
- 静态 HTML/CSS/Vanilla JavaScript 提供三栏界面、SSE 实时刷新、事件回放、Inspector Tabs 和审批操作，不引入 Node.js 构建链。
- SQLiteStore 增加只读 `steps_for_run()` 和 `tool_executions_for_run()` 查询，供观测 Adapter 使用。

### 当前功能

- CLI：`agent-runtime lab` 一条命令启动并默认打开浏览器。
- 页面：`GET /lab`。
- 场景 API：场景目录、启动 Run、读取 Snapshot 和处理审批。
- 首批场景：纯文本响应、Tool Calling、Token Streaming、Human Approval。
- 事件时间线支持从头回放、上一步、下一步、自动播放和速度选择。
- Inspector 支持事件解释、状态 diff、Messages、Step/ToolExecution、Trace、Metrics、SQLite 和自动验收。
- API 和项目版本更新为 `0.5.1`。

### 已知限制

- 首版仅提供 4 个确定性学习场景。
- 回放控制的是持久化事件展示，不是 Runtime 协程的内核级单步调试。
- Learning Console 面向本地单用户环境，没有认证、授权和多租户隔离。
- Snapshot 采用按请求聚合查询，不面向高并发生产控制台。
- 尚未展示多 Agent Parent/Child Run 和 Trace Tree。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
cd D:\AICoding\Agent
python -m compileall -q src tests
python -m pytest -p no:cacheprovider -q
python scripts/check_docs.py
agent-runtime lab --no-browser
```

当前测试：37 passed。新增覆盖静态页面、场景目录、CLI 参数、真实 Runtime 场景执行、Tool Calling、Token Streaming、Approval 暂停/恢复、Snapshot 教学数据、Trace、SQLite 统计和自动验收。

### 后续计划

进入 v0.6 多 Agent 编排后，在 Learning Console 中增加 Parent/Child Run Tree、委派事件、并行子 Run、聚合策略和跨 Run Trace 可视化。

---

<a id="e2026-08-14-003"></a>
## E2026-08-14-003：建立 v0.6 至 v1.0 演进路线图

- **完成时间**：2026-08-14
- **状态**：✅ stable
- **类型**：governance
- **影响范围**：
  - `docs/ROADMAP.md`
  - `docs/README.md`
  - `docs/CURRENT.md`
  - `docs/CHANGELOG.md`
  - `README.md`
  - `scripts/check_docs.py`
- **关联 commit**：`80d90c2`
- **关联 ADR**：不需要；本次只建立规划与文档治理规则，不改变 Runtime 架构和执行协议

### 变更摘要

将此前分散在 CURRENT、CHANGELOG 和对话中的后续方向整理为独立 `ROADMAP.md`，正式记录 v0.6 至 v1.0 的演进顺序、版本范围、前置依赖、非目标、验收重点和预计 ADR。

### 系统架构

无 Runtime 架构变化。新增 Roadmap 只负责描述未来计划，当前实现事实仍由 `CURRENT.md` 和 `ARCHITECTURE.md` 维护，已完成历史仍由 `CHANGELOG.md` 维护。

### 实现方式

- 路线图按版本从低到高排列，与 CHANGELOG 的完成时间倒序职责分离。
- completed 版本关联已有 Change ID，planned 版本不伪造完成记录和日期。
- 同一时间最多允许一个主版本处于 `in-progress`。
- `check_docs.py` 检查文件存在性、版本顺序、状态、完成记录和当前版本一致性。
- README 和文档中心增加 Roadmap 导航入口。

### 当前功能

- 可以从一个文件查看 v0.6 多 Agent、v0.7 Memory、v0.8 Sandbox、v0.9 Worker、v0.10 生产治理和 v1.0 稳定协议规划。
- 每个版本包含目标、计划范围、验收重点、非目标和预计 ADR。
- 路线图定义开始、完成和调整时如何同步 CURRENT、CHANGELOG、ARCHITECTURE 和 ADR。

### 已知限制

- 路线图是 Living Document，不代表固定交付日期。
- planned 内容可能随着前置版本的实现结果调整。
- 路线图只定义方向和验收边界，具体技术方案仍需在版本开始时通过 ADR 确认。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
cd D:\AICoding\Agent
python scripts/check_docs.py
python -m pytest -p no:cacheprovider -q
```

文档门禁必须识别 7 条演进记录，并验证 Roadmap 的版本顺序、状态和 Change ID 关联；现有 30 项 Runtime 测试保持通过。

### 后续计划

按照路线图进入 v0.6.0：Parent/Child Run、RunRelation、AgentRegistry 和受控 `Runtime.delegate()`。

---

<a id="e2026-08-14-002"></a>
## E2026-08-14-002：增加 Observability、Tracing、Metrics 与 Evals

- **完成时间**：2026-08-14
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/__init__.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/observability.py`
  - `src/agent_runtime/evals.py`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/api/app.py`
  - `tests/test_observability.py`
  - `tests/test_evals.py`
  - `tests/test_api.py`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/README.md`
  - `docs/adr/0008-observability-evals.md`
  - `README.md`
- **关联 commit**：`b5ddc61`
- **关联 ADR**：[ADR-0008](./adr/0008-observability-evals.md)

### 变更摘要

为 v0.4 Runtime 增加基于持久化执行事实派生的 Trace、Metrics、Prometheus 和确定性 Eval Runner，使每个运行和评估用例都可以定位到 Run、Event、Trace、报告和代码版本。

### 系统架构

- Runtime 创建 Run 时自动生成 `trace_id`，但不改变 Run 状态机和 SQLite Event schema。
- `ObservabilityService` 位于 Runtime 之外，从 Run/Event 派生 Span 和指标。
- `EvalRunner` 通过正式 `Runtime.run()` 执行用例，复用 Provider、Tool、Checkpoint、安全和恢复语义。
- FastAPI 和 CLI 作为 Adapter 暴露观测结果，核心 Runtime 不依赖遥测框架。

### 实现方式

- Run、Model、Tool 和 Approval Span 根据事件时间、sequence 和关联 ID 配对。
- Metrics 汇总 Run 状态、Event 类型、模型/工具/审批次数、token usage、平均延迟和 p95 Run 延迟。
- Prometheus 使用 `agent_runtime_*` 指标名称输出纯文本。
- Eval 内置状态、精确匹配和包含判断评估器，报告写入 Artifact Store。
- Eval Run metadata 保存 report、suite 和 case 标识，可反查 Trace 与 Event。

### 当前功能

- Python SDK：`ObservabilityService`、`RunTrace`、`MetricsSnapshot`、`EvalSuite`、`EvalCase`、`EvalRunner`。
- CLI：`agent-runtime observe metrics`、`agent-runtime observe trace <run-id>`、`agent-runtime eval demo`。
- API：`GET /observability/metrics`、`GET /observability/metrics/prometheus`、`GET /runs/{run_id}/trace`。
- 每个 Run 自动生成并持久化 `trace_id`。
- Eval Report 包含用例通过率、Run ID、Trace ID、断言、耗时和 Artifact 路径。
- API 和项目版本更新为 `0.5.0`。

### 已知限制

- Metrics 当前通过扫描本地 SQLite 历史派生，尚未做增量聚合。
- 尚未接入 OpenTelemetry Collector、外部 Trace Backend 或时序数据库。
- Eval Suite 顺序执行，尚无并发评估、数据集版本和统计显著性分析。
- 评估器暂不支持 LLM-as-a-Judge、语义相似度或工具轨迹规则。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
cd D:\AICoding\Agent
python -m compileall -q src tests
python -m pytest -p no:cacheprovider -q
python scripts/check_docs.py
agent-runtime eval demo
agent-runtime observe metrics
```

当前测试：30 passed。覆盖 trace_id、Run/Model/Tool Span、历史 Metrics、Prometheus、Observability API、Eval 通过/失败、Contains 评估、Artifact 报告和 metadata 追溯。

### 后续计划

进入 v0.6：多 Agent 编排基础，包括子 Run、委派关系、并行执行和结果汇聚，同时复用 v0.5 Trace 与 Eval 能力。

---

<a id="e2026-08-14-001"></a>
## E2026-08-14-001：增加真实模型 Provider 与 Token Streaming

- **完成时间**：2026-08-14
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/__init__.py`
  - `src/agent_runtime/providers.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/api/app.py`
  - `tests/test_providers.py`
  - `tests/test_runtime.py`
  - `tests/test_api.py`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/README.md`
  - `docs/adr/0007-model-token-streaming.md`
  - `README.md`
- **关联 commit**：`444dec4`
- **关联 ADR**：[ADR-0007](./adr/0007-model-token-streaming.md)

### 变更摘要

在 v0.3 FastAPI / SSE 基础上增加真实模型 Provider 的流式响应能力。Runtime 可以接收文本和 Tool Call 增量，产生持久化 `model.delta` 事件，并最终恢复为原有完整模型响应。

### 系统架构

- `ModelProvider.complete()` 继续作为兼容基线。
- 新增可选 `StreamingModelProvider.stream()` 和 `ModelTokenDelta`。
- Runtime Kernel 负责把 Provider 增量转换为 `model.stream.started`、`model.delta`、`model.stream.completed`。
- FastAPI 不新增第二套 token API，继续通过 `/runs/{run_id}/events/stream` 输出统一事件流。

### 实现方式

- `MockStreamingProvider` 用于确定性测试，验证多个文本 delta 和 Tool Call delta 的合并。
- `OpenAICompatibleProvider` 使用标准库 HTTP 和后台线程逐行解析 Chat Completions SSE，支持 `[DONE]`、finish reason、usage 和 Tool Call 增量。
- Runtime 对不支持 `stream()` 的 Provider 自动回退到 `complete()`，保留 v0.2/v0.3 行为。
- 流式响应完成后才生成最终 assistant message，并继续使用既有 Step、ToolExecution、Checkpoint 和审批流程。

### 当前功能

- 支持 OpenAI-compatible Chat Completions 非流式调用。
- 支持 OpenAI-compatible SSE 流式文本响应。
- 支持多个 `model.delta` 事件通过 Runtime Event Log 和 SSE 输出。
- 支持流式 Tool Call 参数拼接后进入原有工具 schema 校验。
- 支持旧 Provider 无 streaming 时自动 fallback。
- API 版本更新为 `0.4.0`。

### 已知限制

- 仍然使用标准库 HTTP；不同厂商的 SSE 扩展格式需要持续补充适配。
- 当前每个 delta 都持久化，极高吞吐场景的存储成本尚未优化。
- 标准库阻塞 HTTP 在 `asyncio.to_thread` 中执行，取消请求不能立即中断底层 socket。
- 尚未实现多 Agent 编排、分布式 Worker、长期记忆和 Docker 代码沙箱。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
cd D:\AICoding\Agent
python -m compileall -q src tests
python -m pytest -p no:cacheprovider -q
python scripts/check_docs.py
```

当前测试：25 passed。覆盖 Mock streaming、OpenAI-compatible SSE 解析、Runtime 增量事件、最终 Checkpoint 合并和 v0.3 API 回归。

### 后续计划

进入 v0.5：Observability、Metrics、Tracing 与 Evals，继续保持每个功能都有事件、测试和演进记录。

---

<a id="e2026-08-13-002"></a>
## E2026-08-13-002：增加 FastAPI Run API 与 SSE 事件接口

- **完成时间**：2026-08-13
- **状态**：✅ stable
- **类型**：feature
- **影响范围**：
  - `pyproject.toml`
  - `src/agent_runtime/api/__init__.py`
  - `src/agent_runtime/api/app.py`
  - `tests/test_api.py`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/README.md`
  - `docs/adr/0006-fastapi-sse-adapter.md`
  - `README.md`
- **关联 commit**：`f4fc22b`
- **关联 ADR**：[ADR-0006](./adr/0006-fastapi-sse-adapter.md)

### 变更摘要

为 Runtime 增加独立 FastAPI HTTP Adapter 和基于持久化 Event Log 的 SSE 接口，使客户端可以创建、查询、控制 Run，并订阅可恢复的运行事件。

### 系统架构

- FastAPI 位于 Runtime Core 之外的 Application / Adapter Layer。
- API 不直接操作 SQLite connection，而是复用 `Runtime`、`SQLiteStore` 和 `Runtime.stream()`。
- SSE 使用 Event sequence 作为事件 id，并通过 `after_sequence` 支持断点续传。
- API 依赖通过 optional extra 提供，核心包默认不依赖 FastAPI。

### 实现方式

- 新增 `agent_runtime.api.create_app()` 和 `create_demo_app()`。
- `POST /runs` 使用 `Runtime.start()` 异步启动 Run，返回 `202 Accepted`。
- 历史事件通过 `GET /runs/{run_id}/events` 读取；事件流通过 `StreamingResponse` 输出。
- 暴露 pause、resume、cancel 和 approval resolve，保留 Runtime 原有状态机与审批语义。
- 使用 Pydantic 请求模型校验创建 Run 和审批决策。

### 当前功能

- `GET /health`。
- `POST /runs`、`GET /runs/{run_id}`。
- `GET /runs/{run_id}/events`，支持 `after_sequence`。
- `GET /runs/{run_id}/events/stream`，输出 `id`、`event`、`data` 三段 SSE。
- Run pause / resume / cancel。
- Pending approval 查询与审批决策提交。
- 不存在的 Run 或 Approval 返回 404，非法生命周期操作返回 409。

### 已知限制

- SSE 当前通过 SQLite polling，不是跨进程消息总线。
- SSE 输出 Runtime Event，不是模型 token 原生流。
- API 内异步任务属于当前进程，尚无 Worker lease、鉴权、限流和多租户隔离。
- `create_demo_app()` 只注册确定性的 Demo Agent。

### 测试与验收

2026-08-13 验证结果：`19 passed`。

```powershell
cd D:\AICoding\Agent
python -m pytest -p no:cacheprovider
python scripts/check_docs.py
```

测试覆盖健康检查、创建和查询 Run、历史事件顺序、SSE 编码、断点续传、生命周期接口和 404 行为。

### 后续计划

进入 v0.4：增加真实模型 Provider 的 token streaming，并保持 Runtime Event 与 Model Token Stream 的协议边界。

---

<a id="e2026-08-13-001"></a>
## E2026-08-13-001：完成可靠单 Agent 执行 v0.2

- **完成时间**：2026-08-13
- **状态**：✅ stable
- **类型**：milestone
- **影响范围**：
  - `README.md`
  - `pyproject.toml`
  - `src/agent_runtime/cli.py`
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/tools.py`
  - `tests/test_runtime.py`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/README.md`
  - `docs/adr/0005-tool-execution-idempotency.md`
- **关联 commit**：`a765fa6`
- **关联 ADR**：[ADR-0005](./adr/0005-tool-execution-idempotency.md)

### 变更摘要

将 v0.1 的 Checkpoint 级恢复深化为可追踪的 Step / ToolExecution 执行模型，增加 SQLite schema migration、状态与事件原子提交、工具幂等恢复、多工具审批队列、活动任务取消和未知副作用人工处置。

### 系统架构

- Runtime Kernel 新增 `Step` 和 `ToolExecution` 两级持久化执行对象。
- SQLite 新增 `schema_migrations`、`steps`、`tool_executions` 表。
- 状态、工具结果、Checkpoint 和 Event 可以在同一事务中提交。
- 恢复流程优先处理未完成 Step，再决定是否请求下一次模型响应。
- 副作用工具的执行结果无法确认时进入 `unknown`，Run 自动暂停。

### 实现方式

- `ToolExecution` 使用 `run_id + step_id + tool_call_id` 生成稳定 idempotency key。
- 已完成、失败或拒绝的工具执行恢复时直接重建 tool message，不再调用 handler。
- 模型一次返回的全部工具调用先持久化为有序队列，审批后继续处理后续调用。
- `CancellationToken` 注入 `ToolContext`，`Runtime.cancel()` 同时取消活动 asyncio Task。
- SQLite 通过编号迁移兼容 v0.1 数据库，并为审批记录补充 ToolExecution 关联。

### 当前功能

- 支持 Step 和 ToolExecution 查询与恢复。
- 支持工具结果幂等复用。
- 支持多个工具调用逐项审批和继续执行。
- 支持副作用结果未知时暂停并由 `resolve_unknown_tool()` 处置。
- 支持 Run 状态与 Event、工具结果与 Checkpoint 的事务一致性。
- 支持模型调用和异步工具的主动取消传播。

### 已知限制

- 外部系统的真正幂等仍需工具 handler 使用 `ToolContext.idempotency_key` 配合实现。
- 同步阻塞 handler 无法被 Python 强制安全中断，只能在返回前后检查取消信号。
- 未实现 Worker lease、heartbeat 和跨节点所有权。
- ToolExecution 当前按顺序执行，尚未支持安全的并行工具调度。
- `unknown` 副作用需要应用层或 CLI 提供更完整的人工处置界面。

### 测试与验收

2026-08-13 验证结果：`15 passed`。

```powershell
cd D:\AICoding\Agent
python -m pytest -p no:cacheprovider
python scripts/check_docs.py
agent-runtime demo "19 * 23"
```

测试覆盖：

- v0.1 SQLite schema 自动迁移到 v0.2。
- 多工具审批后继续执行。
- 已完成工具恢复时不重复执行。
- 副作用工具运行中重启后标记 unknown 并暂停。
- 状态与事件事务在故障注入时整体回滚。
- 活动异步工具取消传播和 cancelled 状态持久化。
- unknown 工具经人工确认后继续恢复。
- 既有状态机、工具校验、workspace 安全和审批回归。

### 后续计划

进入 v0.3：在保持 Runtime Core 无 HTTP 依赖的前提下增加 FastAPI Run API 和 SSE 持久化事件接口。
---

<a id="e2026-08-11-002"></a>
## E2026-08-11-002：建立可追溯演进文档体系

- **完成时间**：2026-08-11
- **状态**：✅ stable
- **类型**：governance
- **影响范围**：
  - `README.md`
  - `docs/README.md`
  - `docs/CURRENT.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CHANGELOG.md`
  - `docs/adr/README.md`
  - `docs/templates/change-entry.md`
  - `docs/templates/adr.md`
  - `scripts/check_docs.py`
  - `.github/workflows/quality.yml`
  - `.github/PULL_REQUEST_TEMPLATE.md`
- **关联 commit**：`b4adc90`
- **关联 ADR**：不需要；该变更建立治理流程，不改变 Runtime 公共接口、数据模型或安全边界。

### 变更摘要

建立当前状态、当前架构、倒序演进时间线、ADR、模板和自动检查职责分离的文档体系。

### 系统架构

Runtime 代码架构不变；新增围绕代码事实的文档治理层和 CI 门禁。

### 实现方式

- `CURRENT.md` 维护当前能力和限制。
- `ARCHITECTURE.md` 维护当前实现结构和语义。
- `CHANGELOG.md` 使用 Change ID 按完成时间倒序记录演进。
- ADR 记录影响公共接口、数据、可靠性或安全性的决策。
- `scripts/check_docs.py` 校验结构、格式、时间、路径、链接和 Git 变更门禁。
- CI 同时执行文档检查和测试。

### 当前功能

- 可以从单一入口定位当前状态、当前架构、历史记录和 ADR。
- 每个状态项均关联 Change ID。
- 核心 Runtime 代码变化时可以检查是否同步修改演进记录。

### 已知限制

- 自动检查只能识别文件级变更，是否需要 ADR 仍需开发者根据模板判断。

### 测试与验收

本地 Python 3.13 全量结果为 `214 passed`；core line coverage 为 `91.71%`，core branch coverage 为 `80.10%`。100 Run 本地稳定验收、可靠性快速压力、sdist/wheel 构建和干净虚拟环境发行 smoke 均通过。

```powershell
python scripts/check_docs.py
pytest
```

### 后续计划

后续每项功能按 Change ID、实现、测试、文档和 ADR 流程演进。

---

<a id="e2026-08-11-001"></a>
## E2026-08-11-001：完成单 Agent Runtime MVP

- **完成时间**：2026-08-11
- **状态**：✅ stable
- **类型**：milestone
- **影响范围**：
  - `src/agent_runtime/domain.py`
  - `src/agent_runtime/runtime.py`
  - `src/agent_runtime/providers.py`
  - `src/agent_runtime/tools.py`
  - `src/agent_runtime/storage.py`
  - `src/agent_runtime/sdk.py`
  - `src/agent_runtime/cli.py`
  - `tests/test_domain.py`
  - `tests/test_runtime.py`
  - `tests/test_tools.py`
- **关联 commit**：`b4adc90`
- **关联 ADR**：[ADR-0001](./adr/0001-runtime-kernel.md)、[ADR-0002](./adr/0002-model-provider-protocol.md)、[ADR-0003](./adr/0003-sqlite-event-checkpoint.md)、[ADR-0004](./adr/0004-tool-security-boundary.md)

### 变更摘要

完成第一版可持久化单 Agent Runtime，实现模型决策、工具执行、状态持久化、事件追踪、人工审批和失败收敛闭环。

### 系统架构

- 引入 Runtime Kernel 和明确的 Run 状态机。
- 引入统一 Model Provider 接口。
- 引入 Tool Registry 和受控工具执行。
- 引入 SQLite Event Log、Checkpoint 和 Approval。
- 引入 Python SDK 和 CLI。

### 实现方式

- Python 标准库实现核心 Runtime。
- SQLite 保存 Run、Event、Checkpoint 和 Approval。
- Mock Provider 用于测试和本地 Demo。
- OpenAI-compatible Provider 用于真实模型接入。
- 工具执行支持 schema 校验、超时和 workspace 路径限制。

### 当前功能

- 支持单 Agent 执行和工具调用。
- 支持持久化事件流读取。
- 支持暂停、恢复和取消。
- 支持高风险工具人工审批。
- 支持从最近 Checkpoint 恢复消息历史。
- 支持 Python SDK 和 CLI。

### 已知限制

- 暂无 FastAPI / SSE API。
- 暂无多 Agent 和分布式 Worker。
- 暂无长期记忆。
- 暂无容器级代码沙箱。
- 尚无数据库迁移和分布式幂等协议。

### 测试与验收

2026-08-11 验证结果：`8 passed`。

```powershell
cd D:\AICoding\Agent
python -m pip install pytest pytest-asyncio
pytest
agent-runtime demo "19 * 23"
```

### 后续计划

深化单 Agent 执行可靠性，再增加 FastAPI 和 SSE 事件接口。

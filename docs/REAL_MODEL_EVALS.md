# 真实模型稳定性验收

v0.8.19 增加 `local-real-model` 验收套件；v0.8.20 根据首轮完整真实基线收紧新建文件验证证据，用固定、隔离、可重复的任务判断真实模型与 Runtime 组合是否稳定。它不是模型能力排行榜，也不会把当前项目 Workspace 复制进验收报告。

## 为什么使用隔离 Workspace

每个 Case 都在以下独立目录运行：

```text
<state-dir>/evals/<report-id>/cases/<attempt>-<case>/
├── workspace/   # 合成的小型 Git 项目
└── state/       # 该 Case 独立 SQLite、Artifact 和日志
```

真实 Provider 只会收到内置合成 Fixture、Case Prompt 和该 Case 的 Tool Result。调用 `agent-runtime eval run` 时不会把原工作目录的源码、Session 历史或 Tool 结果带入模型请求。报告只保存结构统计、Run/Trace ID、Tool 名称与计数、断言结果、最终答案长度和 SHA-256，不保存 Prompt、文件内容、Tool arguments/result 或最终答案原文。

## 内置 Case

| Case | 类型 | 主要检查 |
| --- | --- | --- |
| `explain-project` | explanation | 只读解释、收敛、无写入 |
| `inspect-implementation` | inspection | 搜索/读取、具体代码事实、无写入 |
| `small-verified-edit` | small-edit | 有界修改、Approval、Git diff、pytest 验证 |
| `missing-file-recovery` | failure-recovery | 文件缺失时不编造、不创建、有限失败 |
| `approval-lifecycle` | lifecycle | durable Approval、同一 Run resume、`git_diff` 与新文件 `git_status` 检查 |

## 第一次运行

该命令会调用配置中的真实模型，并可能产生模型费用：

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
agent-runtime eval run --suite local-real-model
```

只跑一个只读 Case：

```powershell
agent-runtime eval run --suite local-real-model --case explain-project
```

重复三次观察波动：

```powershell
agent-runtime eval run --suite local-real-model --repeat 3
```

指定报告位置：

```powershell
agent-runtime eval run --suite local-real-model --output .\.agent-runtime\eval-report.json
```

`serve`、`chat` 和 `eval run` 共用本地 Owner Lock，不能同时占用同一状态目录。

## 离线回归比较

真实模型运行完成后，可以在不调用模型、不占用 Runtime Owner Lock 的情况下比较历史报告：

```powershell
agent-runtime eval compare .\baseline-report.json .\candidate-report.json

# 只比较明确指定的 Case（结果标记为 partial）
agent-runtime eval compare .\baseline-report.json .\candidate-report.json --case approval-lifecycle
```

比较规则：

- 退出码 `0`：没有发现可靠性回归。
- 退出码 `1`：发现以前通过的 Attempt 失败、`verified` 退化、协议违规增加或 UNKNOWN Tool Outcome 增加。
- 退出码 `2`：报告格式错误、Suite name/version/checksum 不一致，或严格模式下 Case/Attempt 范围不一致，不能安全比较。
- Provider、模型、Runtime 版本变化和单 Case 耗时超过 20% 只记录 warning，不自动阻断。
- 严格模式要求 Baseline/Candidate 的 Case/Attempt 集合完全一致；只有显式 `--case` 才允许 partial compare。

比较结果不包含 Prompt、Fixture、Tool 参数/结果或最终答案原文；输出中的 Case/Run/Trace 标识可用于回看对应的隔离 durable Event。

## 报告怎么看

重点字段：

- `pass_rate`：本次所有 Case/attempt 的断言通过率。
- `status`：durable Run 最终状态。
- `step_count` / `tool_call_count`：是否在预算内完成。
- `duplicate_tool_calls`：完全相同 Tool + arguments 的重复次数。
- `failed_tool_calls` / `unknown_tool_calls`：执行失败和不确定副作用。
- `protocol_violations`：文本化 Tool Call 等已知协议违规。
- `finalization_requests` / `finalization_contexts`：是否进入收敛综合及 Fresh Context。
- `verification_status`：修改任务是否具备 diff、new-file status 与代码验证证据。
- `created_file_writes` / `git_status_inspected`：是否新建文件，以及是否成功检查未跟踪文件状态。
- `approval_requests` / `approval_resolutions`：Approval 生命周期是否闭环。

最新报告固定复制到：

```text
<state-dir>/evals/latest-report.json
```

每次完整报告和 Case SQLite 会保留在独立 `<report-id>` 目录，便于根据 `run_id` 回看 Event 和 ToolExecution。

## 2026-08-20 首次完整真实基线

使用已配置的 OpenAI-compatible Provider 和 `deepseek-v4-flash` 执行 5 个 Case × 3 repeats：

```text
total_attempts: 15
passed_attempts: 15
failed_attempts: 0
pass_rate: 1.0
failed assertions: 0
```

这说明固定断言全部通过，但不等于证据模型没有缺口。进一步检查每个隔离 Case 的 durable Run/Event/ToolExecution 后发现：`approval-lifecycle` 创建的 `RESULT.txt` 是 untracked 文件，默认 `git diff` 返回 no tracked differences；旧逻辑只看是否调用过 `git_diff`，因此可能在没有 `git_status` 的情况下误标 `verified`。v0.8.20 通过 `write_text_file.created`、Completion Evidence 和 Acceptance Metrics 修复该问题，并给 pytest Fixture 增加 `.gitignore`，排除 `__pycache__`/`.pytest_cache` 噪声。

该修复来自真实 durable 事实，不是针对模型名称、答案文本或单次随机输出的特判。 v0.8.21 延续该原则：验收指标只把最后一次成功写入之后的 diff/status/validation 视为修改后的证据。v0.8.23 进一步将 Case/Repeat selection 写入报告，避免不同范围的报告被误判为完整通过。
## 建议的收敛流程

```text
固定 Case 复现
→ 查看失败断言
→ 打开对应 Case 的 durable SQLite/Event
→ 确认真实根因
→ 做最小 Runtime 修复
→ 增加自动化回归测试
→ 重跑同一 Case 及完整套件
```

不要仅因为一次模型输出不同就增加 Provider 特判。优先确认失败属于 Context、Tool 选择、协议、收敛、验证还是生命周期问题。

## 当前限制

- 真实模型输出存在随机性，一次通过不代表长期稳定；重要修改建议 `--repeat 3`。
- v0.8.20 继续使用确定性 Runtime 指标，不使用 LLM-as-a-Judge，因此不评价答案文风或深层语义正确性。
- Case 默认串行运行，避免模型并发和 Fixture 互相干扰。
- 报告不保存原文；需要诊断时应在本机查看对应隔离 Run，向外分享前仍需人工检查。
- 内置修改任务依赖本机 Git、配置中启用的 `run_process` 以及可执行的 pytest 环境。

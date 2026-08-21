# Agent Runtime

一个以 Python + SQLite 实现的可持久化 Agent Runtime。当前版本是 **v0.8.22 Acceptance Report Regression Gate**，优先解决单机、单用户、本地可信环境下的稳定启动、持续运行、恢复、诊断、学习和直接终端交互问题。

当前支持 Interactive CLI、单 Agent、多 Agent Workflow、Tool Calling、Model Streaming、Approval、Checkpoint、Session/Memory、FastAPI/SSE、备份恢复、诊断、Learning Console，以及受限本地进程 Sandbox。当前不把公网部署、分布式 Worker、多租户、Docker 强隔离和 Secret 生命周期作为本地稳定版的前置条件。

## 最短路径：直接在终端对话

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[api]"

agent-runtime init
# 也可以复制 agent-runtime.example.toml 为 agent-runtime.toml 后修改
agent-runtime chat
```

`chat` 会启动本地 Interactive CLI，通过 Streaming Markdown 显示模型输出，按 Inspecting / Editing / Verifying 阶段投影执行过程，默认以 compact 单行摘要展示 Tool 调用，并将每轮对话保存为可恢复的 Session。常用方式：

```powershell
agent-runtime chat -c                         # 继续最近一次终端会话
agent-runtime chat -r <session_id>            # 恢复指定会话
agent-runtime chat --verbose                  # 展开有界 Tool 参数和多行结果
agent-runtime chat -p "19 * 23" --no-color   # 只输出最终结果后退出
```

完整命令、Session 语义和交互流程见 [Interactive CLI 使用指南](./docs/INTERACTIVE_CLI.md)。

## 本地编码闭环

v0.8.22 的标准本地 Agent 已注册 `list_files`、`search_text`、`read_file_lines`、`read_text_file`、`read_artifact`、`replace_text`、`apply_patch`、`write_text_file`、只读 `git_status/git_diff` 和受限 `run_process`。可以直接在 `chat` 中要求模型检查、修改并验证 Workspace。修改发生后，Runtime 会根据持久化 diff/status/validation 证据标记 `verified` 或 `unverified`；新建未跟踪文件除了 `git_diff` 还必须成功检查 `git_status`，缺证据时最多追加一次验证提醒。

```text
请找到 examples 里的最小 Python 示例，补充一条清晰注释，并运行 Python 做语法检查。修改文件和执行进程前先让我确认。
```

大 Tool Result 会给出 `read_artifact` 分页入口，避免再次读取 Artifact 时递归生成新 Artifact，也不再需要通过 Python/`run_process` 打印文件。根目录发现默认过滤 `.runtime-test-data`、`.coverage` 和 `coverage.json`；列表被截断时，内建协议要求继续缩小路径或使用 `search_text`。v0.8.14 会在参数错误时列出 Tool 允许字段、为 `search_text(max_lines=...)` 给出 `max_results`/`read_file_lines` 修正提示、在无扩展名错误路径后建议同 stem 文件，并复用完全相同的只读结果。对于参数不同但搜索命中或读取区间高度重叠的检查，Runtime 会记录 no-progress；达到边界后执行一次不暴露 Tool 的最终综合，避免简单解释任务以 `max_steps` 失败。v0.8.15 会把 durable `run.input` 作为不可压缩的当前请求保留，并在 finalization 前以 user message 再次聚焦原始问题，避免模型误报“看不到用户请求”或输出无关的文件修改状态。 v0.8.16 会识别 finalization 中被模型错误输出为普通文本的 DSML、XML 和已知 Tool JSON envelope，不执行其中的调用、不把它当成成功答案，并在缓冲流式输出后进行一次有界的纯自然语言修复；重复违规会明确失败。v0.8.17 进一步在检测边界执行 Unicode NFKC 兼容归一化，并允许 DSML marker 中有限的重复竖线和空白，因此真实模型输出的 `<｜｜DSML｜｜...>`、`<||DSML||...>` 等协议变体也不能绕过保护。 v0.8.18 在进入最终综合时不再复用充满 Assistant Tool Call 和 Tool Result 的原始消息轨迹，而是新建只含严格 system 指令、纯文本 Session 摘要、有界 durable evidence digest 和原始请求的 Fresh Context；首次协议修复也继续使用同一隔离上下文。v0.8.20 根据 5 个 Case × 3 轮真实模型基线补齐新建文件证据：`write_text_file` 持久化 `created`，新建文件只有 `git_diff` 而没有后续 `git_status` 时不会被标记为 `verified`。 v0.8.21 进一步要求 `git_diff`、`git_status` 和 validation 都必须发生在最后一次写入之后，写入前的成功测试不能替代修改后的重新验证。v0.8.22 增加 `agent-runtime eval compare`，可在不调用模型的情况下比较两次脱敏验收报告并阻断可靠性回归。

文件写入和进程执行仍需终端 Approval。Runtime Approval 是副作用操作的唯一确认步骤，模型不会先要求一次口头确认；审批卡片会显示命令、工作目录、超时、目标文件、编辑数量和聚焦差异预览。批准后 CLI 会继续消费同一 Run 的 Tool、验证和最终回答 Event，直到 Run 真正结束后才返回 `You >`；完成修改任务后还会列出 changed files、Git diff 与 validation 事实。`python scripts/check_docs.py` 等内建检查脚本会显示为 `Verifying changes`；同名 Tool 先失败后成功时，CLI 会将旧错误标记为 recovered，而不是继续误报 `Task incomplete`。使用 `/workspace` 查看编码工具和进程开关，使用 `/diff` 查看当前 Session 最近的 Tool 文件变更摘要。完整协议、配置和边界见 [Coding Workspace Tools 使用指南](./docs/CODING_TOOLS.md)。

## 启动本地 HTTP Runtime

```powershell
agent-runtime status
agent-runtime serve
```

默认配置使用离线 `MockProvider`，服务只监听 `127.0.0.1:8000`。另开一个 PowerShell 验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
agent-runtime status
```

`chat` 与 `serve` 对同一个状态目录共享单 Owner Lock，因此不能同时运行。停止服务时在运行窗口按 `Ctrl+C`。完整配置、单实例锁、日志和恢复说明见 [本地稳定 Runtime 运行指南](./docs/LOCAL_RUNTIME.md)。

## 接入真实模型

初始化后编辑 `agent-runtime.toml`：

```toml
[model]
provider = "openai-compatible"
model = "your-model"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
```

然后在本机设置环境变量并启动：

```powershell
$env:OPENAI_API_KEY = "..."
agent-runtime chat
```

配置文件只保存环境变量名称，不保存 API Key 明文。

## 可视化学习

```powershell
agent-runtime lab
```

浏览器中的 Learning Console 会展示真实 Runtime 执行产生的事件、独立 Agent 泳道、Parent/Child 委派、Tool、Model Delta、Checkpoint、Memory、Artifact 和 Sandbox 状态。它是教学入口，不替代正式的 `agent-runtime serve` 本地服务入口。

详见 [Learning Console 使用指南](./docs/LEARNING.md)。

## 常用命令

```powershell
# 直接终端对话
agent-runtime chat
agent-runtime chat -c

# 离线算术 Demo
agent-runtime demo "19 * 23"

# 多 Agent 串行 Workflow
agent-runtime workflow demo "分析一个需求并给出结论"

# 状态、诊断与备份
agent-runtime status
agent-runtime doctor --json
agent-runtime observe diagnostics
agent-runtime backup create

# 本地确定性 Runtime 验收
python scripts\verify_local_runtime.py --runs 100 --concurrency 8

# 隔离真实模型验收（会调用配置模型并可能产生费用）
agent-runtime eval run --suite local-real-model --case explain-project
agent-runtime eval compare .\baseline-report.json .\candidate-report.json
```

## 当前边界

- 支持目标：单机、单用户、本地 SQLite、可信 Tool 和脚本。
- HTTP 服务只允许 loopback 地址。
- `LocalProcessSandbox` 是受限本地进程执行器，不是容器或虚拟机。
- 不承诺安全执行任意不可信代码。
- DockerSandbox、SecretProvider、分布式 Worker、多租户和 RBAC 暂缓，等待真实本地使用反馈。

## 文档入口

- [当前功能状态](./docs/CURRENT.md)
- [当前系统架构](./docs/ARCHITECTURE.md)
- [Interactive CLI 使用指南](./docs/INTERACTIVE_CLI.md)
- [Coding Workspace Tools 使用指南](./docs/CODING_TOOLS.md)
- [本地稳定 Runtime 运行指南](./docs/LOCAL_RUNTIME.md)
- [真实模型稳定性验收指南](./docs/REAL_MODEL_EVALS.md)
- [演进历史](./docs/CHANGELOG.md)
- [演进路线图](./docs/ROADMAP.md)
- [ADR 决策记录](./docs/adr/README.md)
- [文档中心](./docs/README.md)

## 开发与发布检查

```powershell
python scripts/check_docs.py
python -m ruff check src tests scripts
python -m mypy src\agent_runtime
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -p no:cacheprovider
python scripts/check_coverage.py coverage.json
python scripts/verify_local_runtime.py --runs 100 --concurrency 8
python -m build
python scripts/verify_distribution.py dist
```

核心实现位于 `src/agent_runtime/`，测试位于 `tests/`。版本、架构、功能状态和构建记录必须随代码同步更新。

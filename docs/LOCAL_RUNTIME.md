# 本地稳定 Runtime 运行指南

- **适用版本**：v0.8.8+
- **最近更新**：2026-08-18
- **关联变更**：[E2026-08-18-001](./CHANGELOG.md#e2026-08-18-001)、[E2026-08-17-001](./CHANGELOG.md#e2026-08-17-001)、[E2026-08-16-006](./CHANGELOG.md#e2026-08-16-006)、[E2026-08-16-005](./CHANGELOG.md#e2026-08-16-005)
- **关联决策**：[ADR-0032](./adr/0032-artifact-paging-workspace-discovery.md)、[ADR-0028](./adr/0028-coding-workspace-tools.md)、[ADR-0027](./adr/0027-interactive-cli-session-history.md)、[ADR-0026](./adr/0026-local-runtime-bootstrap-single-owner.md)

## 1. 目标和信任边界

v0.8.3 延续 v0.8.2 的本地稳定边界，并将 Interactive CLI 作为默认人工使用入口。当前支持目标收敛为单机、单用户、本地 SQLite 和可信 Tool/脚本。Runtime 默认只允许监听 `127.0.0.1`、`localhost` 或 `::1`，不提供公网服务、多租户、分布式调度或任意不可信代码强隔离。

v0.8.0 的 Workspace 边界、Tool Capability、审批、argv、白名单、timeout、输出限制和进程树取消继续保留，但 SecretProvider 和 DockerSandbox 不再是本地稳定版的阻塞项。

## 2. 初始化

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[api]"
agent-runtime init
```

初始化会创建：

```text
agent-runtime.toml
.agent-runtime/
```

如果配置已经存在，命令会拒绝覆盖；只有显式执行 `agent-runtime init --force` 才会替换。

## 3. 配置

默认配置使用离线 Mock Provider：

```toml
[runtime]
workspace = "."
state_dir = ".agent-runtime"
agent_name = "local"
run_timeout_seconds = 300
shutdown_timeout_seconds = 30
max_inflight_runs = 8
max_concurrent_model_requests = 4

[model]
provider = "mock"
model = "arithmetic-demo"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
timeout_seconds = 60

[tools]
sync_workers = 8
pending_queue_size = 32
enable_process = true
allowed_executables = ["python", "git"]
process_timeout_seconds = 120
process_max_output_bytes = 1000000
process_max_concurrent = 2

[workspace_context]
instructions_enabled = true
instruction_files = ["AGENTS.md", "CLAUDE.md"]
max_instruction_chars = 50000

[api]
host = "127.0.0.1"
port = 8000

[logging]
level = "INFO"
file = "logs/runtime.log"
max_file_size_mb = 20
backup_count = 5
```

配置优先级为：

```text
CLI override > AGENT_RUNTIME_* 环境变量 > TOML > 内置默认值
```

真实模型示例：

```powershell
$env:OPENAI_API_KEY = "..."
```

```toml
[model]
provider = "openai-compatible"
model = "your-model"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
```

配置只保存环境变量名称，不保存 API Key 值。


### 3.1 Project instruction files

v0.8.6 looks for root-level `AGENTS.md` and `CLAUDE.md` by default. Existing files are combined with configured `system_prompt` and the built-in coding protocol under one character budget. Restart `chat` or `serve` after changing instructions. `agent-runtime status` and `/workspace` expose only source metadata.

## 4. Interactive CLI

最直接的使用方式是启动终端 Agent Shell：

```powershell
agent-runtime chat
```

常用启动参数：

```powershell
agent-runtime chat -c                         # 继续最近一次 Interactive CLI Session
agent-runtime chat -r <session_id>            # 恢复指定 Session
agent-runtime chat "先读取 README 再总结"     # 启动后立即发送第一条消息
agent-runtime chat -p "19 * 23" --no-color   # 单次执行并退出，适合脚本
```

Shell 会实时消费持久化 Event：`model.delta` 直接追加为回答，Tool 请求与结果以状态行展示，`approval.requested` 会进入 `y/n` 终端确认。活动 Run 期间按 `Ctrl+C` 会请求取消当前 Run 并回到提示符；输入阶段按 `Ctrl+C` 清空输入，`Ctrl+D` 或 `/quit` 退出。

每轮输入都是独立 Run，但共享同一个 Session。`/new` 创建新 Session，`/sessions` 查看最近会话，`/resume <session_id>` 切换会话，`/events` 查看最近 Run 的 durable Event。Session 历史默认只加载最近 20 个已完成 Run 的 user input 和 final assistant result；Tool 中间消息不会自动回放。`<state_dir>/cli-history` 只是终端输入历史，不等同于 Runtime Session。

`chat` 是 embedded Runtime Owner，会获取与 `serve` 相同的 `runtime.lock`。因此同一状态目录下应二选一：先停止 `serve` 再启动 `chat`，或为其中一个入口使用不同的 `--state-dir`。当前版本不支持让 `chat` attach 到已经运行的 HTTP 服务。

完整说明见 [Interactive CLI 使用指南](./INTERACTIVE_CLI.md)。

标准本地 Agent 同时注册文件列表、文本搜索、有界文件读取、Tool Result Artifact 分页读取、精确替换和受限进程 Tool。使用 `/workspace` 查看生效状态，使用 `/diff` 查看当前 Session 的 Tool 文件变更摘要。`read_artifact` 只读当前 Run 的 Tool Result Artifact，无需进程审批；发现结果截断时 Agent 会继续缩小范围。协议和限制见 [Coding Workspace Tools 使用指南](./CODING_TOOLS.md)。

## 5. 启动和状态

```powershell
agent-runtime serve
```

启动流程固定为：

```text
读取并校验配置
→ 获取 runtime.lock
→ 创建 Provider 与 ToolRegistry
→ 打开 SQLite 并执行恢复协调
→ 注册本地 AgentDefinition
→ 配置轮转 JSON 日志
→ 启动 FastAPI
```

查看状态：

```powershell
agent-runtime status
```

状态包括：

- Runtime 是否正在运行。
- Owner PID、主机和启动时间。
- 配置的非敏感生效值。
- SQLite 路径、大小、schema、WAL 和 quick_check。
- Artifact 文件数量和大小。
- 日志文件大小。

## 6. 单实例所有权

每个状态目录只允许一个本地执行 Owner：

```text
.agent-runtime/runtime.lock
```

服务会打开 `runtime.lock`，写入 PID、hostname、版本、启动时间和随机 token，并在整个运行期间持有操作系统级非阻塞排他文件锁。第二个 `serve` 连接同一状态目录时无法获得锁，会返回 `LocalRuntimeLockError`，不会继续打开执行循环。

如果进程被强杀，操作系统会自动释放排他锁；锁文件中的元数据可以保留，但下一次启动会安全获得锁并覆盖遗留内容，然后进入现有 startup reconciliation。Windows 使用 Process Handle 进行只读状态判断，而不是依赖 `os.kill(pid, 0)`。

## 7. 日志

本地服务同时输出 stderr JSON 日志和轮转文件：

```text
.agent-runtime/logs/runtime.log
.agent-runtime/logs/runtime.log.1
...
```

默认单文件 20 MB，保留 5 份。现有键名和值级基础脱敏继续生效。

## 8. 停止、诊断和备份

前台运行时使用 `Ctrl+C`。FastAPI lifespan 会调用受控 `Runtime.shutdown()`，关闭 Provider、Tool 线程池、Sandbox、SQLite，并在退出后释放 Owner Lock。

常用命令：

```powershell
agent-runtime status
agent-runtime doctor --json
agent-runtime observe diagnostics
agent-runtime backup create
```

## 9. 自动验收

快速本地验收：

```powershell
python scripts\verify_local_runtime.py --runs 100 --concurrency 8
```

它会验证：

- 配置生成和加载。
- 单实例锁与重复启动拒绝。
- 并发 Run 和 Event sequence。
- `/health`。
- SQLite health。
- 在线备份与校验。
- shutdown 幂等。
- 同一状态目录重启和历史 Run 保留。
- 线程和 asyncio Task 回到基线。

长稳继续复用：

```powershell
python scripts\run_reliability.py --stress-runs 100 --concurrency 20 --soak-seconds 1800
```

## 10. 当前明确不做

- 公网监听和认证。
- 多用户或多租户。
- 分布式 Worker、Queue 和 Lease。
- DockerSandbox。
- SecretProvider。
- 任意不可信代码执行承诺。
- 自动后台服务安装。

这些能力只有在本地 Runtime 经过真实使用后仍有明确需求时再进入路线。

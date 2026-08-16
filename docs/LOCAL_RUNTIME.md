# 本地稳定 Runtime 运行指南

- **适用版本**：v0.8.1+
- **最近更新**：2026-08-16
- **关联变更**：[E2026-08-16-005](./CHANGELOG.md#e2026-08-16-005)
- **关联决策**：[ADR-0026](./adr/0026-local-runtime-bootstrap-single-owner.md)

## 1. 目标和信任边界

v0.8.1 将当前支持目标收敛为单机、单用户、本地 SQLite 和可信 Tool/脚本。Runtime 默认只允许监听 `127.0.0.1`、`localhost` 或 `::1`，不提供公网服务、多租户、分布式调度或任意不可信代码强隔离。

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

## 4. 启动和状态

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

## 5. 单实例所有权

每个状态目录只允许一个本地执行 Owner：

```text
.agent-runtime/runtime.lock
```

服务会打开 `runtime.lock`，写入 PID、hostname、版本、启动时间和随机 token，并在整个运行期间持有操作系统级非阻塞排他文件锁。第二个 `serve` 连接同一状态目录时无法获得锁，会返回 `LocalRuntimeLockError`，不会继续打开执行循环。

如果进程被强杀，操作系统会自动释放排他锁；锁文件中的元数据可以保留，但下一次启动会安全获得锁并覆盖遗留内容，然后进入现有 startup reconciliation。Windows 使用 Process Handle 进行只读状态判断，而不是依赖 `os.kill(pid, 0)`。

## 6. 日志

本地服务同时输出 stderr JSON 日志和轮转文件：

```text
.agent-runtime/logs/runtime.log
.agent-runtime/logs/runtime.log.1
...
```

默认单文件 20 MB，保留 5 份。现有键名和值级基础脱敏继续生效。

## 7. 停止、诊断和备份

前台运行时使用 `Ctrl+C`。FastAPI lifespan 会调用受控 `Runtime.shutdown()`，关闭 Provider、Tool 线程池、Sandbox、SQLite，并在退出后释放 Owner Lock。

常用命令：

```powershell
agent-runtime status
agent-runtime doctor --json
agent-runtime observe diagnostics
agent-runtime backup create
```

## 8. 自动验收

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

## 9. 当前明确不做

- 公网监听和认证。
- 多用户或多租户。
- 分布式 Worker、Queue 和 Lease。
- DockerSandbox。
- SecretProvider。
- 任意不可信代码执行承诺。
- 自动后台服务安装。

这些能力只有在本地 Runtime 经过真实使用后仍有明确需求时再进入路线。

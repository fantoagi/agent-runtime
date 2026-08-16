# Agent Runtime

一个以 Python + SQLite 实现的可持久化 Agent Runtime。当前版本是 **v0.8.1 Local Stable Runtime**，优先解决单机、单用户、本地可信环境下的稳定启动、持续运行、恢复、诊断和学习问题。

当前支持单 Agent、多 Agent Workflow、Tool Calling、Model Streaming、Approval、Checkpoint、Session/Memory、FastAPI/SSE、备份恢复、诊断、Learning Console，以及受限本地进程 Sandbox。当前不把公网部署、分布式 Worker、多租户、Docker 强隔离和 Secret 生命周期作为本地稳定版的前置条件。

## 最短路径：启动本地 Runtime

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[api]"

agent-runtime init
agent-runtime status
agent-runtime serve
```

默认配置使用离线 `MockProvider`，服务只监听 `127.0.0.1:8000`。另开一个 PowerShell 验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
agent-runtime status
```

停止服务时在运行窗口按 `Ctrl+C`。完整配置、单实例锁、日志和恢复说明见 [本地稳定 Runtime 运行指南](./docs/LOCAL_RUNTIME.md)。

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
agent-runtime serve
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
# 离线算术 Demo
agent-runtime demo "19 * 23"

# 多 Agent 串行 Workflow
agent-runtime workflow demo "分析一个需求并给出结论"

# 状态、诊断与备份
agent-runtime status
agent-runtime doctor --json
agent-runtime observe diagnostics
agent-runtime backup create

# 本地稳定性自动验收
python scriptserify_local_runtime.py --runs 100 --concurrency 8
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
- [本地稳定 Runtime 运行指南](./docs/LOCAL_RUNTIME.md)
- [演进历史](./docs/CHANGELOG.md)
- [演进路线图](./docs/ROADMAP.md)
- [ADR 决策记录](./docs/adr/README.md)
- [文档中心](./docs/README.md)

## 开发与发布检查

```powershell
python scripts/check_docs.py
python -m ruff check src tests scripts
python -m mypy srcgent_runtime
python -m pytest --cov=agent_runtime --cov-branch --cov-report=json:coverage.json -p no:cacheprovider
python scripts/check_coverage.py coverage.json
python scripts/verify_local_runtime.py --runs 100 --concurrency 8
python -m build
python scripts/verify_distribution.py dist
```

核心实现位于 `src/agent_runtime/`，测试位于 `tests/`。版本、架构、功能状态和构建记录必须随代码同步更新。

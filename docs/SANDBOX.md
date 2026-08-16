# Sandbox、Tool Capability 与本地进程执行

- **适用版本**：v0.8.0+
- **最近更新**：2026-08-16
- **关联变更**：[E2026-08-16-004](./CHANGELOG.md#e2026-08-16-004)
- **关联决策**：[ADR-0025](./adr/0025-local-process-sandbox-capability-policy.md)

## 1. v0.8.0 解决什么问题

v0.8.0 不再把“工具已经注册”直接等同于“工具可以执行”。Runtime 会先读取 `ToolDefinition.capabilities`，再通过 `CapabilityPolicy` 合并以下决策：

- `allow`：允许执行。
- `deny`：拒绝该 capability。
- `require_approval`：执行前必须形成持久化 Approval。
- `sandbox_only`：handler 必须绑定受 Runtime 管理的 Sandbox。

默认规则是：

| Capability | 默认动作 |
| --- | --- |
| `file.read` | `allow` |
| `file.write` | `require_approval` |
| `process.exec` | `sandbox_only` |
| `network.access` | `deny` |
| `secret.read` | `deny` |

未声明 capability 的旧 Tool 保持兼容；一旦声明未知 capability，默认拒绝。

## 2. LocalProcessSandbox 的边界

`LocalProcessSandbox` 使用 `asyncio.create_subprocess_exec()`，命令必须按 `argv` 数组传入，不使用 `shell=True`，也不拼接 shell 字符串。执行前检查：

1. 可执行文件是否位于显式白名单。
2. `cwd` resolve 后是否仍位于 Workspace 内。
3. 环境变量是否位于白名单。
4. timeout、总输出字节和并发进程数是否在配置上限内。
5. Runtime 是否仍接受新执行，CancellationToken 是否已取消。

超时、输出超限、Run Cancel 或 Runtime shutdown 会终止进程树。Windows 使用 `taskkill /T /F`，POSIX 使用独立 process group。

> `LocalProcessSandbox` 是受限本地进程适配器，不是容器、虚拟机或操作系统安全边界。它当前不能提供可靠的网络隔离，也不能保证阻止已允许解释器内部的所有系统调用。

## 3. Python 使用示例

```python
import sys
from pathlib import Path

from agent_runtime import (
    LocalProcessSandbox,
    SandboxLimits,
    ToolRegistry,
    register_process_tool,
)

workspace = Path(r"D:\AICoding\Agent")
registry = ToolRegistry()
sandbox = LocalProcessSandbox(
    workspace,
    allowed_executables=[sys.executable],
    limits=SandboxLimits(
        timeout_seconds=10,
        max_output_bytes=256 * 1024,
        max_concurrent_processes=2,
    ),
)
process_tool = register_process_tool(registry, sandbox)
```

将 `process_tool` 放入 `AgentDefinition.tools` 后，模型可以提出：

```json
{
  "argv": ["C:\\Python313\\python.exe", "-c", "print('hello sandbox')"],
  "cwd": "."
}
```

该调用默认同时具备 `process.exec`、`file.read` 和 `file.write`，因此必须运行在 Sandbox 中并经过人工审批。

## 4. 查看策略与运行状态

CLI：

```powershell
agent-runtime observe sandbox
```

HTTP：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/observability/sandbox
```

Python：

```python
snapshot = runtime.sandbox_snapshot()
```

快照包含 capability policy、已注册 Tool 的授权结果，以及 Sandbox 的 active process、并发、超时、输出上限、可执行文件与环境变量白名单。快照不会写入 Run Event sequence。

## 5. 持久化事件

模型产生 ToolCall 后，Runtime 新增 durable Event：

```text
tool.policy.evaluated
```

它记录 Tool 名称、capability、是否需要审批和是否要求 Sandbox，但不改变原 ToolExecution 状态机。随后仍使用：

```text
approval.requested
approval.resolved
tool.requested
tool.started
tool.completed / tool.failed / tool.outcome_unknown
```

AgentDefinition 快照会保存 `capabilities` 和 `sandbox_only`，旧快照缺少字段时按空 capability 和非 sandbox-only 读取。

## 6. Learning Console

运行：

```powershell
agent-runtime lab
```

选择“受限进程沙箱”场景。流程会先展示 `tool.policy.evaluated`，然后停在 Approval。批准后，Runtime 通过真实 `LocalProcessSandbox` 启动白名单中的 Python，并在 Tool 泳道展示完成结果。SQLite Inspector 的 `SANDBOX & CAPABILITY` 区域会显示策略和当前进程状态。

## 7. 当前未完成

v0.8.0 明确没有实现：

- Docker 非 root、只读根文件系统、CPU/内存/PID 和默认禁网强隔离。
- SecretProvider、Secret 临时注入和输出值脱敏。
- 对任意不可信 Python、Node 或编译产物的安全执行承诺。
- 自动安装依赖、系统调用过滤、Windows Job Object 或 Linux seccomp。

这些能力将在后续 v0.8.x 独立设计和验收，不能把 `LocalProcessSandbox` 的“受限”描述成“不可信代码强隔离”。
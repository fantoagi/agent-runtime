# ADR-0025：本地进程 Sandbox 与 Tool Capability 采用显式允许模型

- **状态**：Accepted
- **日期**：2026-08-16
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-16-004](../CHANGELOG.md#e2026-08-16-004)

## 背景

v0.7.x 已经解决 Tool 线程隔离、超时、取消、UNKNOWN、审批和恢复，但 Runtime 仍主要依赖 `requires_approval` 与 Workspace 路径检查。进入代码型 Agent 后，进程启动、网络访问、Secret 读取和文件副作用不能只依赖模型遵守 Prompt，也不能把任意注册 handler 默认视为安全。

同时，Windows 是当前首要环境。直接把 Docker 设为唯一执行方式会提高安装门槛，并阻塞 Sandbox 协议、Capability 决策、取消语义和教学流程的验证。因此需要先建立可替换协议与保守的本地实现，但必须明确它不是强隔离。

## 决策

新增 `ToolCapability`、`CapabilityPolicyAction`、`CapabilityPolicy` 和 `ToolAuthorization`。Tool 可以声明文件读写、进程执行、网络和 Secret capability；策略合并遵循 deny 优先，其次 sandbox-only，再次 require-approval。未声明 capability 的旧 Tool 保持兼容，已声明但无规则的 capability 默认拒绝。

新增 `SandboxExecutor` 协议和 `LocalProcessSandbox`。本地实现只接受 argv，不使用 shell；使用可执行文件、Workspace cwd 和环境变量白名单；限制 timeout、总输出和并发；取消或关闭时终止进程树。`run_process` 是显式 sandboxed Tool，并同时声明 process.exec、file.read 和 file.write。

`LocalProcessSandbox` 的快照属于 transient operational state，不进入 Runtime Event sequence；Capability 决策会影响审批和执行，因此 `tool.policy.evaluated` 作为 durable Event 保存。AgentDefinition 快照保存 capability 与 sandbox-only 字段。

网络和 Secret 默认拒绝。v0.8.0 不声称本地进程具备网络强隔离，也不提供 Secret 注入。

## 影响

### 优点

- Tool 是否可执行由 Runtime 策略决定，而不是模型或前端决定。
- process.exec 必须绑定受管理 Sandbox，文件写入自动要求审批。
- 命令不经过 shell，减少字符串拼接与 shell injection 风险。
- timeout、输出、并发、cwd、环境和进程树取消形成一致合同。
- Sandbox 协议可在后续替换为 Docker 或更强实现。
- Windows 初学者无需先安装容器即可理解真实执行链路。

### 代价

- `LocalProcessSandbox` 无法阻止已允许解释器执行网络、系统调用或 Workspace 外的非路径型资源访问。
- 可执行文件白名单需要宿主显式配置。
- 新增 durable Event 会改变包含 ToolCall 的事件序列，但不改变已有状态和结果字段。
- 高风险 Tool 的 AgentDefinition 注册可能因默认 deny 失败，需要显式调整策略。

## 被放弃的方案

- 只依赖 Prompt 要求模型不要执行危险命令：不可审计，也无法抵御错误或恶意输入。
- 继续只使用 `requires_approval`：审批不能表达 sandbox-only、deny 和不同 capability 的组合。
- 使用 `subprocess(..., shell=True)`：跨平台引用和 injection 边界不可控。
- 在 v0.8.0 直接承诺 Docker 为强制依赖：会扩大版本范围，并使 Windows 本地学习和 CI 复杂度显著上升。
- 把 Sandbox 进程状态写入 Runtime Event sequence：瞬时采样会污染用于恢复的 durable sequence。

## 后续约束

- 新增代码、Shell、网络或 Secret Tool 必须声明 capability。
- `process.exec` Tool 必须通过 `sandboxed=True` 注册，否则策略拒绝。
- 任何宣称强隔离的实现都必须有独立攻击面测试和 ADR，不能复用 LocalProcessSandbox 的描述。
- Secret 值不得进入 Tool arguments、Event、Checkpoint、Trace、Eval、日志或 Incident Bundle。
- DockerSandbox、SecretProvider、网络策略或系统调用隔离必须作为后续 v0.8.x 独立 Change ID 验收。
- Capability 或 Event schema 出现不兼容变化时必须新增 ADR。
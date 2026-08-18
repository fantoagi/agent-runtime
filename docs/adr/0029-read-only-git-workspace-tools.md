# ADR-0029：Git 只读工作区检查通过受管 Sandbox

- **状态**：Accepted
- **日期**：2026-08-17
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-17-002](../CHANGELOG.md#e2026-08-17-002)

## 背景

本地编码 Agent 需要在修改前后理解 Git 工作区状态和 tracked diff。直接让模型使用通用 `run_process` 虽然可行，但每次都需要批准、参数容易漂移，也无法稳定限制 diff 大小和关闭 external diff。

## 决策

新增 `git_status` 和 `git_diff` 两个只读 Tool。它们只在 Git 已进入 LocalProcessSandbox 可执行白名单时注册，调用继续经过 ToolRegistry、CapabilityPolicy 和 SandboxExecutor，不经过 Shell。

`git_diff` 固定使用 `--no-ext-diff`、`--no-textconv` 和 `--no-color`，限制 context line 和返回字符数；路径必须位于 Workspace。两个 Tool 声明 `process.exec` 和 `file.read`，但不声明副作用，也不要求人工批准。

## 影响

### 优点

- 模型可以先检查工作区，再决定编辑范围。
- Git 检查不需要每次批准，减少交互噪音。
- diff 输出有界，不会无界占用模型上下文。
- 不新增第二套进程启动方式。

### 代价

- 只有允许列表包含 Git 时才可用。
- `git_diff` 不展示 untracked 文件内容。
- LocalProcessSandbox 仍不是不可信代码强隔离。

## 被放弃的方案

- 通过通用 `run_process` 执行任意 Git 参数。
- 在 Interactive CLI 中直接调用系统 Git，绕过 Runtime Tool 体系。
- 自动执行 commit、push、reset 或 checkout。

## 后续约束

Git 写操作继续使用需要 Approval 的 `run_process`，本 ADR 不授权自动提交、推送或破坏性工作区操作。

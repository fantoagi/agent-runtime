# ADR-0031：项目指令作为有界本地 Agent 上下文

- **状态**：Accepted
- **日期**：2026-08-17
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-17-004](../CHANGELOG.md#e2026-08-17-004)

## 背景

本地编码 Agent 已能读取、修改和验证代码，但每次会话仍需用户重复说明仓库约束。类似成熟 Coding Agent 的基础体验要求 Runtime 在启动时理解项目级规则，同时不能无界读取文件、泄露正文到状态接口或破坏既有 AgentDefinition 恢复语义。

## 决策

本地 Runtime 默认按配置顺序查找 Workspace 根目录中的 `AGENTS.md` 和 `CLAUDE.md`。只读取 UTF-8 普通文件，路径必须相对 Workspace，内容共享一个默认 50000 字符的总预算。加载结果记录相对路径、字符数、截断状态和 SHA-256；状态接口不返回正文。

配置的基础 Prompt、Runtime 内建编码执行协议和项目指令在 Runtime 启动时合成为最终 `AgentDefinition.system_prompt`。该最终 Prompt 继续进入既有 AgentDefinition 快照，因此历史 Run 的恢复不依赖当前文件内容。Interactive CLI Banner、`/workspace` 和 `status` 只展示来源与摘要。

## 影响

### 优点

- Agent 启动后自动理解仓库规则，减少重复提示。
- 内建协议要求先检查、再修改、再看 diff 和验证，降低无依据宣称成功的概率。
- 内容有界、来源可见、摘要可审计。
- 历史 Run 继续绑定启动时的精确 Prompt 快照。

### 代价

- 项目指令占用模型上下文。
- 多个指令文件可能存在冲突，当前按配置顺序共同装配，不做语义合并。
- Runtime 启动后修改指令文件不会影响已注册 Agent，需重启后生效。

## 被放弃的方案

- 每轮让模型自行搜索指令文件，导致行为不稳定且增加 Tool 往返。
- 无限制递归加载所有目录中的规则文件。
- 在状态接口中返回完整指令正文。

## 后续约束

未来若增加目录层级指令，必须定义明确的作用域、覆盖顺序、总上下文预算和快照语义；不得绕过 Workspace confinement，也不得把未受限文件内容隐式注入模型。

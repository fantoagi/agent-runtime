# ADR-0028：Coding Workspace Tools 使用结构化文件操作与 argv 进程执行

- **状态**：Accepted
- **日期**：2026-08-17
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-17-001](../CHANGELOG.md#e2026-08-17-001)

## 背景

v0.8.2 已提供可持久化的 Interactive CLI、Session 多轮对话、Tool Approval 和流式终端，但标准本地 Agent 只有计算器、单文件读取和完整文件写入。用户无法让模型高效发现代码、搜索定义、对已有文件做受约束修改并运行测试，因此还不能完成真实的本地编码闭环。

项目当前目标是以最短路径实现单机、单用户、可信 Workspace 内的稳定 Runtime，而不是立即建设容器平台、LSP、Git 自动化或完整自治编码系统。新增 Tool 必须复用已有 capability、Approval、UNKNOWN、Checkpoint 和持久化语义。

## 决策

### 1. Coding Tool 保持结构化协议

新增 `list_files`、`search_text` 和 `replace_text`，继续复用 `read_text_file`、`write_text_file` 与 `run_process`。所有输入都是 JSON schema 约束的结构化参数，不向模型开放任意 Shell 字符串。

### 2. 文件发现与搜索必须有界

`list_files` 返回排序后的 Workspace 相对路径，跳过运行时、版本控制、虚拟环境和常见生成目录，并限制返回数与扫描数。`search_text` 使用纯 Python 读取 UTF-8 文本，限制文件数、文件大小、匹配数和单行长度，跳过二进制与无效 UTF-8 文件。

这些限制用于控制模型上下文和本地资源消耗，不把目录扫描变成无限索引服务。

### 3. 已有文件修改使用精确替换

v0.8.3 不宣称支持完整 `apply_patch` 或 unified diff。`replace_text` 要求：

- 目标文件已经存在。
- `old_text` 非空。
- 实际匹配数等于 `expected_replacements`，默认 1。
- 数量不一致时不写文件。
- 成功后返回替换数、字符数和修改前后 SHA-256。

写入使用同目录临时文件、flush/fsync 和 `os.replace`。创建新文件或完整覆盖继续使用已有 `write_text_file`。

### 4. 文件写入继续要求人工批准

`replace_text` 声明 `file.write`、`requires_approval=true` 和 `side_effecting=true`。它与 `write_text_file` 使用同一 Approval 和 UNKNOWN 合同，不增加自动批准或 Session 级永久授权。

### 5. 标准本地 Runtime 默认接入受限进程 Tool

`create_configured_local_runtime()` 注册已有 `LocalProcessSandbox` 和 `run_process`。配置提供开关、可执行文件白名单、timeout、输出和并发上限。`run_process` 只接收 argv，不使用 `shell=True`，并继续要求人工批准。

旧 TOML 缺少新字段时使用默认值，保持向后兼容。无法解析白名单可执行文件时 fail fast，不静默放宽。

### 6. Interactive CLI 只增加观察入口

新增 `/workspace` 和 `/diff`。`/workspace` 展示 Workspace 与 Coding Tool 状态；`/diff` 从持久化 ToolExecution 读取当前 Session 最近的文件变更摘要。它不是 Git diff，也不把 UI 状态写入 Runtime Kernel。

## 影响

### 优点

- 标准本地 Agent 可以完成发现、搜索、读取、修改、执行验证和最终汇报闭环。
- 精确替换比无条件整文件覆盖更容易发现过时上下文和意外多匹配。
- 所有副作用继续经过 Approval、ToolExecution 和 Event Log。
- 纯 Python 搜索和结构化 argv 降低外部依赖与 Shell 注入风险。
- 旧配置可继续使用，新能力可通过开关关闭。

### 代价

- 纯 Python 搜索性能低于专用索引或 ripgrep。
- `replace_text` 不能表达复杂 diff、文件移动或多文件原子事务。
- `run_process` 允许的解释器仍可能执行广泛本地操作，LocalProcessSandbox 不是强安全边界。
- 每次文件写入和进程执行都要求批准，长任务会产生更多交互。

## 被放弃的方案

### 方案 A：直接开放 Shell Tool

Shell 字符串会引入引号、管道、重定向、平台差异和注入边界，且难以做精确白名单，因此不采用。

### 方案 B：立即实现完整 unified diff parser

会显著扩大解析、换行、路径、冲突和多文件事务的测试面，不符合 v0.8.3 最短可用闭环，因此先采用精确替换。

### 方案 C：依赖 ripgrep 和 Git

专用工具性能更高，但不能保证所有目标环境已安装。文件搜索先用标准库；Git 只作为可选白名单进程，不作为 Runtime 正确性的依赖。

### 方案 D：默认自动批准 Workspace 写入和测试命令

这会弱化当前清晰的副作用合同，并使模型误操作更难被用户观察，因此不采用。

## 后续约束

- 新增文件修改协议时必须明确冲突检测、原子性、Approval 和 UNKNOWN 语义。
- 不得把 `run_process` 描述成容器级安全隔离。
- 不得绕过 Tool Registry 直接从 Interactive CLI 修改文件或启动进程。
- 修改默认忽略目录、扫描上限、进程白名单默认值或 `/diff` 数据来源时必须同步更新文档和测试。
- 如果未来加入 unified diff、多文件事务、Git 自动操作或自动授权，必须新增 ADR。
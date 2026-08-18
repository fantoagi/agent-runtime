# ADR-0032：Artifact 分页读取与 Workspace 继续发现策略

- **状态**：Accepted
- **日期**：2026-08-18
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-18-001](../CHANGELOG.md#e2026-08-18-001)

## 背景

真实本地编码流程暴露了两个上下文退化问题。第一，大 Tool Result 被 Runtime 转存为 Artifact 后，模型若继续使用 `read_text_file` 读取完整 Artifact，结果会再次超过 inline 阈值并生成第二个 Artifact，最终可能退化为请求 `run_process` 执行 Python 打印文件。第二，根目录列表可能被 Runtime 测试数据和 coverage 产物占满；列表截断后模型可能未发现已知模块便过早结束。

## 决策

标准本地 Runtime 注册专用只读 `read_artifact` Tool。它只允许读取当前 Run 的 `<artifact_root>/<run_id>/tool-results/`，接受绝对或受控相对引用，按 Unicode 字符 offset 返回最多 4000 字符的页面以及 `next_offset`、`total_chars`、`has_more` 和 SHA-256。Runtime 明确禁止 `read_artifact` 页面再次 Artifact 化；`read_text_file` 遇到同 Run Tool Result Artifact 时拒绝并给出迁移提示。

Workspace discovery 默认忽略 `.runtime-test-data`、`.coverage` 和 `coverage.json`。Tool description 与内建 Coding Protocol 同时规定：目标可从请求或 Session 推断时继续执行；广泛 listing/search 截断时缩小 path/pattern 或搜索符号，不因一次截断就结束；不得使用通用进程仅为打印 Workspace 文件或 Runtime Artifact。

## 影响

### 优点

- 大 Tool Result 可以稳定分页进入模型上下文，不形成递归 Artifact 链。
- 读取 Artifact 不再需要副作用进程审批。
- 当前 Run 边界阻止模型读取其他 Run 的 Tool Result。
- Workspace 根目录发现更聚焦，模型更可能完成“发现 → 读取 → 修改 → 验证”。

### 代价

- Artifact 页面读取为计算字符总数和 SHA-256 需要顺序扫描文件，尚未建立随机访问索引。
- Prompt 和 Tool description 只能引导模型，不能保证所有模型都继续调用 Tool。
- `read_file_lines` 最大上下文窗口收紧为 3500 字符，需要通过 `next_start_line` 多次续读。

## 被放弃的方案

- 允许 `read_text_file` 直接读取完整 Artifact：会再次触发大结果处理并污染上下文。
- 允许 `run_process`/Python 打印 Artifact：引入无必要 Approval 和更宽执行面。
- Runtime 在模型无 Tool Call 时强制继续：会破坏模型最终回答和 Run completed 的既有语义。
- 允许按任意绝对路径读取 Artifact：会跨越 Run 和 Artifact 类型边界。

## 后续约束

未来若增加 Artifact ID、索引或生命周期管理，必须保持当前 Run confinement、分页有界和非递归语义。任何允许跨 Run 读取的能力都需要新的授权模型和 ADR。Workspace 忽略规则应只覆盖明确的生成物，不得隐藏正常源码目录。

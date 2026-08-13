<a id="change-id"></a>
## EYYYY-MM-DD-NNN：变更标题

- **完成时间**：pending
- **状态**：🚧 partial
- **类型**：feature | fix | architecture | security | governance | milestone
- **影响范围**：
  - `path/to/file`
- **关联 commit**：`pending`
- **关联 ADR**：不需要；或填写 `[ADR-NNNN](./adr/NNNN-title.md)`

### 变更摘要

说明为什么需要本次变更，以及用户或系统获得了什么能力。

### 系统架构

说明模块边界、数据流、状态机、存储、安全或部署是否变化；如果无变化，明确写“无架构变化”。

### 实现方式

说明主要实现策略和关键技术约束，不复制代码。

### 当前功能

列出完成后可以被调用或验收的能力。

### 已知限制

列出本次变更仍未解决的问题；没有则写“暂无新增限制”。

### 测试与验收

```powershell
python scripts/check_docs.py
pytest
```

### 后续计划

写明下一步，或写“暂无”。

# ADR-0010：Parent/Child Run 与持久化多 Agent 委派模型

- **状态**：Accepted
- **日期**：2026-08-14
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-14-007](../CHANGELOG.md#e2026-08-14-007)

## 背景

v0.5.3 已能可靠执行和观测单个 Agent Run，但多 Agent 编排不能只在内存中调用多个 Python 函数。若 Child Agent 没有独立 Run、Event、Checkpoint 和稳定关系，系统将无法回答“任务委派给了谁”“恢复时是否重复创建”“Parent 取消影响了哪些 Child”“整棵执行树为何失败”等问题。

## 决策

1. 每个被委派 Agent 都创建独立 `AgentRun`，继续使用既有 Runtime `_execute()`、状态机、Event、Checkpoint、ToolExecution 和 Approval 语义。
2. 使用 SQLite `run_relations` 保存 `parent_run_id`、`child_run_id`、`root_run_id`、`relation_type`、`delegation_key` 和 metadata。
3. `child_run_id` 全局唯一；`parent_run_id + delegation_key` 唯一，作为委派幂等边界。
4. 首次委派必须在一个事务中写入 Child Run、RunRelation、Parent `delegation.created` 和 Child `run.created`。
5. Child 拥有独立 `trace_id`；整棵树通过 Root Run 的 `root_trace_id` 和 `root_run_id` 关联。
6. `SequentialWorkflow` 使用稳定 step key 顺序传递结果；`ParallelWorkflow` 使用稳定 branch key，并提供 `all`、`best_effort`、`first_success` 汇聚策略。
7. Parent Cancel 递归传播到活动 Child；已经进入终态的 Child 不被改写。
8. Workflow Parent 本身也是持久化 Run，拥有 Event 和 Checkpoint，但不伪装成模型 Agent；其 `agent_name` 使用 `workflow:<name>`。
9. Trace Tree、Multi-Agent Metrics 和 Workflow Eval 从持久化 RunRelation 与既有执行事实派生，不建立第二套追踪数据库。
10. Agent 定义继续保存在进程内 `AgentRegistry`；v0.6 不持久化可执行代码或动态 Agent 定义。

## 影响

### 优点

- Parent 和 Child 可以独立查询、恢复、审计和评估。
- 稳定 delegation key 防止 Parent 恢复时重复创建 Child。
- 多 Agent 复用已经验证的单 Agent 内核，不引入第二套执行器。
- RunRelation 能支持 Trace Tree、取消传播以及后续分布式调度。
- 顺序与并行 Workflow 具有明确的失败和结果汇聚语义。

### 代价

- 一次 Workflow 会产生多个 Run、Event 和 Checkpoint，存储与观测数据量增加。
- Workflow 定义仍需由调用方重新提供，Runtime 不能仅凭数据库自动恢复任意 Python Workflow 类。
- SQLite 和单进程 Task 管理限制了横向扩展。
- `first_success` 会取消其他分支；若分支内部运行副作用 Tool，仍需遵守未知结果处置规则。

## 被放弃的方案

- 不把 Child 作为 Parent 内的一种 Step，因为这会丢失独立生命周期、审批、Trace 和恢复边界。
- 不只把父子关系放入 Run metadata，因为无法建立唯一约束、事务写入和高效关系查询。
- 不使用内存 Future 列表作为唯一编排事实，因为进程退出后无法追溯或幂等恢复。
- 不在 v0.6 引入消息队列、远程 Worker 或 Workflow DSL，避免在单机语义稳定前扩大故障面。

## 后续约束

任何修改 `RunRelation` schema、delegation key 唯一性、取消传播、Workflow 汇聚或 Parent/Child Trace 语义的变更都必须新增或更新 ADR。进入分布式 Worker 阶段时，Child Run 的执行位置可以变化，但 Run ID、RunRelation、Event、Checkpoint 和幂等委派契约必须保持兼容。

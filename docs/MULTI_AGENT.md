# v0.6 多 Agent 编排使用与学习指南

本指南面向第一次接触多 Agent Runtime 的开发者，解释 v0.6 如何将一个任务拆成多个独立、可持久化、可追踪的 Child Run。

## 1. 一条命令体验

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[api]
agent-runtime workflow demo "为 Agent Runtime 设计一个可靠的恢复机制"
```

该命令执行：

```text
Planner Run → Worker Run → Reviewer Run
```

输出包含一个 Workflow Parent Run、三个具有独立 Run ID 和 Trace ID 的 Child Run、持久化 `RunRelation`、每个 Run 自己的 Event / Checkpoint，以及完整 `TraceTree`。

## 2. 核心对象

### AgentRegistry

`AgentRegistry` 是可委派 Agent 的目录。Runtime 只允许把任务委派给已经注册并通过 ToolDefinition 校验的 Agent。

```python
runtime.register_agent(planner)
runtime.register_agent(worker)
runtime.register_agent(reviewer)

for agent in runtime.list_agents():
    print(agent.name)
```

同名、同定义的重复注册是幂等的；同名但定义不同会被拒绝，避免恢复时 Agent 行为静默变化。

### RunRelation

每次首次委派都会写入一条 SQLite `run_relations` 记录：

```text
parent_run_id
child_run_id
root_run_id
relation_type
delegation_key
metadata
created_at
```

`delegation_key` 在同一个 Parent Run 下唯一，是恢复时防止重复创建 Child Run 的关键。

### Runtime.delegate()

```python
child = await runtime.delegate(
    parent_run_id,
    "worker",
    "实现这个任务",
    delegation_key="implementation-step",
)
```

处理顺序：

1. 校验 Parent Run 和 AgentRegistry。
2. 用 `parent_run_id + delegation_key` 查询已有关系。
3. 已存在时复用原 Child Run，不重复创建。
4. 不存在时原子写入 Child Run、RunRelation、Parent Event 和 Child `run.created`。
5. 通过普通 Runtime `_execute()` 路径执行 Child Run。
6. 将 Child 终态作为 `delegation.completed|failed|cancelled` 写回 Parent Event Log。

## 3. 顺序 Workflow

```python
import asyncio
from agent_runtime import create_multi_agent_demo_runtime, multi_agent_demo_workflow

async def main():
    runtime = create_multi_agent_demo_runtime(".")
    workflow = multi_agent_demo_workflow()
    execution = await workflow.run(runtime, "设计一个任务恢复方案")

    print(execution.parent.result)
    for child in execution.children:
        print(child.agent_name, child.id, child.result)

asyncio.run(main())
```

`SequentialWorkflow` 将上一个 Child 的结果作为下一个 Child 的输入：

```text
原始输入 → Planner.result → Worker.result → Reviewer.result → Parent.result
```

每一步使用稳定键：

```text
<workflow-name>:step:0
<workflow-name>:step:1
<workflow-name>:step:2
```

重新运行同一个未完成 Parent 时，已完成步骤会被复用：`await workflow.run(runtime, input_text, parent_run_id=parent.id)`。Workflow 不支持通用 `Runtime.pause()`，进程重启后也不能只调用 `Runtime.resume(parent.id)`，因为 Runtime 必须重新获得原 Workflow 定义。

## 4. 并行 Workflow

```python
from agent_runtime import AggregationStrategy, ParallelWorkflow

workflow = ParallelWorkflow(
    "parallel-research",
    ["researcher-a", "researcher-b", "researcher-c"],
    max_concurrency=2,
    timeout_seconds=30,
    aggregation=AggregationStrategy.BEST_EFFORT,
)
execution = await workflow.run(runtime, "分析这个问题")
```

当前汇聚策略：

| 策略 | 行为 |
| --- | --- |
| `all` | 所有分支必须完成，否则 Parent 失败 |
| `best_effort` | 至少一个分支完成即可汇聚成功结果 |
| `first_success` | 第一个成功分支成为 Parent 结果，其余活动分支被取消 |

`max_concurrency` 通过 `asyncio.Semaphore` 限制同一 Workflow 同时运行的 Child 数量。`timeout_seconds` 到期后会取消活动 Child，并将 Parent 收敛为 failed。

## 5. 取消传播

```python
parent = workflow.start(runtime, "一个耗时任务")
runtime.cancel(parent.id)
```

Parent Cancel 会递归取消活动 Child Run；已经进入终态的 Child 不会被改写。取消仍使用既有 `CancellationToken` 和 Task cancellation 语义，副作用 Tool 的未知结果规则不变。

## 6. Trace Tree 和 Metrics

```python
from agent_runtime import ObservabilityService

observability = ObservabilityService(runtime.store)
tree = observability.trace_tree(parent_run_id)
print(tree.to_dict())
```

也可以从任意 Child Run 查询，服务会先定位 `root_run_id`：

```powershell
agent-runtime observe trace-tree <parent-or-child-run-id>
```

FastAPI：

```text
GET /agents
GET /runs/{run_id}/relations
GET /runs/{run_id}/trace/tree
POST /runs/{parent_run_id}/delegations
```

Metrics 新增 `root_runs`、`child_runs`、`workflow_runs` 和 `delegations`。Prometheus 对应 `agent_runtime_root_runs_total`、`agent_runtime_child_runs_total`、`agent_runtime_workflow_runs_total` 和 `agent_runtime_delegations_total`。

## 7. Workflow Eval

```python
from agent_runtime import EvalCase, EvalSuite, WorkflowEvalRunner

suite = EvalSuite(
    name="workflow-smoke",
    cases=[
        EvalCase(
            name="three-agents",
            input="设计恢复方案",
            expected_contains=["REVIEWED"],
            expected_child_count=3,
        )
    ],
)
report = await WorkflowEvalRunner(runtime).run(suite, workflow)
```

报告保留 Parent Run ID、Trace ID、输出、状态、Child 数量断言和 Artifact 路径。

## 8. SQLite 检查

```powershell
python -c "import sqlite3; c=sqlite3.connect('.agent-runtime/runtime.sqlite3'); print(c.execute('SELECT parent_run_id, child_run_id, root_run_id, relation_type, delegation_key FROM run_relations ORDER BY created_at').fetchall())"
```

v0.6 schema version 为 `3`。

## 9. 当前限制

- Child Run 仍在同一 Python 进程和同一个 SQLite Store 中执行。
- Workflow 定义保存在应用代码中，尚未作为可部署 DSL 持久化。
- 没有跨机器 Queue、Worker、Lease 和 Heartbeat。
- 没有无限递归和 Agent 动态创建 Agent。
- Learning Console Snapshot 已包含 `trace_tree`，但尚未提供专用的可折叠跨 Run 树形画布。
- Parent/Child 共享 Root Trace 身份，但每个 Run 有自己的 Trace ID；当前没有接入 OpenTelemetry Collector。

## v0.7.7 Workflow 崩溃恢复

Sequential/Parallel Workflow 创建时保存规范化 snapshot。进程重启后，`await runtime.resume(parent_run_id)` 会从 snapshot 重建 Workflow，并通过稳定 delegation key 复用已经创建或完成的 Child Run。应用仍需重新注册 snapshot 引用的 AgentDefinition。
## v0.7.9 AgentDefinition 确切快照恢复

Workflow snapshot 不再只保存 Agent 名称。每个 Step 同时保存 `agent_definition_checksum`，该 checksum 指向 SQLite 中不可变的 AgentDefinition JSON。恢复进程只需重新提供 Tool Handler 与 Model Provider，不需要重新构造或注册 AgentDefinition；即使同名 Agent 已升级，历史 Workflow 仍使用创建时的 Prompt、ModelConfig 和执行限制。

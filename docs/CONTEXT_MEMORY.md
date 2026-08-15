# Context、Session 与长期记忆

> 当前版本：v0.7.1（Context/Memory 内核能力来自 v0.7，Learning Console 教学投影增强于 v0.7.1）
> 最近更新：2026-08-15  
> 关联记录：[E2026-08-15-001](./CHANGELOG.md#e2026-08-15-001)  
> 关联决策：[ADR-0011](./adr/0011-context-session-memory.md)

## 1. 一条命令体验

```powershell
cd D:\AICoding\Agent
.\.venv\Scripts\Activate.ps1
agent-runtime memory demo "Which Python language do I prefer?" --remember "The user prefers Python for examples."
```

命令会依次：

1. 创建一个持久化 `Session`。
2. 写入一条 `session` scope Memory。
3. 创建关联到 Session 的 Run。
4. 使用 SQLite FTS5 检索相关 Memory。
5. 将命中的 Memory 注入模型 Context。
6. 输出 `memory.search.*`、`context.built` 和最终结果。

## 2. ContextBuilder

`ContextBuilder` 在每次模型调用前构造受预算约束的输入副本，完整 Checkpoint 历史不会因为模型窗口裁剪而被原地修改。

```python
builder = ContextBuilder(
    token_budget=4096,
    recent_groups=4,
    summary_max_chars=1000,
    memory_token_budget=512,
)
```

当前使用确定性的近似 token 估算：约四个字符计为一个 token，并增加每条消息的固定开销。它不替代具体模型 tokenizer，但可以为不同 Provider 提供一致、可测试的裁剪行为。

### 保留优先级

1. 所有 System Prompt。
2. 尚未得到完整 Tool Result 的 Assistant Tool Call 组。
3. 最近的消息组。
4. 预算允许时从近到远补充旧消息。
5. 被省略的旧消息生成确定性 Summary。
6. Scoped Memory 作为独立 system message 注入。

Assistant Tool Call 和其 Tool Result 被视为一个不可拆分消息组，避免模型收到孤立 Tool Result 或缺失 Tool Result 的调用。

### Context 事件

每次模型请求产生：

```text
context.built
```

发生裁剪时额外产生：

```text
context.compacted
```

事件记录预算、估算 token、原始 token、选择数量、省略数量、Summary、Memory ID 和是否仍超出预算。

## 3. 大 Tool Result Artifact 化

当工具文本结果超过 `RuntimeConfig.large_tool_result_chars` 时：

1. 完整内容写入 Artifact Store。
2. ToolExecution 和 Checkpoint 只保留 Artifact 引用与预览。
3. Runtime 写入 `tool.result.artifactized` 事件。
4. 后续模型看到的是可追溯的引用，不是全部大文本。

默认值：

```text
large_tool_result_chars = 4000
large_tool_result_preview_chars = 400
```

Artifact 默认位于：

```text
.agent-runtime/artifacts/<run-id>/tool-results/<tool-execution-id>.txt
```

## 4. Session

Session 是多个 Run 的持久化容器：

```python
session = runtime.create_session({"user_id": "alice"})
run = await runtime.run(
    "assistant",
    "继续上次任务",
    session_id=session.id,
)
```

SQLite 使用 `sessions` 和 `session_runs` 保存关系。一个 Run 当前最多属于一个 Session；Parent/Child Run 会继承 Parent 的 `session_id`。

查询：

```python
runs = runtime.session_runs(session.id)
```

## 5. Memory Scope

v0.7 只支持受控的两种 Scope：

| Scope | scope_id | 可见范围 |
| --- | --- | --- |
| `session` | Session ID | 只对该 Session 内 Run 可见 |
| `agent` | Agent Name | 只对该 Agent 可见，可跨 Session |

没有 global scope，避免不同用户或不相关 Agent 意外共享记忆。

写入 Session Memory：

```python
memory = runtime.remember(
    "用户偏好 Python 示例。",
    scope="session",
    scope_id=session.id,
    source_run_id=run.id,
)
```

写入 Agent Memory：

```python
runtime.remember(
    "恢复前必须解释 Checkpoint。",
    scope="agent",
    scope_id="teacher-agent",
)
```

## 6. Memory 生命周期

Memory Record 保存：

```text
id
scope / scope_id
content
source_run_id
source_trace_id
created_at
expires_at
deleted_at
metadata
```

支持 TTL：

```python
runtime.remember(
    "临时部署窗口到今晚结束。",
    scope="session",
    scope_id=session.id,
    ttl_seconds=3600,
)
```

软删除：

```python
runtime.forget_memory(memory.id)
```

清理过期 FTS 索引：

```python
runtime.purge_expired_memories()
```

删除或过期 Memory 不会再出现在检索结果中。

## 7. SQLite FTS5 检索

```python
results = runtime.search_memory(
    "Python examples",
    session_id=session.id,
    agent_name="teacher-agent",
    limit=5,
)
```

查询只会在显式传入的 Scope 中执行。当前是关键词检索，不包含 Embedding 或向量相似度；中文复杂语义检索和同义词召回能力有限。

自动模型调用会根据 Run 的 Session 和 Agent Name 构造允许的 Scope，只有存在活动 Memory 时才执行检索。

## 8. Trace、Metrics 与 Eval

检索事件：

```text
memory.search.started
memory.search.completed
```

Metrics 新增：

```text
sessions
memories_total
memories_active
memories_deleted
memories_expired
memory_searches
context_compactions
```

`MemoryEvalRunner` 使用现有 Eval Report 格式验证关键词检索结果和数量：

```python
suite = EvalSuite(
    name="memory-retrieval",
    cases=[
        EvalCase(
            name="recovery",
            input="SQLite recovery",
            expected_contains=["checkpoints support recovery"],
            expected_memory_count=1,
        )
    ],
)
report = await MemoryEvalRunner(runtime).run(
    suite,
    session_id=session.id,
)
```

## 9. HTTP API

```text
POST   /sessions
GET    /sessions
GET    /sessions/{session_id}
GET    /sessions/{session_id}/runs
POST   /memories
GET    /memories/search
DELETE /memories/{memory_id}
POST   /memories/purge-expired
```

`POST /runs` 新增可选字段：

```json
{
  "agent_name": "demo",
  "input": "继续上次任务",
  "session_id": "session_xxx"
}
```

## 10. 当前限制

- token 估算是 Provider-neutral 近似值，不是模型厂商精确 tokenizer。
- Context Summary 是确定性文本摘要，不是模型生成的语义摘要。
- 当前只提供 SQLite FTS5，不提供向量检索。
- 不自动永久保存全部对话；Memory 必须显式创建。
- 不提供 global Memory Scope。
- Learning Console 暂无专用 Session/Memory 管理画布，可通过 CLI、API 和 Event Log 学习。

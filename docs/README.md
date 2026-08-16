# Agent Runtime 文档中心

这里将“当前事实”“历史演进”“关键决策”和“使用手册”分开维护，避免当前文档被历史记录淹没。

## 推荐阅读顺序

1. [LOCAL_RUNTIME.md](./LOCAL_RUNTIME.md)：从初始化到启动、状态、日志、停止和验收，适合第一次运行项目。
2. [CURRENT.md](./CURRENT.md)：当前版本已经实现什么、哪些能力仍有限制。
3. [ARCHITECTURE.md](./ARCHITECTURE.md)：Runtime Kernel、Provider、Tool、SQLite、Workflow、API 和本地启动层如何协作。
4. [LEARNING.md](./LEARNING.md)：通过 Learning Console 直观看懂真实事件和处理流程。
5. [CHANGELOG.md](./CHANGELOG.md)：按完成时间倒序查看每次可验收演进。
6. [ROADMAP.md](./ROADMAP.md)：查看当前方向、候选方向和明确暂缓事项。
7. [adr/README.md](./adr/README.md)：查看影响未来兼容性、可靠性或安全边界的架构决策。

## 专题手册

- [MULTI_AGENT.md](./MULTI_AGENT.md)：Parent/Child Run、串行与并行 Workflow。
- [CONTEXT_MEMORY.md](./CONTEXT_MEMORY.md)：Context、Session、Memory 和 Artifact。
- [OPERATIONS.md](./OPERATIONS.md)：备份、校验、恢复和运行演练。
- [OBSERVABILITY.md](./OBSERVABILITY.md)：日志、Trace、Metrics 和诊断。
- [INCIDENTS.md](./INCIDENTS.md)：脱敏故障诊断包和根因摘要。
- [SANDBOX.md](./SANDBOX.md)：Tool Capability、LocalProcessSandbox 和安全边界。

## 文档职责

| 文件 | 回答的问题 | 何时更新 |
| --- | --- | --- |
| `CURRENT.md` | 当前 Runtime 能做什么？ | 功能状态、测试状态或限制变化时 |
| `ARCHITECTURE.md` | 当前系统如何实现？ | 模块关系、协议、状态机或数据流变化时 |
| `LOCAL_RUNTIME.md` | 本地服务如何安装、启动和运行？ | 配置、CLI、日志、锁或运维流程变化时 |
| `CHANGELOG.md` | 什么时候完成了什么？ | 每个可独立验收的功能、修复或架构变更完成时 |
| `ROADMAP.md` | 接下来可能做什么？ | 优先级、里程碑或延期决策变化时 |
| `adr/*.md` | 为什么采用这个关键设计？ | 公共 API、schema、恢复、安全或兼容性决策变化时 |
| `LEARNING.md` | 如何通过可视化理解处理流程？ | Learning Console 场景或交互变化时 |

## 演进约定

- 文档语言以中文为主，代码标识和协议名称保留英文。
- 时间使用 `Asia/Shanghai`，格式为 `YYYY-MM-DD`。
- `CHANGELOG.md` 按完成时间和当天序号严格倒序。
- Change ID 是稳定引用，Git commit 是实现来源补充。
- 核心代码变化必须同步更新演进记录；重要决策必须新增或更新 ADR。

## 开发完成检查

```powershell
python scripts/check_docs.py
python -m ruff check src tests scripts
python -m mypy srcgent_runtime
python -m pytest
```

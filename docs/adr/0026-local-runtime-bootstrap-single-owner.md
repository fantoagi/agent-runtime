# ADR-0026：本地 Runtime 采用配置驱动启动与单执行 Owner

- **状态**：Accepted
- **日期**：2026-08-16
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-16-005](../CHANGELOG.md#e2026-08-16-005)

## 背景

Runtime 已具备持久化、恢复、FastAPI、备份、诊断和本地进程执行，但长期使用仍需要调用方自行拼装 Runtime，并可能无意中启动两个执行进程连接同一 SQLite。当前目标是单机、单用户、本地可信环境，不需要立即引入分布式 Lease、认证、SecretProvider 或 Docker 强隔离。

## 决策

新增 `agent-runtime.toml`、`agent-runtime init`、`agent-runtime serve` 和 `agent-runtime status`，以配置驱动方式构造唯一支持的本地服务入口。

每个状态目录通过 `runtime.lock` 声明一个执行 Owner。服务在整个生命周期内持有操作系统级非阻塞排他文件锁，并在文件中记录 PID、主机、版本、启动时间和随机 token。活动 Owner 存在时拒绝第二个服务；Owner 被强杀后由操作系统自动释放锁，下一次启动覆盖遗留元数据。锁只负责本地进程准入，不替代分布式 Lease，也不改变 SQLite 的多连接能力。

本地服务只允许绑定 loopback 地址。日志使用现有结构化格式并增加有界文件轮转。API 模块的默认 `app` 改为惰性构造，避免仅 import 模块便创建 SQLite 和 Runtime。

## 影响

### 优点

- 新用户可以通过 init/serve 两个命令运行 Runtime。
- 配置、状态目录、模型、容量和日志边界统一。
- 防止同一状态目录被两个本地执行循环同时领取。
- 强杀后可根据 PID 回收遗留锁，并继续使用现有恢复协调。
- import HTTP Adapter 不再产生隐藏 Runtime 和 SQLite 副作用。
- 不需要提前引入分布式系统复杂度。

### 代价

- `runtime.lock` 只适用于单机进程所有权，不处理跨主机共享文件系统。
- 本地服务明确拒绝 `0.0.0.0` 等远程监听地址。
- Python SDK 仍可自行创建多个 Store；单 Owner 约束只适用于标准 `serve` 入口。
- API Key 继续由本机环境变量提供，本版本不构建 Secret 生命周期系统。

## 被放弃的方案

### 立即实现 Worker Lease

当前没有多进程或多主机调度需求，Lease 会引入 Queue、Heartbeat、接管和数据库一致性复杂度。

### 使用普通“文件存在”锁

仅检查文件是否存在无法区分活动进程和强杀遗留文件，因此必须持有操作系统排他锁；PID、主机和 token 作为状态展示和诊断元数据。

### import 时创建默认 FastAPI Runtime

会在只需要复用 `create_app` 时产生隐藏 SQLite 连接和状态目录，因此改为首次 ASGI 调用时惰性创建。

## 后续约束

- 标准本地服务必须先获得 Owner Lock，再构造 Runtime。
- shutdown 必须在释放 Owner Lock 前关闭 Runtime 资源。
- 本地配置不得保存 API Key 明文。
- 默认 HTTP Host 必须是 loopback。
- 如果未来支持远程访问或多 Worker，必须新增 ADR，不能扩展 `runtime.lock` 冒充分布式 Lease。

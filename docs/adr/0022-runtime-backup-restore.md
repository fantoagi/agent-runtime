# ADR-0022：Runtime 状态归档与离线恢复

- **状态**：Accepted
- **日期**：2026-08-16
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-16-001](../CHANGELOG.md#e2026-08-16-001)

## 背景

Runtime 已具备 SQLite durability、崩溃协调和确定性恢复，但这些能力只能处理进程故障，不能替代数据库损坏、误操作、磁盘故障或错误升级时的独立恢复点。直接复制 WAL 模式下的主数据库文件可能产生不完整备份；只备份 SQLite 又会遗漏 Artifact Store。

## 决策

新增 `RuntimeBackupManager`，使用 SQLite Online Backup API 生成一致性数据库快照，并将数据库、Artifact 文件和 Manifest 封装为单一 `.agent-backup` 归档。

Manifest 保存 format version、schema version、migration checksum、关键表记录数、数据库 SHA-256，以及每个 Artifact 的路径、大小和 SHA-256。归档创建完成后必须自行校验，通过后才能原子替换目标备份文件。

恢复必须离线执行：先完整校验归档，再确认目标数据库可 checkpoint 并获取排他锁；当前状态先重命名为回滚副本，随后安装数据库和 Artifact，失败则自动回滚。默认保留恢复前状态，只有调用方明确要求时才删除。

由于现有 ToolExecution 和 Event 保存绝对 Artifact 路径，v0.7.10 只允许恢复到原数据库和 Artifact 路径，不自动进行字符串级迁移。

## 影响

### 优点

- WAL 运行期间可以获得事务一致的 SQLite 快照。
- 数据库和 Artifact 作为同一可校验恢复单元。
- 损坏、截断、重复 Entry、migration checksum 变化和 Artifact 丢失会在恢复前被拒绝。
- 恢复前状态默认保留，降低误恢复造成的二次损失。
- PR 和 Nightly 可以持续执行真实备份恢复演练。

### 代价

- 在线备份期间仍会产生磁盘和 I/O 开销。
- Artifact 在数据库快照后复制，归档可能包含未被该快照引用的额外文件。
- 当前不能直接恢复到新机器或新目录。
- 归档没有内建加密和远程上传能力。

## 被放弃的方案

- 直接复制 `runtime.sqlite3`：WAL 模式下不能保证包含已提交数据。
- 只执行 `VACUUM INTO`：无法同时覆盖 Artifact，且不适合作为完整运维合同。
- 自动恢复并覆盖运行中的数据库：容易造成进程继续持有旧连接和状态分叉。
- 恢复时全库字符串替换绝对路径：可能误改用户内容或非 Artifact 数据。

## 后续约束

- Backup format 变化必须提升 `format_version` 并保持旧格式显式拒绝或兼容读取。
- 恢复前始终先校验，不能提供跳过 checksum 或 SQLite 检查的开关。
- 未来支持路径迁移前，必须先将 Artifact 引用改为结构化相对标识，并新增 ADR。
- 未来加入加密、远程存储、自动调度或保留策略时，应保持本地归档和恢复合同可独立使用。

# Agent Runtime 运行与灾难恢复手册

- **适用版本**：v0.7.11
- **最近更新**：2026-08-16（Asia/Shanghai）
- **适用范围**：单机、单用户、SQLite、默认 Artifact Store

本文说明如何备份、校验和恢复 Agent Runtime 的持久化状态。恢复属于破坏性运维操作，必须先停止所有连接目标数据库的 Runtime、API 和 Learning Console 进程。

## 1. 状态目录

默认状态目录为：

```text
<workspace>/.agent-runtime/
├── runtime.sqlite3
├── runtime.sqlite3-wal
├── runtime.sqlite3-shm
├── artifacts/
└── backups/
```

- `runtime.sqlite3`：Run、Event、Checkpoint、ToolExecution、Approval、Workflow、Session、Memory 和 AgentDefinition 快照。
- `artifacts/`：大 Tool Result 和其他文件型产物。
- `*-wal` / `*-shm`：SQLite WAL 运行时文件，不能单独复制作为可靠备份。

## 2. 创建在线备份

Runtime 可以继续运行时创建备份：

```powershell
cd D:\AICoding\Agent
agent-runtime backup create
```

默认输出：

```text
<state-dir>/backups/runtime-YYYYMMDDTHHMMSSZ.agent-backup
```

指定输出位置：

```powershell
agent-runtime backup create --output D:\Backups\agent-runtime.agent-backup
```

归档包含：

- SQLite Online Backup API 生成的一致性数据库快照。
- `artifacts/` 文件副本。
- `manifest.json`。
- 数据库和每个 Artifact 的 SHA-256。
- schema version、migration checksum 和关键表记录数。

数据库快照先完成，Artifact 随后复制。Artifact 写入发生在对应 SQLite 成功记录之前，因此快照引用的 Artifact 应当已存在；备份中可能额外包含快照完成后产生但尚未被该数据库快照引用的文件，这些文件不会破坏恢复结果。

## 3. 校验备份

任何恢复前都必须先校验：

```powershell
agent-runtime backup verify D:\Backups\agent-runtime.agent-backup
```

校验内容：

1. ZIP 路径安全和重复 Entry。
2. Manifest 格式版本。
3. 数据库与 Artifact 字节数和 SHA-256。
4. SQLite `quick_check`。
5. SQLite `foreign_key_check`。
6. schema migration 名称与 checksum。
7. Manifest 表记录数与数据库事实一致。

成功返回码为 `0`；损坏或不兼容返回码为 `2`。

## 4. 恢复

### 4.1 停止服务

先停止：

- `agent-runtime lab`
- Uvicorn/FastAPI
- 使用相同 `runtime.sqlite3` 的 Python 进程
- 其他 SQLite 查看或维护程序

### 4.2 执行恢复

```powershell
agent-runtime backup restore D:\Backups\agent-runtime.agent-backup --force
```

恢复流程：

1. 完整校验归档。
2. 检查目标 SQLite 是否可 checkpoint 和获取排他锁。
3. 清理已停止数据库的 WAL/SHM sidecar。
4. 将当前数据库和 Artifact 目录重命名为 `pre-restore-*` 回滚副本。
5. 安装已校验数据库和 Artifact。
6. 再次执行 SQLite 一致性检查。
7. 成功后保留回滚副本，并在命令输出中返回路径。

确认恢复无误后，如不需要保留自动回滚副本，可在恢复时使用：

```powershell
agent-runtime backup restore D:\Backups\agent-runtime.agent-backup `
  --force `
  --discard-previous
```

## 5. 自动恢复演练

项目提供确定性演练：

```powershell
python scripts/run_backup_recovery.py
```

演练会验证：

- 在线创建备份。
- 归档校验。
- 备份前 Run 可以恢复。
- 备份后 Run 不会错误出现在恢复点。
- Artifact 内容恢复到备份版本。
- 恢复后的 SQLite 健康检查通过。

该演练已经进入 PR 和 Nightly 门禁。

## 6. 推荐备份策略

单机开发环境建议：

- 每天至少一次备份。
- 重要 AgentDefinition、Workflow 或数据库迁移前额外备份。
- 至少保留最近 7 个日备份和 4 个周备份。
- 将备份复制到与运行目录不同的磁盘或远程存储。
- 定期执行 `backup verify`，不能只验证文件是否存在。
- 每月至少执行一次完整恢复演练。

## 7. 故障处理

### 数据库无法打开

1. 不要删除原数据库、WAL 或 SHM。
2. 复制整个状态目录作为故障现场。
3. 运行 `agent-runtime observe diagnostics` 与 `agent-runtime doctor --json`。
4. 校验最近备份。
5. 停止 Runtime 后执行恢复。

### 恢复提示目标仍在使用

说明仍有进程连接数据库。不要绕过检查；关闭进程后重试。

### 备份校验失败

不要执行恢复。保留损坏归档用于分析，并选择更早且校验通过的备份。

## 8. 当前限制

- v0.7.10 只能恢复到归档记录的原数据库和 Artifact 绝对路径。
- 原因是历史 ToolExecution 和 Event 中仍包含绝对 Artifact 引用。
- 当前不支持跨机器或跨目录自动重写 Artifact 引用。
- 备份没有加密；包含敏感输入、Memory 或 Tool Result 时，必须依赖磁盘或外部存储加密。
- 当前没有自动调度和自动保留清理，需由操作系统任务或外部备份系统调用 CLI。
- 备份恢复不替代 Provider 凭据、Tool Handler 和应用配置管理。

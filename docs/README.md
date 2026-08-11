# Agent Runtime 文档中心

本目录是 Agent Runtime 的架构、功能现状和演进历史的事实来源。

## 文档导航

- [CURRENT.md](./CURRENT.md)：当前版本、能力状态、限制、测试结果和运行方式。
- [ARCHITECTURE.md](./ARCHITECTURE.md)：当前系统架构与实现边界。
- [CHANGELOG.md](./CHANGELOG.md)：按完成时间倒序维护的功能与架构演进记录。
- [adr/README.md](./adr/README.md)：关键架构决策记录（ADR）索引。
- [templates/change-entry.md](./templates/change-entry.md)：演进记录模板。
- [templates/adr.md](./templates/adr.md)：ADR 模板。

## 文档职责

| 文档 | 回答的问题 | 更新时机 |
| --- | --- | --- |
| `CURRENT.md` | 系统现在能做什么、不能做什么？ | 功能状态、版本、测试或限制变化时 |
| `ARCHITECTURE.md` | 系统现在如何组成和运行？ | 模块边界、数据流、安全或恢复语义变化时 |
| `CHANGELOG.md` | 系统何时、为何、如何发生变化？ | 每个可独立验收的功能、修复或架构变更完成时 |
| `adr/*.md` | 为什么选择这一方案而不是其他方案？ | 公共接口、数据、可靠性或安全决策变化时 |

## 演进约定

- 文档语言以中文为主，代码标识、协议和接口名称保留英文。
- 时间统一使用 `Asia/Shanghai`，日期格式为 `YYYY-MM-DD`。
- `CHANGELOG.md` 最新记录在最上面，同一天按序号倒序排列。
- 每条演进记录使用稳定的 Change ID：`EYYYY-MM-DD-NNN`。
- Change ID 是主追溯标识，Git commit hash 是补充；提交前可使用 `pending`，合并前必须补全。
- 核心代码发生功能性变化时，必须同步更新 `CHANGELOG.md`；影响当前事实时还要更新 `CURRENT.md` 或 `ARCHITECTURE.md`。
- 影响公共 API、事件/存储 schema、恢复语义或安全边界时，必须新增或更新 ADR。

## 开发完成检查

```powershell
python scripts/check_docs.py
pytest
```

如果需要检查某个 Git 基线之后是否遗漏演进记录：

```powershell
python scripts/check_docs.py --base-ref <base-commit>
```

## 变更追溯

- Change ID：`EYYYY-MM-DD-NNN`
- CHANGELOG 条目：
- 关联 ADR：不需要 / `ADR-NNNN`
- 完成时间：`YYYY-MM-DD`（Asia/Shanghai）

## 变更说明

简述变更目标、实现方式和用户可见结果。

## 验收结果

- [ ] `python scripts/check_docs.py` 通过
- [ ] `pytest` 通过
- [ ] 已更新 `docs/CHANGELOG.md`
- [ ] 已根据当前事实更新 `docs/CURRENT.md`
- [ ] 已根据架构变化更新 `docs/ARCHITECTURE.md`
- [ ] 影响公共 API、Event/存储 schema、恢复语义或安全边界时，已新增或更新 ADR
- [ ] 合并前已将 `pending` commit 回填为真实 Git hash

## 风险和兼容性

说明 breaking change、迁移、安全、数据兼容和回滚影响；没有则写“无”。

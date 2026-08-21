# ADR-0046：Acceptance Report 必须显式并严格匹配执行范围

- **状态**：Accepted
- **日期**：2026-08-21
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-21-003](../CHANGELOG.md#e2026-08-21-003)
- **取代**：无；本 ADR 补充 [ADR-0045](./0045-acceptance-regression-gate.md) 的报告比较约束

## 背景

v0.8.22 的离线 Acceptance Regression Gate 已能够比较两份脱敏报告，但如果 Baseline 和 Candidate 的 Case 范围不同，旧逻辑可能把 Candidate 额外执行的 Attempt 当作 warning，并仍返回 `passed`。这会把“没有比较完整”误认为“没有回归”，形成假通过。

Acceptance 报告需要同时服务于本地学习、真实模型验收和后续 CI/Nightly 门禁，因此比较结果必须能够回答一个更基础的问题：两份报告是否真的来自同一组执行范围。

## 决策

1. `RealModelAcceptanceRunner` 在报告中持久化 `selection` 元数据：
   - `case_names`：本次选择的 Case 名称；
   - `repeat`：每个 Case 的计划重复次数；
   - `expected_attempts`：计划总 Attempt 数；
   - `actual_attempts`：实际落盘 Attempt 数。
2. 默认 strict compare 先校验：
   - `suite_name`、`suite_version`、`suite_checksum` 一致；
   - 两份报告的 `selection` 可解释；
   - `(case_name, attempt)` 集合完全一致。
3. 范围校验通过后，才执行既有回归判断。范围缺失、额外或不完整都返回 `status: incompatible`，而不是 `passed + warning`。
4. 部分比较必须显式指定 `--case`。部分比较结果使用 `status: partial` 和 `scope: partial`；即使是部分比较，也要求被选 Case 的 Attempt 集合在两份报告中一致。
5. 旧报告缺少 `selection` 时，可以根据结果推断范围以维持兼容，但必须产生 warning，建议重新生成 v0.8.23+ 基线。
6. Compare 继续保持离线纯函数边界：不创建 Runtime、不读取 Case SQLite、不访问 Workspace、不调用 Provider。

## 影响

### 优点

- 把“报告范围不兼容”和“执行行为回归”明确分开。
- 缺失或额外 Attempt 不会被误降级为普通 warning。
- 报告本身保存了重现比较所需的范围信息，便于审计和 CI 门禁。
- 通过显式 `--case` 保留调试单 Case 的效率，同时避免隐式部分比较。

### 代价

- 旧报告可能只能推断范围并带 warning，不能完全达到新格式的可追溯性。
- 重新选择 Case 或调整 repeat 后，必须生成新的 Baseline，不能跨范围强行比较。
- `partial` 表示只比较了指定子集，不应被解释为完整 Suite 通过。

## 被放弃的方案

### 把额外 Candidate Attempt 作为 warning 并返回 passed

该方案无法区分“候选新增覆盖”与“Baseline 缺少必要 Case”，会制造假通过。

### 默认自动取两份报告的交集

隐式取交集会隐藏 Case 缺失，调用者很难从退出码判断是否完成了全量回归。

### 比较时自动重新运行缺失 Case

这会破坏离线、确定性和低成本的报告比较边界，也会重新引入模型随机性和费用。

## 后续约束

- 任何新增或修改 Acceptance Case 必须同步更新 Suite checksum，并重新建立完整 Baseline。
- CI/Nightly 的完整回归必须使用 strict compare，不得依赖 `--case` 绕过范围不一致。
- 需要在 UI 中展示 Acceptance 结果时，必须同时展示 `status` 和 `scope`，不能只显示通过率。

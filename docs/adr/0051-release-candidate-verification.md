# ADR-0051：本地 Release Candidate 必须经过可重复的发布验证

- **状态**：Accepted
- **日期**：2026-08-22
- **决策人**：Agent Runtime Maintainers
- **关联变更**：[E2026-08-22-007](../CHANGELOG.md#e2026-08-22-007)

## 背景

v0.8.x 的源码测试通过并不等于发布产物可用。Wheel 可能携带错误版本、干净环境安装可能缺少依赖，CLI、SDK 或 FastAPI/Uvicorn 入口也可能与源码运行路径不一致。发布验证还必须避免依赖真实模型和 API Key。

## 决策

1. `scripts/verify_distribution.py` 在创建干净虚拟环境前，先检查 `pyproject.toml`、`src/agent_runtime/version.py`、README、CURRENT、ROADMAP、Learning Console 和 Wheel filename/metadata 的版本一致性。
2. 验证脚本输出稳定的 Release Candidate checklist，并可通过 `--report` 写出 JSON 结果，供本地复核和 CI artifact 使用。
3. 版本检查通过后，继续在全新虚拟环境安装 Wheel，并执行已有 CLI demo、Python SDK、FastAPI/Uvicorn、SSE、备份和诊断 smoke；Provider 使用 Mock，不读取或记录 API Key。
4. 不重写发布系统，不自动上传制品，不改变 Runtime 或 Tool 的业务语义。

## 影响

### 优点

- 版本漂移会在昂贵的干净环境 smoke 之前快速失败。
- 源码运行和 Wheel 安装路径使用同一套可重复验收清单。
- CI 可以保存结构化 RC 结果，而不暴露任何敏感配置。

### 代价

- 每次 RC 验收需要创建临时虚拟环境并安装依赖。
- 该验证仍不能证明真实 Provider 服务端行为或跨平台所有安装差异。

## 被放弃的方案

- 只检查 Git tag 或 Wheel 文件名：无法发现文档、源码和 metadata 漂移。
- 只运行源码 pytest：无法证明干净环境中 CLI/API 入口和打包依赖可用。

## 后续约束

以后变更发布入口、打包元数据或当前版本展示时，必须更新 RC checklist/测试，并在发布前运行 `python scripts/verify_distribution.py dist --report release-candidate-report.json`。

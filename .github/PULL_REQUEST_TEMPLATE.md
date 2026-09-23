## 变更概述 · Summary

(用一句话 + 关键细节描述这次 PR 解决了什么)

## 关联 Issue

- Closes #(如有)

## 变更点 · Changes

- [ ] 功能 / 修复描述
- [ ] 涉及文件:`tradingagents/...`, `cli/...`, `docs/...`

## 影响范围(勾选)

- [ ] 数据路由(`tradingagents/dataflows/`)— 影响 vendor fallback 链 / 数据语义
- [ ] LLM 客户端 / 模型 / 提示词(`tradingagents/llm_clients/`, `tradingagents/agents/`)
- [ ] 分析管线(`tradingagents/graph/`)— 影响 Graph / checkpoint 语义
- [ ] CLI / 配置(`cli/`, `tradingagents/default_config.py`, `.env.example`)
- [ ] 报告输出 / 持久化格式
- [ ] 无影响(纯文档 / 测试 / 重构)

## 验证 · Verification

本地已跑过(请如实勾选):

- [ ] `uv run ruff check .` 通过
- [ ] `uv run mypy tradingagents cli --ignore-missing-imports` 通过
- [ ] `uv run pytest -m unit` 通过(新增测试 ____ 个)
- [ ] 新增测试均带 `unit` / `integration` / `smoke` 标记
- [ ] (如涉及)集成测试 `uv run pytest -m integration` 通过 / 已注明 skip 原因

## 截图 / 报告样例

(如为报告 / UI 相关改动,贴样例输出)

## 补充说明

(任何维护者需要知道的注意事项:破坏性变更、环境变量、迁移等)
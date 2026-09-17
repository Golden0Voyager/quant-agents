# tradingagents/agents

Agent 角色层：分析师 → 研究辩论 → 经理 → 交易员 → 风险辩论 → 组合经理。

## 边界

- `analysts/` / `researchers/` / `managers/` / `trader/` / `risk_mgmt/` — 各角色节点实现
- `schemas.py` — 结构化输出契约：`ResearchPlan`、`TraderProposal`、`PortfolioDecision`
- `utils/` — 分析师工具层（数据工具 `*_tools.py`）、`agent_states.py`（图状态）、`structured.py`、`memory.py`
- 数据获取一律经 `utils/*_tools.py` → `tradingagents/dataflows/`，角色节点不直连数据源

## 契约

- 结构化输出统一走 `utils/structured.py` 的 `bind_structured()` + `invoke_structured_or_freetext()` 回退，
  不要在节点内直接调用 `with_structured_output`（deepseek-reasoner 不支持 `tool_choice`，会抛
  `NotImplementedError`，必须保留自由文本回退路径）
- Dual-LLM 分工：分析师/辩论者用 `quick_think_llm`，经理/交易员/组合经理用 `deep_think_llm`；
  单个分析师与 reflector 可经 `quick_think_llm_roles` 覆盖模型
- 修改 `schemas.py` 字段时同步检查消费方（managers、trader、cli 报告渲染）与相关单测

## 验证

- `uv run python -m pytest tests/test_agent_nodes.py tests/test_agent_tools.py tests/test_agent_utils.py -m unit`
- 全量：`uv run python -m pytest -m unit`

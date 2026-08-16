# 数据可靠性与证据覆盖第二阶段设计

> 日期：2026-08-16  
> 状态：已完成设计评审，待用户复核书面规格  
> 范围：`quant_agents` 与 `quant_pipeline` 的日期语义、数据结果语义、新闻与公告证据归档、资产能力、结构化输出和报告审计

## 1. 背景

2026-08-16 批量运行已生成 18 份报告。原始文本审计显示所有报告出现 `NO_DATA` 或 `fallback`，但结构化诊断进一步确认这些命中混合了四种不同情况：

1. 数据源成功回退，例如本地数据库回退到 AkShare；
2. 查询成功但窗口内确实没有事件，例如限售解禁；
3. 工具不适用于当前市场，例如港股调用 A 股北向持股；
4. 真实的数据缺失、网络失败或模型结构化输出失败。

主要实证问题包括：

- 2026-08-16 是周日，涨跌停工具仍直接查询该自然日；`quant_core.db` 的最近交易日为 2026-08-14；
- 相同个股新闻请求被多个 Analyst 重复调用，产生额外限流风险和不一致结果；
- 当前财联社工具只读取最新一页，`look_back_days` 参数没有实际生效；
- 17 只 A 股的“窗口内无解禁事件”被标为 `no_data`；
- 港股报告调用 A 股专属治理和情绪工具；
- 18 份报告中有 11 份出现 Structured fallback，共 13 次，主要位于 Research Manager 和 Portfolio Manager。

2026-08-11 的第一阶段已经实现本地优先适配、CNINFO/Tushare 可选来源、统一路由诊断和报告覆盖区块。本设计是其第二阶段，不重复实现已有能力。

## 2. 目标

1. 将分析观察日期、交易市场截止日期和新闻证据窗口日期分开表达。
2. 将成功回退、正常空结果、不适用、部分覆盖、过期、无数据和失败准确分类。
3. 通过本地证据归档提高 A 股和港股新闻、公告覆盖率，并保留来源和时效。
4. 同一 ticker 运行内共享相同数据请求，减少网络访问和结果漂移。
5. 按资产市场选择适用工具，避免港股调用 A 股专属数据。
6. 降低 DeepSeek V4 的 Structured fallback，并让模型降级与数据降级分别可解释。
7. 保持两个仓库可独立发布；旧数据库和未部署的新表不阻塞现有运行。

## 3. 全局约束

- 默认只使用免费或公开来源；Tushare 等付费、积分型来源仅作可选增强。
- 不整体引入 AxData，不增加第二套 DuckDB/Parquet 数据基础设施。
- 不保存受版权保护的新闻全文，不默认下载巨潮或 HKEX 公告 PDF。
- `quant_agents` 对 `quant_core.db` 保持只读。
- 数据库 migration 只能新增表和索引，不修改或删除现有业务表。
- 不通过无限重试提高覆盖率；所有来源都有请求超时、重试次数和批次预算。
- 不改变投资评级权重、交易策略或决策流程顺序。
- 所有借鉴代码必须保留来源、许可证和本项目改动说明。

## 4. 方案选择

### 4.1 已选方案：分阶段双仓库架构

`quant_agents` 负责分析时语义和只读查询；`quant_pipeline` 负责证据采集、归档、去重和覆盖审计。两个仓库通过 `quant_core.db` 的稳定契约协作，不直接互相导入 Python 模块。

### 4.2 未选方案

- 仅修改 `quant_agents`：不能解决历史新闻缺口和实时来源波动。
- 使用 AxData 替换现有数据层：与 `quant_core.db` 重叠，迁移和回归风险超出本次范围。

## 5. 总体模块设计

### 5.1 `DataPolicyRegistry`

该 Module 位于 `quant_agents`，其 Interface 根据工具和资产市场返回一条 `ToolPolicy`：

```python
@dataclass(frozen=True)
class ToolPolicy:
    applicable_markets: frozenset[str]
    date_policy: Literal["market_session", "calendar_window", "latest_snapshot"]
    empty_semantics: Literal["confirmed_empty", "coverage_gap"]
    impact: Literal["high", "medium", "low"]
    allowed_vendors: tuple[str, ...]
```

所有工具适用性、日期策略、空结果语义和影响等级集中在该 Interface 后，Prompt、Adapter 和报告不得维护第二套散落规则。

### 5.2 `MarketDateResolver`

该 Module 输入分析日期、交易所和日期策略，返回：

```python
@dataclass(frozen=True)
class AnalysisDates:
    analysis_date: str
    market_as_of_date: str
    evidence_window_end: str
```

- `analysis_date` 是报告观察时点；
- `market_as_of_date` 是不晚于分析日期的最近有效交易日；
- `evidence_window_end` 允许新闻和公告覆盖到分析自然日。

交易日规则复用 `exchange_calendars` 的 XSHG/XHKG 日历。本地数据库最新日期只用于数据新鲜度检查，不用于定义交易日。

### 5.3 `EvidenceIngestion`

该 Module 位于 `quant_pipeline`。各来源 Adapter 负责请求外部来源并返回标准记录，Module 负责：

- 时间窗口拆分；
- 请求预算；
- 字段规范化；
- 去重与幂等写入；
- 覆盖窗口记录；
- `ingestion_runs` 审计；
- 失败时保护已有数据。

### 5.4 `EvidenceStore` seam

`quant_pipeline` 写入标准证据表，`quant_agents` 通过 `smartmoney_vendor` 的只读 Adapter 查询。两个仓库不共享运行时对象或连接。

查询 Interface：

```python
def query_company_evidence(
    ticker: str,
    start_date: str,
    end_date: str,
    kinds: tuple[str, ...],
) -> VendorPayload:
    ...
```

新闻与公告证据不使用普通的“首个成功来源即停止”策略。`EvidenceStore` 同时返回已覆盖窗口；如果本地只覆盖请求窗口的一部分，Evidence resolver 只向在线 Adapter 查询剩余缺口，然后按 `(source, source_record_key)` 和 `content_hash` 合并去重。其他行情、基本面和治理工具继续使用现有的顺序 fallback 路由。

### 5.5 `RequestMemo`

该 Module 位于 `quant_agents`，生命周期限定为单 ticker、单次 graph 运行。它提供 single-flight 和成功结果缓存，避免多个 Analyst 对相同逻辑请求重复访问外部来源。

## 6. 证据数据库契约

### 6.1 `evidence_items`

保存一份规范化证据正文和来源信息：

- `evidence_id TEXT PRIMARY KEY`
- `source TEXT NOT NULL`
- `source_record_key TEXT NOT NULL`
- `evidence_kind TEXT NOT NULL`
- `title TEXT NOT NULL`
- `summary_text TEXT`
- `published_at TEXT NOT NULL`
- `source_url TEXT`
- `content_hash TEXT NOT NULL`
- `record_status TEXT NOT NULL DEFAULT 'active'`
- `ingestion_run_id TEXT`
- `ingested_at TEXT NOT NULL`

唯一约束：`(source, source_record_key)`。

`evidence_kind` 首期值固定为：

- `company_news`
- `market_flash`
- `announcement`
- `regulatory_filing`

`record_status` 首期值固定为：

- `active`
- `cancelled`
- `superseded`

### 6.2 `evidence_symbols`

映射证据与股票：

- `evidence_id TEXT NOT NULL`
- `ticker TEXT NOT NULL`
- `market TEXT NOT NULL`
- `relation_type TEXT NOT NULL`
- `match_confidence REAL NOT NULL`

唯一约束：`(evidence_id, ticker)`。

`relation_type` 固定为 `explicit`、`name_match`、`sector_only`。只有 `explicit` 和达到配置阈值的 `name_match` 默认进入个股报告。

### 6.3 `evidence_coverage`

记录来源是否完整检查过指定窗口：

- `source TEXT NOT NULL`
- `scope_key TEXT NOT NULL`
- `window_start TEXT NOT NULL`
- `window_end TEXT NOT NULL`
- `status TEXT NOT NULL`
- `row_count INTEGER NOT NULL DEFAULT 0`
- `ingestion_run_id TEXT`
- `checked_at TEXT NOT NULL`

`status` 固定为 `complete`、`partial`、`failed`。

覆盖判定：

- 完整覆盖且零记录：`valid_empty`；
- 仅部分时间覆盖：`partial`；
- 没有完整覆盖证明且无记录：`no_data`；
- 请求已经执行但因网络、格式或限流失败：`failed`。

### 6.4 数据保留

- 元数据、快讯正文和公开摘要长期保留；
- 不保存受版权保护的新闻全文；
- 公告只保存官方链接，不默认下载 PDF；
- 取消或替换的公告保留历史记录并更新 `record_status`。

## 7. 数据来源与开源复用

### 7.1 A 股新闻

优先级：

1. 本地 `EvidenceStore`；
2. AkShare/Eastmoney 个股新闻；
3. 财联社按代码、简称和别名过滤；
4. Yahoo 低置信度补充。

### 7.2 A 股公告

优先级：

1. 本地巨潮归档；
2. CNINFO 在线 Adapter；
3. AkShare/Eastmoney；
4. 可选 Tushare。

### 7.3 港股新闻与公告

港股新闻优先使用本地证据和 Yahoo；财联社只有在明确关联港股代码或公司名称时才纳入。港股公告优先使用本地 HKEX 归档，再使用 HKEX 在线 Adapter，不使用 A 股公告来源兜底。

### 7.4 成熟项目复用决策

- 交易日历直接依赖 `exchange_calendars`，使用 XSHG/XHKG；项目为 Apache-2.0。
- `quant_pipeline` 使用轻量 MIT 包 `levistock` 的财联社接口，并在本项目 Adapter 中处理字段、错误、审计和限流。
- AxData 仅作为来源 Adapter、采集审计和质量元数据的设计参考，不作为依赖。
- HKEX Adapter 参考 `skxox/hkex-announcement-sync` 的 JSF 会话、分页和双语匹配方式，仅实现公告元数据查询。

## 8. 统一结果语义

`VendorRouteDiagnostic.status` 支持：

- `ok`
- `ok_fallback`
- `valid_empty`
- `not_applicable`
- `partial`
- `stale`
- `no_data`
- `unavailable`
- `failed`

内部 Adapter 可返回：

```python
@dataclass(frozen=True)
class VendorPayload:
    data: str
    status: Literal["ok", "valid_empty", "partial", "stale"]
    as_of: str | None
    reason: str
```

旧 Adapter 返回字符串时继续视为 `ok`。`route_to_vendor()` 继续只返回业务文本，保持调用兼容；`route_to_vendor_with_source()` 和 route diagnostics 保留结构化元数据。

语义要求：

- 成功查询本周解禁但没有事件：`valid_empty`；
- 最新完整质押快照中没有该股票：`valid_empty`；
- 质押快照超过策略时效：`stale`；
- 无法解析上游响应：`failed`；
- 港股调用 A 股北向工具：在调用前判断为 `not_applicable`。

## 9. 请求缓存与资产能力

### 9.1 请求缓存

缓存键包含方法、规范化 ticker、规范化日期、关键参数和 DataPolicy 版本。

缓存：

- `ok`
- `ok_fallback`
- `valid_empty`
- `not_applicable`

不缓存临时 `failed`。相同逻辑请求并发到达时只允许一条解析流程执行，其余调用等待同一结果。该流程仍可按策略尝试多个 vendor，但每个 vendor 对相同规范化参数最多执行一次。

### 9.2 资产能力

Agent 创建工具列表前根据 `DataPolicyRegistry` 过滤。

A 股可启用涨跌停、北向、融资融券、龙虎榜、质押、巨潮公告和财联社。港股可启用港股行情与财务、Yahoo/本地新闻和 HKEX 公告。

港股不得获得 A 股涨跌停、北向、龙虎榜、A 股融资融券、巨潮公告和 A 股质押工具。需要资格判断的 A 股工具先检查陆股通或融资融券标的资格；不具备资格时返回 `not_applicable`。

## 10. Structured Output 稳定化

结构化策略按模型 capability 选择：

1. `json_schema`；
2. DeepSeek V4 等模型使用 `json_mode`；
3. 仅支持工具调用时使用 function calling；
4. 都不支持时直接进入明确标记的自由文本模式。

单个决策节点的有限修复链：

1. 首次结构化调用；
2. 本地确定性清理 Markdown fence、唯一 JSON 对象和已知字段别名；
3. schema validation 仍失败时，允许一次 JSON repair 请求；
4. 仍失败才进入自由文本 fallback。

本地清理和 repair 不得补造报价、止损、仓位或其他缺失投资数据。无可靠数据的数值字段允许 `null`；评级、动作和置信度仍使用受限枚举。

每个决策节点记录：

- `agent_name`
- `model`
- `structured_method`
- `attempt_count`
- `failure_kind`
- `repair_used`
- `fallback_used`
- `validation_summary`

## 11. 报告与审计

每份报告分别展示“数据可靠性”和“决策生成可靠性”。数据源 fallback 与 Structured fallback 不共用字段。

数据可靠性显示：

- 确认覆盖；
- fallback 成功；
- 正常无事件；
- 不适用；
- 部分、过期和失败；
- 来源、截止日期和原文链接。

决策生成可靠性显示 Research Manager、Trader 和 Portfolio Manager 的结构化方法、repair 使用情况和最终 fallback 状态。

`batch_summary.json` 为每只股票增加：

```json
{
  "data_reliability": {
    "confirmed": 18,
    "fallback_success": 4,
    "valid_empty": 3,
    "not_applicable": 1,
    "partial": 1,
    "missing": 2
  },
  "decision_reliability": {
    "structured_success": true,
    "repair_used": false,
    "fallback_agents": []
  }
}
```

审计器按结构化诊断统计并合并重复逻辑请求，同时保留 `call_count`。A 股和港股分别计算分母。`valid_empty`、`not_applicable` 和 `ok_fallback` 不计为缺失。

## 12. 分阶段实施

### 阶段 1：`quant_agents` 正确性

- 实现 DataPolicy、市场日期、结果语义、RequestMemo、资产能力和新审计；
- 在旧数据库上仍可运行；
- 2026-08-16 的 A 股市场工具使用 2026-08-14；
- `1810.HK` 不调用 A 股专属工具。

### 阶段 2：`quant_pipeline` 证据归档

- 新增 additive migration；
- 实现财联社、巨潮采集、覆盖窗口、去重和审计；
- 同一 source record 重跑不重复；
- 失败抓取不覆盖历史成功数据。

### 阶段 3：港股公告

- 实现 HKEX Adapter、代码规范化、双语标题和官方链接；
- `1810.HK` 可查询最近一周公告；
- 无公告返回 `valid_empty`；
- 不下载 PDF。

### 阶段 4：Structured Output

- 实现 capability-driven JSON strategy、本地清理、单次 repair 和独立诊断；
- 单节点最多一次 repair 请求；
- 不因模型结构化失败中断 batch。

## 13. 测试与验收

每个仓库执行其权威检查：

```bash
rtk uv run ruff check .
rtk uv run python -m pytest -m unit
```

`quant_pipeline` 额外执行 migration 幂等、SQLite `quick_check`、外键、唯一约束和失败回滚测试。

真实网络冒烟：

- `002413`：完整 A 股分析；
- `1810.HK`：港股能力和 HKEX 公告；
- `my` watchlist：3 workers 批量验收。

批量验收对比：

- 新闻确认覆盖率；
- 真实缺失率；
- `valid_empty` 数量；
- 每只股票外部请求数；
- fallback 成功率；
- Structured fallback 率；
- 总运行时间和失败 ticker 数。

量化目标：

- 同一 ticker、方法和参数在一次运行内只解析一次；fallback 链中的每个 vendor 最多执行一次；
- 交易日类工具不再查询周末日期；
- 港股 A 股专属工具调用数为零；
- 18 只股票批量验收的 Structured fallback 从 11/18 降至不超过 2/18；
- 新证据表不可用时，旧 vendor 链仍能完成分析。

## 14. 发布与回滚

- 两个仓库分别使用功能分支和 PR；
- 先合并 `quant_agents`，再合并 `quant_pipeline`；
- migration 只新增表和索引，旧代码可忽略新表；
- `quant_agents` 检测不到证据表时自动使用原 vendor 链；
- Structured strategy 可通过配置切回 function calling；
- 不删除旧报告、checkpoint 或现有数据库记录。

## 15. 非目标

- 不整体引入 AxData；
- 不强制付费数据源；
- 不保存新闻全文或默认下载 PDF；
- 不改变投资评级权重和交易策略；
- 不使用无限重试；
- 不把 `valid_empty` 伪装成有数据；
- 不把语义不等价的来源合并成同一指标。

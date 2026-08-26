# 同花顺 Financial-API 能力报告 & 与 quant_agents 匹配分析

> 调研日期：2026-08-26
> 仓库：https://github.com/HiThink-Tech/Financial-API （⭐1,794 / MIT / 官方维护）
> 官网：https://fuyao.aicubes.cn/ · 文档契约：https://fuyao.aicubes.cn/llms-full.txt
> 实测：已用有效 Key 探测 21 个核心端点，全部 `code=0`，无权限拒绝（公测期全能力开放）

---

## 第一部分：能力报告

### 1.1 基本信息

| | |
|---|---|
| 来源 | 同花顺官方（HiThink-Tech），非逆向、非爬虫 |
| 定位 | 面向 AI Agent / 量化研究的 A股结构化金融数据服务 |
| 时间线 | 2026-06-09 创建，持续活跃维护；当前为公测期免费 |
| 收费 | 无公开定价页；API Key 用同花顺账号免费签发。官方表述：「数据权限、调用频率和可访问 capability 以官网与账号授权为准」 |
| 认证 | 统一 `X-api-key` 请求头；REST/MCP/CLI/Python SDK 共用一个 Key |
| 环境变量约定 | `HITHINK_FINANCE_API_KEY` |

### 1.2 接入方式矩阵

| 方式 | 适用场景 | 备注 |
|---|---|---|
| REST API | 程序化接入（本项目主路径） | 统一 `ApiResponse{code, message, request_id, data}` 信封；HTTP 恒 200，业务错误走 `code` 字段 |
| MCP 托管端点 ×4 | Agent 直接调用 | `a-share` / `a-share-index` / `meta` / `fund` 四个 HTTP 端点 |
| Node CLI `hithink-finance` | 批量取数、大结果落盘 | `npm i -g @hithink-tech/hithink-finance-cli`，运行时不依赖 Python |
| Python SDK | notebook / 研究脚本 | 可与 pandas 组合 |
| Agent Skill | Agent 统一入口 | `npx skills add HiThink-Tech/Financial-API --skill hithink-finance -g --yes` |
| marketdb (DuckDB) | 本地全市场库 + SQL 研究 | 与本项目 quant_core.db 定位重叠（见 2.6） |

### 1.3 错误码（接入时必须处理）

| code | 含义 |
|---|---|
| `0` | 成功 |
| `1001` / `1002` / `1003` / `1004` | 参数缺失 / 格式错 / 越界 / 冲突 |
| `2001` | 未认证（Key 缺失或无效） |
| `2003` | **权限不足**（capability 未开放给该 Key） |
| `3001` / `3002` / `3004` | 标的不存在 / 数据未就绪 / 类型不支持 |
| **`4001`** | **频率超限（QPS）——批跑必须做退避重试的唯一硬约束** |
| `5001` / `5002` / `5003` | 服务端错误 / 上游超时 / 数据源不可用 |

### 1.4 能力全景（56 个端点，按数据域）

#### A. 价格与基础（8）

| 端点 | 内容 | 关键参数格式 |
|---|---|---|
| `/api/a-share/prices/snapshot` | 实时行情快照，支持批量 + 全市场分页 | `thscodes=600519.SH,000001.SZ` |
| `/api/a-share/prices/historical` | 日/周/月 K，前复权/后复权 | `interval=1d&adjust=forward&start/end=毫秒时间戳` |
| `/api/a-share/corporate-actions/adjustment-factors` | 分红/送股/配股原始事件流 | `thscode&from/to=YYYY-MM-DD` |
| `/api/a-share/calendar/trading-days` | 近一年交易日序列 | 无参 |
| `/api/meta/tickers/search` | 名称/代码跨市场消歧 → 唯一 thscode | `q=贵州茅台` |
| `/api/meta/tickers/list` | 代码表分页获取 | 按资产类型 |
| `/dump/market-dumps`（API: `/api/dump/...`） | 全市场 10 年日K + 复权因子 Parquet | 预签名链接一次拉全量 |
| `/api/a-share/auction/snapshot` + `/api/a-share/auction/short-term-benchmark` | 集合竞价快照 + 短线风向标基准 | 盘前数据 |

#### B. 财务（5）

| 端点 | 内容 | 参数 |
|---|---|---|
| `/api/a-share/financials/income-statements` | 利润表多期序列 | `period=quarterly\|annual&limit≤20` 或毫秒区间（与 limit 互斥） |
| `/api/a-share/financials/balance-sheets` | 资产负债表多期 | 同上 |
| `/api/a-share/financials/cash-flow-statements` | 现金流量表多期 | 同上 |
| `/api/a-share/financials/indicators` | 五类 23 项指标（成长/盈利/偿债/营运/现金流） | `report=2025-1` 单期；返回 `data.abilities[]`（注意不是 `item[]`） |
| `/api/a-share/valuations/snapshot` | 批量 PE(TTM/MRQ)/PB(MRQ)/PS(TTM)/PCF(TTM) | `thscodes` 批量 |

#### C. 特色盘面数据（11）——akshare 最不稳的部分

| 端点 | 内容 |
|---|---|
| `special-data/limit-up-pool` / `limit-down-pool` / `limit-break-pool` | 涨停/跌停/炸板池（按交易日分页，`trade_date=20260825`） |
| `special-data/limit-up-ladder` | 连板天梯（近 30 日矩阵） |
| `special-data/dragon-tiger-list` | 龙虎榜（全部榜/机构榜/游资榜） |
| `special-data/hot-stock-list` + `-history` + `rank-trend` | 热榜 + 历史热股排行 + 个股排名走势 |
| `special-data/anomaly-analysis-list` / `-stock` | 个股异动原因（官方解释"为什么动"） |
| `special-data/skyrocket-list` | 飙升榜 |

#### D. 指数/板块（4）

THS 指数目录（含行业/概念 tag）、成分股清单、指数行情快照、指数历史 K 线（同时支持沪深300等标准指数）。

#### E. 公募基金（28）

资料/公司/经理（风格+业绩+经历）/披露持仓（股票+债券）/净值/区间收益/回撤/持有人结构/前十大持有人/财务/分红/资讯/募集/ETF·LOF 场内行情与历史日线。

### 1.5 明确不提供（官方声明 + 实测确认）

❌ 分钟K / tick　❌ 港股美股等海外行情　❌ 宏观数据　❌ 新闻公告原文　❌ 研报原文
❌ 主力资金流向　❌ 北向持股　❌ 股东户数 / 股权质押 / 融资融券 / 大宗交易 / 解禁　❌ 券商盈利预测　❌ 筹码分布

---

## 第二部分：与 quant_agents 的匹配分析

### 2.1 本项目数据层架构现状

`tradingagents/dataflows/interface.py` 是按方法名路由的 vendor-chain 架构：
`route_to_vendor("get_income_statement", ...)` → `_build_vendor_chain()` 按优先级依次尝试
（A 股默认链：本地 quant_core.db 的 `smartmoney_db` 优先 → akshare 在线兜底；
tushare 为 opt-in，仅当 `tushare_enabled` 配置或 `TUSHARE_ENABLED=1` 时追加链尾），
带熔断（`_ROUTE_CIRCUIT_BREAKERS`）、降级标记（路由内 `ok_fallback`，batch_summary 渲染为 `[fallback]`）、route diagnostics。

**结论：接入 hithink = 新增一个实现同名方法的 vendor 文件 + 在 `VENDOR_METHODS`（interface.py:902）对应方法下注册一行（+ 顶部 import），不需要动任何 analyst 代码。**
注意：`VENDOR_LIST`（interface.py:268）是无引用的死代码，新 vendor 无需登记。

### 2.2 核心映射矩阵（现有数据方法 × hithink 端点）

#### 🟢 高价值匹配（建议接管或前置）

| 现有方法 | hithink 端点 | 判定 | 理由 |
|---|---|---|---|
| `fetch_eastmoney_hot_rank`（eastmoney_sentiment.py） | `hot-stock-list` + `rank-trend` + `-history` | 🔥 最强匹配 | 该接口在 0824/0825 两批 23 份报告里每一次都是 partial 降级；hithink 版还多给个股排名时序走势 |
| `get_income_statement` / `get_balance_sheet` / `get_cashflow` | `financials/*` 三表 | 🔥 强匹配 | 中航光电批次的 `core_stock_apis` 降级来自这组 akshare 接口；官方结构化直出 |
| `get_indicators`（财务指标） | `financials/indicators` | 强匹配 | 五类指标一一对应，PM 报告的 ROE/毛利率/现金流比直接可用 |
| `get_dragon_tiger` | `dragon-tiger-list` | 强匹配 | 分机构榜/游资榜，信息量大于现有版本 |
| `get_limit_up_down` | `limit-up/down/break-pool` + `limit-up-ladder` | 强匹配 | 连板天梯是现版本没有的维度 |
| ticker_resolver 的 akshare 模糊匹配 | `meta/tickers/search` | 🔥 强匹配 | 0825 批次 9 只票公司名解析失败（8 只 A 股 + 1810.HK 显示 `--`）正是此环节；官方消歧根治 |
| `get_historical_valuation` / `get_industry_valuation`（部分） | `valuations/snapshot` | 增强 ✅ 已落地 | 官方估值快照（PE/PB/PS/PCF）已作交叉校验锚喂给 `market_data_validator`（`get_valuation_snapshot` + `fetch_valuation_metrics`） |
| `get_dividend_history` | `adjustment-factors` | 部分 | 事件流含现金分红/送股/配股，够复权与分红历史用 |
| （无现有对应） | `anomaly-analysis-stock` | 全新增量 ✅ 已落地 | "个股异动原因"维度：`get_anomaly_reason`，Sentiment 分析师预取注入（查不到=DATA_UNAVAILABLE 降级） |
| （无现有对应） | `auction/snapshot` + `auction/short-term-benchmark` | 全新增量 ✅ 已落地 | `get_auction_snapshot` / `get_short_term_benchmark` 作为 market analyst 工具（XSHG-only）支持盘前决策场景 |

#### 🟡 部分匹配

| 现有方法 | 说明 |
|---|---|
| `get_sector_fund_flow` / `get_concept_board` | THS 指数目录+成分股覆盖板块成分与行情；但板块资金流没有，仍留现有源 |
| `get_institutional_holdings` / `get_institutional_intelligence` | ❌ 反查不可行（2026-08-26 实测证伪）：`fund/portfolio/holdings` 是**基金视角**（`fund_type=exchange|otc|reits` + 基金代码 → 该基金的持仓明细，已验证 510300.SH），不存在"股票 → 哪些基金持有"的反向端点（`fund/holdings/by-stock` 等候选全部 404）；OTC 基金代码格式未解（110022 各种后缀均 3001 Fund not found）。机构维度维持现有源 + 已落地的龙虎榜机构榜 |
| `get_stock_data`（OHLCV） | quant_core.db 已是首选且更快；hithink 适合当第二在线层（排在 akshare 之前），qfq 直出省去自算复权 |

#### 🔴 无覆盖（现有链路一条都不能删）

| 现有方法 | 维持方案 |
|---|---|
| `get_fund_flow`（主力资金流） | Sentiment 分析师核心输入 → akshare/本地 |
| `get_northbound_hold` / `get_shareholder_count` / `get_pledge_ratio` / `get_margin_trading` / `get_block_trade` / `get_restricted_release` | Governance 分析师大半输入 → 维持现状 |
| `get_news` / `get_company_announcements` / `get_research_reports` | News 分析师 → akshare/cailianpress 维持（神州泰岳 ticker 碰撞问题与此 API 无关，需单独修） |
| `get_macro_indicators` | fred.py 维持 |
| `get_earnings_estimates` / `get_earnings_forecast` | 两批报告反复 missing 的就是它——hithink 补不上 |
| 小米等港股 | yfinance / quant_db_global 维持 |

### 2.3 六个 Analyst 受益评估

| Analyst | 受益度 | 结论 |
|---|---|---|
| Fundamentals | ★★★★★ | 三表 + 指标 + 估值全面升级，最大赢家 |
| Sentiment | ★★★★☆ | 热榜三件套彻底替代最不稳的 eastmoney hot_rank；资金流/北向仍留旧源 |
| Governance | ★★★☆☆ | 龙虎榜/涨停池/异动原因升级；股东户数/质押/两融缺口仍在 |
| Market | ★★★☆☆ | 官方 qfq K线质量升级；技术指标仍靠本地 stockstats 计算（不变） |
| Industry | ★★★☆☆ | 板块成分/K线升级；资金流和宏观缺口仍在 |
| News | ★☆☆☆☆ | 几乎无受益 |

### 2.4 接入架构建议

```
vendor chain 示例（以 get_income_statement 为例）：
  smartmoney(quant_core.db) → hithink_vendor(P1 新增) → akshare(在线兜底)
  （tushare 为 opt-in，仅 TUSHARE_ENABLED=1 / config 开启时追加链尾）
```

1. **新建 `tradingagents/dataflows/hithink_vendor.py`**：实现 8-10 个同名方法
   （三表×3、indicators、dragon_tiger、limit_up_down、hot_rank 替代、valuation、stock_data），
   内部统一走一个带退避的 `_get()` client（参考 `akshare_common.py:117` 的 `_akshare_retry`
   指数退避 + `errors.py:46` 的 `VendorRateLimitError`，触发限流时直接跳下一 vendor）。
2. **QPS 门控**：`4001` 指数退避重试；优先用批量接口
   （snapshot / valuations 支持 CSV thscodes，23 只票 1 次调用）。
   批跑调用量估算 ~120 次/批（23票×4 财务调用 + 特色数据按日查询），限速 2 req/s 足够。
3. **Key 管理**：`.env` 增加 `HITHINK_FINANCE_API_KEY`；
   当前调研用 Key 已在聊天中出现，正式接入时应到 admin 页轮换。
4. **溯源增强**：把响应中的 `request_id` + `timestamp` 写入 route diagnostics，
   让 `market_data_validator` 多一层官方数据源校验依据。
5. **不做的事**：不用 marketdb 替代 quant_core.db（功能重叠，本地 pipeline 已在维护）；
   仅在需要回补历史时使用其 market-dumps Parquet。

### 2.5 落地优先级

| 优先级 | 内容 | 解决的现实痛点 |
|---|---|---|
| **P0** | 三表×3 + indicators + hot_rank 替代 + tickers/search 接入 resolver | 最近两批报告中出现频率最高的降级项 |
| P1 ✅ | dragon_tiger + limit_up_down + 异动原因（新增 prompt 维度） | Governance / Sentiment 增强 |
| P2 ✅ | valuations 交叉校验、竞价数据盘前模式 | validator 增强、盘前决策 |

---

## 附：实测记录（2026-08-26）

以下端点用真实 Key 验证通过（`code=0`）：

标的检索 / 交易日历(243日) / 行情快照 / 历史K线(interval=1d,18根) /
利润表·资产负债表·现金流量表(period=quarterly,各4期) /
财务指标(report=2025-1,五类23项) / 估值快照 / 集合竞价 / 复权因子(5条) /
热榜(30条) / 涨停池(46只,trade_date=20260825) / 龙虎榜 / 指数快照(000300.SH) /
异动解读(anomaly-analysis-stock) / 短线风向标(auction/short-term-benchmark) /
基金持仓(fund/portfolio/holdings, fund_type=exchange + ETF 代码 510300.SH：
item[] 含 hold_ratio/position_capital/period_increase_rate_pct/investment_rank)

踩坑备注：
- `prices/historical` 的 start/end 是**毫秒时间戳**（非日期字符串）
- `financials/*` 必须传 `period=annual|quarterly`
- `financials/indicators` 的 `report` 格式为 `{yyyy}-{1|2|3|4}`，响应在 `data.abilities[]`
- 基金组端点普遍要求 `fund_type=exchange|otc|reits`，且是基金视角（thscode=基金代码）；OTC 基金代码格式未验证通过（110022 各后缀均报 3001 Fund not found）
- `anomaly-analysis-stock` / `valuations/snapshot` / `auction/snapshot` 都用批量参数 `thscodes`（传 `thscode` 报 code=1001）
- 短线风向标完整路径是 `/api/a-share/auction/short-term-benchmark`（不在 special-data 下）

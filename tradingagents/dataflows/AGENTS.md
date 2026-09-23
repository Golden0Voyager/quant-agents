# tradingagents/dataflows

数据源路由与封装层。所有对外数据获取从 `interface.py` 进入。

## 边界

- `interface.py` — 统一路由：yfinance / alpha_vantage / akshare（A 股走 Eastmoney）
- `akshare_vendor.py` — 实时抓取上游 akshare 接口；`smartmoney_vendor.py` — 读取本地
  `quant_core.db`（SQLite）：A 股走 `daily_bars`（vendor 名 `smartmoney_db`），美股/加密货币
  走 `global_assets_bars`（vendor 名 `quant_db_global`，单独注册是因为路由器对非 A 股
  ticker 按名字跳过 `smartmoney_db`）。本地 OHLCV 不新鲜（最新行落后请求日期超过
  `MAX_OHLCV_STALE_DAYS` 天）时自动 fallback 到线上厂商。排查数据缺失时先区分是
  DB 缺失还是上游接口失效
- `akshare_common.py` — 共享工具：`format_money_cn`、`to_akshare_symbol`、`no_proxy`
- `hithink_common.py` / `hithink_vendor.py` — 同花顺 HiThink Financial-API 客户端与 vendor
  （env `HITHINK_FINANCE_API_KEY`；三表/财务指标/热榜/龙虎榜/涨停池，链位在 smartmoney_db 之后、akshare 之前；异动原因 hithink 独占，预取注入 Sentiment prompt；估值快照为 market_data_validator 的 PE/PB/PS/PCF 交叉校验锚；集合竞价/短线风向标 hithink 独占，预取注入 Market Analyst prompt）
- `market_data_validator.py` — 数值声明的验证锚定（grounding）
- `news_gate.py` — Jev 新闻质量门控（env `TYPESAFE_API_KEY`，默认关闭；开启后对个股新闻做语义重要性判断，低分文章降级为仅列标题；全路径 fail-open，shadow 模式默认只记录不降级；详见 `docs/jev_news_gate_design.md`）

## 已知坑

- **MACD 字段语义**：指标字段遵循标准定义 `macd_dif`=DIF（快线）、`macd_dea`=DEA（慢线）、
  `macd_hist`=柱状图（(DIF-DEA)*2）。指标映射中 `'macd'` 必须映射到 `macd_dif`，
  `'macdh'`→`macd_hist`、`'macds'`→`macd_dea`；曾因 `'macd'` 误映射到 `macd_hist`
  导致 LLM 报告「MACD 数据冲突」
- akshare 调用注意 `no_proxy` 上下文；所有文件 I/O 使用 `encoding="utf-8"`
- **akshare 按票 crash 归一**：无质押记录（`stock_gpzy_...` TypeError）、非沪深港通标的
  （`stock_hsgt_individual_em` TypeError）、无研报覆盖（`stock_research_report_em` KeyError
  'infoCode'）时 akshare 库内部会崩而不是返回空表；vendor 层已将其归一为带明确语义的
  `NoMarketDataError`（"无质押记录/非标的/无覆盖，not a data outage"），别把这类
  NO_DATA 当成上游故障排查。个股新闻零直接命中（榜单快讯正文不点名）不再抛错，
  降级为仅列标题的市场背景区
- **东财热榜 partial 陷阱**:`fetch_eastmoney_hot_rank` 的表格端点（`stock_hot_rank_em`）
  2026-08 起频繁断连（反爬），表格挂时由 hithink hot-stock-list 补位当前排名行（双失败才
  保留 `<hot-rank table unavailable>` 占位符 → partial）；未进 top-100 是明确阴性信号，
  以普通文本渲染（不用尖括号占位符），避免被 `_eastmoney_payload` 误判 partial 稀释降级信号
- **板块名解析**：`get_sector_fund_flow` 的板块名经 `_resolve_sector_name`
  （精确→唯一双向子串→剥"行业/概念/板块"后缀→别名表 `_INDUSTRY_SECTOR_ALIASES`）解析；
  LLM 传概念名（"新能源汽车"）解析失败且有 `ticker` 时，按 `stock_list` 注册行业再解析一次。
  语义有歧义的映射（如 "农牧饲渔"）故意不收进别名表，走报错路径让 LLM 用可用板块名重试

## 验证

- `uv run python -m pytest tests/ -k "akshare or alpha_vantage or vendor" -m unit`
- 数据诊断脚本：`uv run python scripts/diagnose_ashare_data.py`

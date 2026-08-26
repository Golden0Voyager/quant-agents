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
  （env `HITHINK_FINANCE_API_KEY`；三表/财务指标/热榜/龙虎榜/涨停池，链位在 smartmoney_db 之后、akshare 之前；异动原因 hithink 独占，预取注入 Sentiment prompt）
- `market_data_validator.py` — 数值声明的验证锚定（grounding）

## 已知坑

- **MACD 字段语义**：指标字段遵循标准定义 `macd_dif`=DIF（快线）、`macd_dea`=DEA（慢线）、
  `macd_hist`=柱状图（(DIF-DEA)*2）。指标映射中 `'macd'` 必须映射到 `macd_dif`，
  `'macdh'`→`macd_hist`、`'macds'`→`macd_dea`；曾因 `'macd'` 误映射到 `macd_hist`
  导致 LLM 报告「MACD 数据冲突」
- akshare 调用注意 `no_proxy` 上下文；所有文件 I/O 使用 `encoding="utf-8"`

## 验证

- `uv run python -m pytest tests/ -k "akshare or alpha_vantage or vendor" -m unit`
- 数据诊断脚本：`uv run python scripts/diagnose_ashare_data.py`

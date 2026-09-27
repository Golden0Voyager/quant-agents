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
- `sw_industry_map.py` — 申万行业归属缓存（ticker → 申万一/二/三级），板块名解析的
  权威兜底；**只读本地 SQLite**，联网抓取只发生在显式刷新时
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
  （精确→唯一双向子串→剥"行业/概念/板块"后缀→**裸名优先消歧**→别名表
  `_INDUSTRY_SECTOR_ALIASES`）解析；裸名优先指候选多于一个但其中就有剥后缀的
  裸名时取它（"贸易行业"→"贸易"，而非与"石油加工贸易"一起判歧义）。
  LLM 传概念名（"新能源汽车"）解析失败且有 `ticker` 时，按 `stock_list` 注册行业再解析一次。
  语义有歧义的映射（如 "农牧饲渔"，旧门类下对应多个申万板块）故意不收进别名表。
  **解析失败必须抛 `NoMarketDataError` 而非 `RuntimeError`**：前者被 `route_to_vendor`
  归类为无数据并降级成 `NO_DATA_AVAILABLE` 文本（带候选板块名供 LLM 重试），
  后者会被当成真实故障、在 fallback 链耗尽后重新抛出，而 langgraph 的 `ToolNode`
  默认只吞 `ToolInvocationError`、其余异常直接 re-raise —— 这样一个板块名对不上
  就会中断整只 ticker 的图并让该票无报告落盘（20260926 批次 300175/001316 即此因）。
- **申万权威归属缓存**：`stock_list.industry` 是旧门类口径，无法映射到申万板块，而
  库里没有"个股→板块"成员表；`sw_industry_map` 用申万官方三级行业成分股反查权威归属
  （`scripts/refresh_sw_industry_map.py` 刷新，默认 `~/.tradingagents/cache/sw_industry_map.db`，
  env `TRADINGAGENTS_SW_INDUSTRY_MAP` 可改路径）。三条纪律：① 解析链为
  请求名 → `stock_list` 注册行业 → 申万二级 → 申万三级，每级只接受精确/唯一命中，
  全不中就降级，**绝不按语义相近挑板块**；② 输出带"请求 X → 实际板块 Y"标注供核对；
  ③ 上游（申万宏源页面）限流极重——并发 10 实测 324/335 行业失败，连续多轮探测会
  触发更长的冷却（连续 4 轮 + 5 次单发全被 `No tables found` 拦截），所以运行模型
  是"每次跑一点、跨多次累积"：默认并发压到 4、指数退避重试、`--budget-seconds`
  给单轮设上限、进度落在 `.tmp` 且**不删**（`already`/`pending` 在并入正式缓存之后
  才算，因此续跑只抓缺的行业）；`--sweep-delay 1.2 --workers 1` 是低负载礼貌模式；
  默认带 `industry_filter`，只抓"解析时用得上"的行业（申万二/三级名能解析到本地板块
  的那些，实测可省约 25% 请求，`--all-industries` 可关闭）。缓存缺失/未收录时解析链
  自动退化为"只认本地板块名 + 注册行业"，仍然不瞎映射
- **申万名带罗马数字**：官方层级名是 `白酒Ⅱ`/`白酒Ⅲ`、`贸易Ⅱ`/`国有大型银行Ⅱ`，
  本地 `sector_fund_flow` 是不带后缀的 `白酒`/`贸易`/`银行`。`_resolve_sector_name`
  比较前统一剥罗马数字（`_normalize_sector_name`），否则"白酒"会同时命中 `白酒Ⅱ` 和
  `白酒Ⅲ` 被误判歧义。归一化**只用于比较**，返回值始终是 `names` 里的原名（SQL 要用），
  且归一后若仍对应多个本地板块则继续报歧义——不把两个三级折叠成一个
- **板块名跨表口径不一致**：`sector_fund_flow`/`sector_daily` 存申万口径（90 个），
  两者交集只有 2 个；且 `get_sector_daily`/`get_sector_valuation` 仍是
  `WHERE sector_name = ?` 精确匹配、不走解析器，LLM 传"白酒行业"会静默拿到
  no data。DB 内也没有"个股→申万板块"成员表（`concept_member` 与板块名仅 2 个交集），
  所以跨口径只能靠名称映射，不存在确定性的自动桥接

## 验证

- `uv run python -m pytest tests/ -k "akshare or alpha_vantage or vendor" -m unit`
- 申万映射缓存：`uv run python -m pytest tests/test_sw_industry_map.py -m unit`
- 刷新申万缓存（只读本地缓存，联网只在此处）：`uv run python scripts/refresh_sw_industry_map.py`
  （`--check-only` 只看覆盖率与抽样映射，不联网；`--budget-seconds` 控制单轮时长；
  上游限流时**重复运行**即可逐步补齐，续跑只补缺的部分，不会重来）
- 数据诊断脚本：`uv run python scripts/diagnose_ashare_data.py`

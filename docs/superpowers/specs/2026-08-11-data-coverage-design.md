# 数据覆盖与可解释性增强设计

> 日期：2026-08-11
> 状态：待用户复核
> 范围：A 股批量分析的数据回退、已有本地数据接入、数据覆盖报告

## 1. 目标

提高 A 股批量分析的有效数据覆盖率，并让每个数据维度的缺失原因、来源和时效能够在报告中解释。系统必须继续允许可选数据缺失，不得因为新闻、公告、情绪或治理增强数据不可用而中止整只股票分析。

## 2. 现状与问题边界

1. `quant_core.db` 已有 `earnings_forecast`、`stock_pledge`、`margin_trading`、`north_hold` 等表，但部分 vendor adapter 没有正确读取这些表。
2. 新闻和公司公告目前不在本地数据库中，主要依赖 AkShare/Eastmoney、yfinance 等在线源。
3. 路由层以字符串 sentinel 表示 `NO_DATA` / `DATA_UNAVAILABLE`，可以继续运行，但缺少统一的来源、尝试链和时效摘要。
4. AkShare 上游接口可能返回空响应、格式变化或网络错误；重试必须有边界，不能通过无限扩大重试换取覆盖率。

## 3. 设计方案

### 3.1 本地优先适配修复

修复 `smartmoney_vendor`：

- `get_earnings_estimates()` 读取 `earnings_forecast`，按 `end_date <= curr_date` 返回最近记录。
- `get_pledge_ratio()` 使用实际的 `stock_pledge` schema（`stock_code`、`trade_date`、`pledge_amount`、`pledge_ratio` 等）。
- 保留 `north_hold` 的季度快照语义，在输出中标记 `frequency=quarterly_snapshot`，不伪装成日频数据。
- 现有 margin/fund-flow adapter 继续本地优先；单标的无记录时才进入外部回退。

### 3.2 外部来源扩展

先接入不增加新强制依赖的 CNINFO 路径：

- 公司公告优先尝试 CNINFO 公告接口，返回公告标题、日期、分类和原文链接。
- Eastmoney/AkShare 作为后备；接口失败时保留失败原因。

增加 Tushare 为可选 provider：

- 仅当存在 `TUSHARE_TOKEN` 且依赖可导入时启用。
- 首期覆盖公告、业绩预告、融资融券、股权质押和资金流向。
- 未配置 token、积分不足或接口不可用时返回 typed unavailable，不影响 batch 主流程。
- Tushare 不作为每次 LLM 调用的隐式强制网络请求；后续可接入 quant_pipeline 定时归档到 `quant_core.db`。

### 3.3 统一路由诊断

在 vendor 路由结果中保留以下信息：

```text
status: available | partial | no_data | failed | stale
method: get_company_announcements
source: smartmoney_db | cninfo | akshare | tushare
attempted_vendors: [...]
as_of: YYYY-MM-DD or null
reason: concise non-sensitive text
```

保持 `route_to_vendor()` 的旧 payload 接口兼容；诊断信息通过可选的 route context / state 汇总，不把结构化元数据拼进业务数据正文。

### 3.4 报告展示

在每只股票的报告中增加“数据覆盖与限制”区块，至少显示：

- 可用、部分可用、无数据、失败、过期的数据项；
- 最终来源与最新日期；
- 尝试过的来源及最终失败原因；
- 对该分析维度的影响等级：低 / 中 / 高。

`NO_DATA` 不再等同于网络错误：报告必须区分“来源没有覆盖”“本地没有归档”“在线请求失败”“数据过期”。模型提示继续要求不得根据缺失数据推测或编造。

## 4. 错误与回退策略

- 空结果、未覆盖标的：进入下一个 provider，不重试同一请求。
- JSONDecodeError、连接超时、临时 5xx：有限重试，单请求和单批次都有预算。
- 认证、权限、积分不足、接口 schema 不兼容：标记 provider unavailable，当前 batch 内熔断该 provider/方法。
- 核心行情和关键基本面失败：保持现有失败语义；可选增强数据失败：继续分析并进入覆盖报告。
- 不使用 yfinance 作为 A 股公告、质押、北向等语义数据的兜底来源；避免把不等价的数据源标记为成功。

## 5. 测试与验收

单元测试覆盖：

- 本地 `earnings_forecast` 与 `stock_pledge` schema 的读取；
- CNINFO/Tushare 未配置、成功、空结果、权限错误、网络错误；
- 多 provider 回退顺序和同批次熔断；
- `NO_DATA` 与 `failed` 的诊断区分；
- 报告覆盖区块的字段完整性和中文输出；
- 现有 payload-only vendor 调用保持兼容。

验收标准：

1. 当前 watchlist 中已有本地记录的业绩预告和质押数据不再被报告为无数据。
2. CNINFO/Tushare 未配置时，现有默认运行仍能完成，不产生 import/config 崩溃。
3. 一次 vendor 网络异常不会导致无限重试或整批静默等待。
4. 每只股票报告能回答“哪些数据缺失、为什么缺失、最终用了什么来源”。

## 6. 非目标

- 本次不直接抓取社交平台登录态数据。
- 本次不把所有新闻正文全文归档到数据库；先归档标准化元数据和链接。
- 本次不强制购买 Tushare、聚宽或其他商业数据权限。
- 本次不改变投资决策模型的权重，只增强输入覆盖和可解释性。

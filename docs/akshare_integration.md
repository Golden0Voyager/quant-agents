# A 股数据源（akshare）集成说明

## 概述

quant_agents 对 A 股 ticker（`.SS / .SZ / .BJ`）自动启用 akshare 作为首选
数据源，yfinance 仍保留为兜底。本次接入直接解决了
`docs/financial_data_errors_report.md` 中记录的 10× 单位错误、估值错误、
公司名幻觉等系统性问题（根因是 yfinance 对 A 股财报字段覆盖差）。

## 工作机制

`tradingagents/dataflows/interface.py:route_to_vendor()` 在每次调用前检查
传入的 ticker：

- **A 股**（`.SS / .SZ / .BJ`）→ akshare 优先；akshare 失败时回落到 yfinance
- **美股 / 港股 / 其他** → yfinance（行为不变）

无需任何配置改动。从用户视角看不到差异，唯一感知是：A 股报告里的财务
数字会更准确、单位会显式标注（亿 / 万）。

## 涉及函数

| 工具                 | akshare 实现 |
|----------------------|--------------|
| `get_stock_data`     | `ak.stock_zh_a_hist`（东财，前复权） |
| `get_indicators`     | 同上 + stockstats 计算 |
| `get_fundamentals`   | `ak.stock_individual_info_em` + `ak.stock_yjbb_em` |
| `get_balance_sheet`  | `ak.stock_balance_sheet_by_report_em` |
| `get_cashflow`       | `ak.stock_cash_flow_sheet_by_report_em` |
| `get_income_statement` | `ak.stock_profit_sheet_by_report_em` |

未走 akshare 的工具（A 股仍降级到 yfinance）：
- `get_news` / `get_global_news`
- `get_insider_transactions`（A 股本无此概念）

## 单位归一化

`tradingagents/dataflows/akshare_common.py` 是单位归一化的唯一来源：

- `to_yuan(value, source_unit)`：把 `wan` / `yi` / `yuan` 统一到 **元**
- `format_money_cn(value_yuan)`：自动按量级输出 `亿 / 万` 字符串

所有 vendor 函数返回的 markdown 文本中，**金额都带显式中文单位**，从源头
封堵 LLM 单位漂移导致的 10× 错误。

## 报告审计的真值对照

`scripts/report_auditor.py` 增加 `--cross-validate` 选项：

```bash
python scripts/report_auditor.py reports/batch_20260510_120902 --cross-validate
```

启用后，auditor 会对每只 A 股调用 `akshare_realtime.fetch_realtime_snapshot()`
获取实时 PE 和市值，与报告中的对应值比对，偏差 > 10% 标 `REALTIME-PE` /
`REALTIME-MCAP` ERROR。这把"自洽检查"升级为"事实校验"。

## 雪球 token（可选）

设置 `XUEQIU_TOKEN` 环境变量后，`akshare_realtime` 会优先从雪球获取盘中
价格和 PE/PB。未设置时，会优雅降级到东财快照（只有市值和公司名）。

## 已知限制

- 东财 push2 端点偶尔会对单 IP 限流几分钟。代码侧已实现指数退避，
  但若批量审计中 cross-validate 全部失败，建议稍后再跑。
- akshare 数据多数为收盘后 T+0/T+1 更新；交易日内拿到的财报数据
  可能滞后到上一个报告期。
- 当前不覆盖 A 股新闻 — quant_agents 的 news_data 仍走 yfinance。

## 验证方式

```bash
# 单元测试
uv run pytest -m unit -q

# 端到端 smoke
uv run python scripts/smoke_akshare.py 603893.SS

# 11 只股票回归（需联网）
uv run pytest -m integration tests/test_akshare_regression.py -v
```

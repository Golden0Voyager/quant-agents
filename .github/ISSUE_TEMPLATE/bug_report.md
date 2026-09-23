# Bug 报告 · Bug Report

感谢报告问题!请尽量填全以下信息,这能极大加速定位。

## 环境信息

- 操作系统:
- Python 版本:
- 安装方式:`uv pip install -e .` / pip
- `quant-agents` 版本或 commit:
- LLM Provider / 模型(如适用):

## 复现步骤

1.
2.
3.

## 期望行为

## 实际行为

(附上报错堆栈 / 输出片段,越多越好)

## 最小复现

如有最小示例脚本 / ticker + 日期,请贴出:

```bash
# 例如:
uv run tradingagents analyze --watchlist my-watchlist-1 --workers 1
```

## 相关日志 / 报告

- 失败报告路径(`reports/...`,`failures.log`)
- 是否跑过 `uv run python scripts/report_auditor.py reports/<batch_dir>`?

## 补充

(数据源相关?LLM 输出相关?测试相关?请勾选或说明)
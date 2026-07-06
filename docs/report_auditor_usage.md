# Report Auditor 使用文档

> 系统性审计股票分析报告中财务数据的逻辑一致性、量级合理性与交叉验证工具。

---

## 一、工具定位

Report Auditor 是 Trading Agents 报告生成系统的**数据质量守门员**。它在报告生成后（或批量修正后）自动扫描所有 Markdown 文件，提取关键财务指标，并执行多维度验证规则，确保数据在逻辑上自洽、量级上合理、跨文件一致。

---

## 二、功能特性

### 2.1 自动数据提取

从 Markdown 文件中自动识别并提取以下指标（支持列表项、表格、段落多种格式）：

| 类别 | 指标 |
|------|------|
| **估值** | PE(TTM)、Forward PE、PB、EPS(TTM)、预期 EPS、股价、市值 |
| **盈利** | 毛利率、营业利润率、净利率、ROE、ROA |
| **资产** | 总资产、流动资产、现金、存货、总负债、负债权益比、流动比率 |
| **现金流** | 经营现金流、自由现金流 |
| **损益** | TTM 营收、Q1 营收、TTM 净利润、Q1 净利润 |
| **成长** | 营收增长率、净利润增长率、EPS 增长率 |
| **其他** | 研发费用占比、股息率 |

### 2.2 验证规则引擎

| 规则 ID | 名称 | 严重程度 | 说明 |
|---------|------|---------|------|
| `MARGIN-001` | 利润率倒挂（营业 > 毛利） | CRITICAL | 营业利润率不得超过毛利率（容差 1%） |
| `MARGIN-002` | 利润率倒挂（净利 > 营业） | CRITICAL | 净利率不得超过营业利润率（容差 1%，>3% 差值为 CRITICAL） |
| `ASSET-001` | 流动资产 > 总资产 | CRITICAL | 流动资产不可超过总资产的 105% |
| `ASSET-002` | 现金+存货 > 流动资产 | WARNING | 现金与存货之和不应超过流动资产的 120% |
| `CASH-001` | 现金占比过高 | ERROR | 现金/总资产 > 90%，疑似 10x 单位错误 |
| `CASH-002` | 现金占比过低 | WARNING | 现金/总资产 < 0.1% 且总资产 > 10 亿 |
| `PE-001` | PE 计算不一致 | ERROR | 报告 PE 与 股价/EPS 偏差 > 15% |
| `PE-002` | Forward PE 不一致 | ERROR | 报告 Forward PE 与 股价/预期 EPS 偏差 > 15% |
| `PROFIT-001` | 净利润 > 营收 | CRITICAL | 净利润不可超过营收的 110% |
| `PROFIT-002` | 净利率计算不一致 | WARNING | 报告净利率与 净利润/营收 偏差 > 20% |
| `MAGNITUDE-001` | 资产量级异常 | WARNING | 总资产/现金 > 100 倍且总资产 > 1000 亿 |
| `MAGNITUDE-002` | 利润率量级异常 | WARNING | 净利率 > 50%，疑似 10x 单位错误 |
| `MCAP-001` | 市值不一致 | WARNING | 报告市值与 PE×净利润 偏差 > 30% |
| `CROSS-001` | 跨文件数据不一致 | ERROR | 同一指标在不同 MD 文件中偏差 > 20% |

### 2.3 输出报告

每次审计生成两份报告：

- **Markdown 报告** (`audit_report_YYYYMMDD_HHMMSS.md`)：人可读的问题清单与汇总
- **JSON 报告** (`audit_report_YYYYMMDD_HHMMSS.json`)：结构化数据，可用于 CI/CD 流水线或自动化处理

---

## 三、环境要求

- **Python**: 3.8+
- **依赖**: 仅使用标准库（`re`, `json`, `argparse`, `pathlib`, `dataclasses`, `datetime`），无需额外安装

---

## 四、使用方法

### 4.1 基本用法

```bash
cd /Users/hainingyu/Code/quant_agents
python3 scripts/report_auditor.py reports/batch_20260510_120902
```

输出示例：

```
🔍 开始审计: reports/batch_20260510_120902

📊 审计完成!
   股票数: 11
   文件数: 237
   CRITICAL: 0
   ERROR: 0
   WARNING: 0
   健康评分: 100/100

📄 报告已生成: reports/audit_output/audit_report_20260511_181905.md
```

### 4.2 只审计指定股票

```bash
python3 scripts/report_auditor.py reports/batch_20260510_120902 --ticker 603893
```

### 4.3 指定输出目录

```bash
python3 scripts/report_auditor.py reports/batch_20260510_120902 --output ./my_audit_reports
```

### 4.4 完整参数说明

```
usage: report_auditor.py [-h] [--ticker TICKER] [--output OUTPUT] batch_dir

positional arguments:
  batch_dir             报告批次目录路径

optional arguments:
  -h, --help            显示帮助信息
  --ticker TICKER       只审计指定股票代码
  --output OUTPUT, -o OUTPUT
                        报告输出目录 (默认: ./audit_reports)
```

---

## 五、退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 无 CRITICAL 问题，数据质量通过 |
| `1` | 存在 CRITICAL 问题，需要立即修复 |

可用于 CI/CD 流水线中的质量门禁：

```yaml
# GitHub Actions 示例
- name: Audit Financial Reports
  run: |
    python3 scripts/report_auditor.py reports/batch_${{ github.run_id }}
```

---

## 六、报告解读

### 6.1 Markdown 报告结构

```markdown
# 财务报告审计报告

## 汇总统计
| 指标 | 数值 |
| 审计股票数 | 11 |
| 审计文件数 | 237 |
| CRITICAL | 0 |
| ERROR | 0 |
| WARNING | 0 |
| 健康评分 | 100/100 |

## CRITICAL 问题（必须立即修复）
### [603893] MARGIN-001: 营业利润率(49.30%) > 毛利率(41.30%)，超出1%容差，逻辑矛盾
- **文件**: `reports/.../603893/1_analysts/fundamentals.md`
- **预期**: 毛利率(41.30%) >= 营业利润率(49.30%)
- **实际**: 营业利润率(49.30%) > 毛利率(41.30%)
- **建议**: 检查营业利润率是否被高估或毛利率被低估

## 各股票审计详情
### 603893
- 审计文件数: 22
- CRITICAL: 1
- ERROR: 0
- WARNING: 0
```

### 6.2 JSON 报告结构

```json
{
  "audit_time": "2026-05-11T18:19:05",
  "batch_dir": "reports/batch_20260510_120902",
  "summary": {
    "total_tickers": 11,
    "total_files_audited": 237,
    "total_critical": 0,
    "total_errors": 0,
    "total_warnings": 0,
    "health_score": 100
  },
  "results": {
    "603893": {
      "ticker": "603893",
      "files_audited": 22,
      "critical": 0,
      "errors": 0,
      "warnings": 0,
      "issues": []
    }
  }
}
```

---

## 七、常见问题

### Q1: 为什么某些指标没有被提取到？

脚本使用正则表达式匹配文本，如果报告中使用了非常规的格式，可能导致提取失败。目前支持的格式包括：

- 列表项：`- **市盈率(TTM)**：约63.29倍`
- 表格：`| 市盈率(TTM) | 约63.29倍 | 偏高 |`
- 段落：`公司当前市值为750.6亿元人民币，市盈率(TTM)为约63.29倍`

如果使用了其他格式，可以修改 `MetricsExtractor.PATTERNS` 添加新的匹配模式。

### Q2: 健康评分是如何计算的？

```
健康评分 = 100 - CRITICAL数×10 - ERROR数×5 - WARNING数×1
最低不低于 0
```

### Q3: 如何处理"净利率略高于营业利润率"的合法情况？

如果公司存在显著的营业外收入，净利率可能略高于营业利润率。脚本设置了 1% 的容差阈值：

- 差异 ≤ 1%：不触发告警
- 1% < 差异 ≤ 3%：WARNING
- 差异 > 3%：CRITICAL

### Q4: 如何扩展新的验证规则？

在 `ValidationRules` 类中添加新的静态方法：

```python
@staticmethod
def check_new_rule(metrics: FinancialMetrics) -> Optional[AuditIssue]:
    if metrics.some_field is not None and metrics.another_field is not None:
        if 某些条件:
            return AuditIssue(
                severity="ERROR",
                rule_id="NEW-001",
                message="问题描述",
                ticker=metrics.ticker,
                file_path=metrics.file_path,
                expected="预期值",
                actual="实际值",
                suggestion="修复建议"
            )
    return None
```

然后在 `run_all()` 方法的 `rules` 列表中添加新方法。

---

## 八、维护者信息

- **脚本位置**: `scripts/report_auditor.py`
- **报告输出**: `reports/audit_output/`
- **最后更新**: 2026-05-11

---

## 九、路线图

- [ ] 接入 `query_stock` 实时股价验证
- [ ] 增加行业特定阈值（银行、保险、地产等）
- [ ] 支持历史数据趋势检查（如营收环比下降 50% 触发告警）
- [ ] 生成数据修正建议（自动计算正确值）

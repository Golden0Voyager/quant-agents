#!/usr/bin/env python3
"""
Trading Agents Report Auditor
==============================
系统性审计股票分析报告中财务数据的：
1. 逻辑一致性（利润率链条、资产负债平衡）
2. 量级合理性（识别10x放大/缩小错误）
3. 交叉验证（营收×毛利率≈毛利，股价/EPS≈PE等）
4. 跨文件一致性（同一股票不同层级文件数据是否一致）

Usage:
    python report_auditor.py /path/to/batch_dir [--ticker TICKER] [--output OUTPUT_DIR]
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tradingagents.reporting import (
    DATA_RELIABILITY_KEYS,
    aggregate_data_reliability,
)


def _nonnegative_int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0

# =============================================================================
# 数据模型
# =============================================================================

@dataclass
class FinancialMetrics:
    """从MD文件中提取的核心财务指标"""
    ticker: str = ""
    file_path: str = ""

    # 估值指标
    pe_ttm: float | None = None
    pe_forward: float | None = None
    pb: float | None = None
    eps_ttm: float | None = None
    eps_expected: float | None = None
    price: float | None = None
    market_cap: float | None = None

    # 盈利能力
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    roe: float | None = None
    roa: float | None = None

    # 资产负债表
    total_assets: float | None = None
    current_assets: float | None = None
    cash: float | None = None
    inventory: float | None = None
    total_debt: float | None = None
    debt_to_equity: float | None = None
    current_ratio: float | None = None

    # 现金流
    operating_cash_flow: float | None = None
    free_cash_flow: float | None = None

    # 损益表
    revenue_ttm: float | None = None
    revenue_q1: float | None = None
    net_profit_ttm: float | None = None
    net_profit_q1: float | None = None

    # 成长性
    revenue_growth: float | None = None
    profit_growth: float | None = None
    eps_growth: float | None = None

    # 运营效率
    rd_ratio: float | None = None
    dividend_yield: float | None = None

    # 元数据
    company_name: str = ""


@dataclass
class AuditIssue:
    """审计发现的问题"""
    severity: str
    rule_id: str
    message: str
    ticker: str
    file_path: str
    expected: str | None = None
    actual: str | None = None
    suggestion: str | None = None

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "rule_id": self.rule_id,
            "message": self.message,
            "ticker": self.ticker,
            "file_path": self.file_path,
            "expected": self.expected,
            "actual": self.actual,
            "suggestion": self.suggestion,
        }


@dataclass
class AuditResult:
    """单只股票的审计结果"""
    ticker: str
    files_audited: int = 0
    issues: list[AuditIssue] = field(default_factory=list)
    metrics: dict[str, FinancialMetrics] = field(default_factory=dict)
    data_reliability: dict[str, int] = field(default_factory=dict)

    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "CRITICAL")

    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "ERROR")

    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "WARNING")


# =============================================================================
# 数据提取器
# =============================================================================

class MetricsExtractor:
    """增强版财务指标提取器，支持多种Markdown格式"""

    # 核心指标提取规则（支持列表项、表格、文本段落多种格式）
    # 每种指标可以有多个正则模式，按优先级匹配
    PATTERNS = {
        "pe_ttm": [
            # 列表项格式: - **TTM市盈率**：约63.29倍
            r"(?:TTM)?市盈率\(TTM\)[为是:]?(?:约|约为|about)?([\d.]+)(?:倍|x)",
            # 表格格式: | TTM市盈率 | 约63.29倍 | 偏高 |
            r"\|\s*(?:TTM)?市盈率\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*(?:倍|x)\s*\|",
            # 英文格式
            r"P/E\s*[\(（]\s*TTM\s*[\)）][:：\s]*(?:about)?\s*([\d.]+)\s*(?:x|倍)",
            # 紧凑英文: PE(TTM) 24.34 倍 / PE(TTM) 9.76x
            r"PE\s*[\(（]\s*TTM\s*[\)）]\s*[:：]?\s*(?:约|约为|about)?\s*([\d.]+)\s*(?:倍|x)",
        ],
        "pe_forward": [
            r"(?:预期|远期)市盈率[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*(?:倍|x)",
            r"\|\s*(?:预期|远期)市盈率\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*(?:倍|x)\s*\|",
            r"forward\s+P/E[:：\s]*(?:about)?\s*([\d.]+)",
        ],
        "pb": [
            r"市净率(?:\s*[\(（]\s*PB\s*[\)）]\s*)?[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*(?:倍|x)",
            r"\|\s*市净率\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*(?:倍|x)\s*\|",
        ],
        "eps_ttm": [
            r"(?:TTM)?每股收益(?:\(\s*EPS\s*\))?[:为]?(?:约|约为|about)?([\-\d.]+)元",
            r"\|\s*每股收益\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*元\s*\|",
        ],
        "eps_expected": [
            r"(?:机构预测|预期|预计|expected)\s*(?:2026年)?\s*EPS[:：\s]*(?:约|约为|about)?\s*([\d.]+(?:\s*-\s*[\d.]+)?)",
            r"预测\s*EPS[:：\s]*(?:仅|约|约为|about)?\s*([\d.]+(?:\s*-\s*[\d.]+)?)",
            r"\|\s*预期EPS\s*\|\s*(?:约|约为|about)?\s*([\d.]+(?:\s*-\s*[\d.]+)?)\s*\|",
            # 研报口径: "2026E EPS 在 2.85（东吴 08/19）…"、"下调了2026E EPS至2.85"
            r"2026E\s*EPS\s*[^0-9%]{0,8}(\d+\.\d+)",
        ],
        "price": [
            r"当前股价[:：\s]*[\(（]?([\d.]+)[\)）]?",
            r"Current\s+Price[:：\s]*[\(（]?([\d.]+)[\)）]?",
        ],
        "market_cap": [
            r"(?<!持股)(?<!板块)(?<!行业)公司?总?市值[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)",
            r"market\s+cap[:：\s]*(?:about)?\s*([\d.]+)\s*(?:billion|B)",
        ],
        "gross_margin": [
            r"毛利率[:为]?\s*(?:约|约为|about)?\s*([\d.]+)\s*%",
            r"\|\s*毛利率\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*%\s*\|",
            r"gross\s+margin[:：\s]*(?:about)?\s*([\d.]+)\s*%",
        ],
        "operating_margin": [
            r"营业利润率[:为]?\s*(?:约|约为|about)?\s*([\-\d.]+)\s*%",
            r"\|\s*营业利润率\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*%\s*\|",
            r"operating\s+margin[:：\s]*(?:about)?\s*([\d.]+)\s*%",
        ],
        "net_margin": [
            r"净利润率[:为]?\s*(?:约|约为|about)?\s*([\-\d.]+)\s*%",
            r"\|\s*净利润率\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*%\s*\|",
            r"net\s+profit\s+margin[:：\s]*(?:about)?\s*([\d.]+)\s*%",
        ],
        "roe": [
            r"(?:ROE|股本回报率)[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*%",
            r"\|\s*ROE\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*%\s*\|",
        ],
        "roa": [
            r"(?:ROA|资产回报率)[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*%",
            r"\|\s*ROA\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*%\s*\|",
        ],
        "total_assets": [
            r"总资产[:为]?(?:约|约为|about)?([\d.]+)(?:亿元|亿)",
            r"\|\s*总资产\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)\s*\|",
            r"total\s+assets[:：\s]*(?:about)?\s*([\d.]+)\s*(?:billion|B)",
        ],
        "current_assets": [
            r"流动资产[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)",
            r"\|\s*流动资产\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)\s*\|",
        ],
        "cash": [
            r"现金及(?:现金)?等价物[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)",
            r"\|\s*现金及等价物\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)\s*\|",
            r"Cash\s+(?:and\s+)?(?:Equivalents)?[:：\s]*(?:about)?\s*([\d.]+)\s*(?:billion|B)",
        ],
        "inventory": [
            r"(?:存货|库存)[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)",
            r"\|\s*存货\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)\s*\|",
            r"inventory[:：\s]*(?:about)?\s*([\d.]+)\s*(?:billion|B)",
        ],
        "total_debt": [
            r"总负债[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)",
            r"\|\s*总负债\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)\s*\|",
        ],
        "debt_to_equity": [
            r"(?:负债权益比|Debt-to-Equity)[:：\s]*(?:约|约为|about)?\s*([\d.]+)",
            r"\|\s*负债权益比\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*\|",
        ],
        "current_ratio": [
            r"(?:流动比率|Current\s+Ratio)[:：\s]*(?:约|约为|about)?\s*([\d.]+)",
            r"\|\s*流动比率\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*\|",
        ],
        "operating_cash_flow": [
            r"(?:经营|经营活动)现金流[:：\s]*(?:约|约为|about)?\s*([\-\d.]+)\s*(?:亿元|亿)",
            r"\|\s*经营现金流\s*\|\s*(?:约|约为|about)?\s*([\-\d.]+)\s*(?:亿元|亿)\s*\|",
        ],
        "free_cash_flow": [
            r"(?:自由现金流|Free\s+Cash\s+Flow|FCF)[:：\s]*(?:约|约为|about)?\s*([\-\d.]+)\s*(?:亿元|亿)",
            r"\|\s*自由现金流\s*\|\s*(?:约|约为|about)?\s*([\-\d.]+)\s*(?:亿元|亿)\s*\|",
        ],
        "revenue_ttm": [
            r"(?:收入|营收)\(\s*TTM\s*\)[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)",
            r"\|\s*收入\(TTM\)\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)\s*\|",
        ],
        "revenue_q1": [
            r"Q1(?:季度)?(?:营收|收入)[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)",
        ],
        "net_profit_ttm": [
            r"净利润\(\s*TTM\s*\)[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)",
            r"\|\s*净利润\(TTM\)\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)\s*\|",
        ],
        "net_profit_q1": [
            r"Q1(?:季度)?净利润[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)",
        ],
        "revenue_growth": [
            r"(?:营收|收入)增长[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*%",
        ],
        "profit_growth": [
            r"净利润增长[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*%",
        ],
        "eps_growth": [
            r"(?:EPS增长|每股收益增长)[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*%",
            r"\|\s*预期EPS增长\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*%\s*\|",
        ],
        "rd_ratio": [
            r"(?:研发费用|研发投入)[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)",
        ],
        "dividend_yield": [
            r"(?:股息率|dividend\s+yield)[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*%",
        ],
    }

    @staticmethod
    def _preprocess_text(text: str) -> str:
        """预处理文本：移除Markdown格式标记，标准化分隔符"""
        # 移除Markdown粗体标记
        text = re.sub(r'\*\*', '', text)
        # 标准化冒号（全角冒号转半角，并去除多余空格）
        text = re.sub(r'[:：]\s*', ':', text)
        return text

    def extract(self, text: str, ticker: str, file_path: str) -> tuple[FinancialMetrics, list[AuditIssue]]:
        """从文本中提取所有指标，返回 (指标, 提取过程发现的问题)。

        同一指标在文中多次出现时取首次出现（通常是分析师段落的原始数据，
        避免取到后文 PM 转述的二手数字），并记录 WARNING。
        """
        text = self._preprocess_text(text)
        metrics = FinancialMetrics(ticker=ticker, file_path=file_path)
        issues: list[AuditIssue] = []

        for field_name, patterns in self.PATTERNS.items():
            for pattern in patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                if matches:
                    value_str = matches[0]
                    if isinstance(value_str, tuple):
                        value_str = value_str[0]

                    parsed = self._parse_value(value_str, field_name)
                    if parsed is not None:
                        setattr(metrics, field_name, parsed)
                        if len(matches) > 1:
                            issues.append(AuditIssue(
                                severity="WARNING",
                                rule_id="EXTRACT-002",
                                message=f"指标'{field_name}'在文件中出现{len(matches)}次，已取首次出现值{parsed}",
                                ticker=ticker,
                                file_path=file_path,
                                suggestion="确认首次出现为分析师原始数据，而非后文转述值"
                            ))
                        break

        # 提取公司名称
        name_match = re.search(r'#\s*(.+?)[\(（]\d{6}', text)
        if name_match:
            metrics.company_name = name_match.group(1).strip()

        return metrics, issues

    def _parse_value(self, value_str: str, field_name: str) -> float | None:
        """解析数值字符串"""
        try:
            value_str = value_str.strip().replace(' ', '')

            # 处理范围值，取中值
            if '-' in value_str and not value_str.startswith('-'):
                parts = value_str.split('-')
                if len(parts) == 2:
                    low = float(parts[0])
                    high = float(parts[1])
                    return (low + high) / 2

            return float(value_str)
        except (ValueError, TypeError):
            return None


# =============================================================================
# 验证规则引擎
# =============================================================================

class ValidationRules:
    """数据验证规则集合"""

    @staticmethod
    def check_margin_hierarchy(metrics: FinancialMetrics) -> AuditIssue | None:
        """规则1: 毛利率 >= 营业利润率 >= 净利率"""
        gm = metrics.gross_margin
        om = metrics.operating_margin
        nm = metrics.net_margin

        if gm is not None and om is not None and gm < om - 1.0:  # 1%容差
            return AuditIssue(
                severity="CRITICAL",
                rule_id="MARGIN-001",
                message=f"营业利润率({om:.2f}%) > 毛利率({gm:.2f}%)，超出1%容差，逻辑矛盾",
                ticker=metrics.ticker,
                file_path=metrics.file_path,
                expected=f"毛利率({gm:.2f}%) >= 营业利润率({om:.2f}%)",
                actual=f"营业利润率({om:.2f}%) > 毛利率({gm:.2f}%)",
                suggestion="检查营业利润率是否被高估或毛利率被低估"
            )

        if om is not None and nm is not None and om < nm - 1.0:  # 1%容差
            return AuditIssue(
                severity="CRITICAL" if nm - om > 3.0 else "WARNING",
                rule_id="MARGIN-002",
                message=f"净利率({nm:.2f}%) > 营业利润率({om:.2f}%)，逻辑矛盾",
                ticker=metrics.ticker,
                file_path=metrics.file_path,
                expected=f"营业利润率({om:.2f}%) >= 净利率({nm:.2f}%)",
                actual=f"净利率({nm:.2f}%) > 营业利润率({om:.2f}%)",
                suggestion="检查是否有营业外收入导致，否则数据有误"
            )
        return None

    @staticmethod
    def check_asset_composition(metrics: FinancialMetrics) -> AuditIssue | None:
        """规则2: 现金 + 存货 <= 流动资产 <= 总资产"""
        ta = metrics.total_assets
        ca = metrics.current_assets
        cash = metrics.cash
        inv = metrics.inventory

        if ta is not None and ca is not None and ca > ta * 1.05:
            return AuditIssue(
                severity="CRITICAL",
                rule_id="ASSET-001",
                message=f"流动资产({ca:.2f}亿) > 总资产({ta:.2f}亿)，逻辑不可能",
                ticker=metrics.ticker,
                file_path=metrics.file_path,
                expected=f"流动资产 <= 总资产({ta:.2f}亿)",
                actual=f"流动资产 = {ca:.2f}亿",
                suggestion="检查总资产是否被低估或流动资产被高估（可能是10x单位错误）"
            )

        if ca is not None and cash is not None and inv is not None and cash + inv > ca * 1.2:
                return AuditIssue(
                    severity="WARNING",
                    rule_id="ASSET-002",
                    message=f"现金({cash:.2f}亿) + 存货({inv:.2f}亿) > 流动资产({ca:.2f}亿)的120%",
                    ticker=metrics.ticker,
                    file_path=metrics.file_path,
                    expected=f"现金 + 存货 <= 流动资产({ca:.2f}亿)",
                    actual=f"现金 + 存货 = {cash + inv:.2f}亿",
                    suggestion="检查流动资产是否被低估"
                )
        return None

    @staticmethod
    def check_cash_reasonableness(metrics: FinancialMetrics) -> AuditIssue | None:
        """规则3: 现金/总资产 合理性检查"""
        ta = metrics.total_assets
        cash = metrics.cash

        if ta is not None and cash is not None and ta > 0:
            ratio = cash / ta
            if ratio > 0.9:
                return AuditIssue(
                    severity="ERROR",
                    rule_id="CASH-001",
                    message=f"现金占总资产比例高达{ratio:.1%}，异常偏高",
                    ticker=metrics.ticker,
                    file_path=metrics.file_path,
                    expected="现金/总资产 < 80%",
                    actual=f"现金/总资产 = {ratio:.1%}",
                    suggestion="可能是现金被高估或总资产被低估（10x单位错误常见症状）"
                )
            if ratio < 0.001 and ta > 10:
                return AuditIssue(
                    severity="WARNING",
                    rule_id="CASH-002",
                    message=f"现金占总资产比例仅{ratio:.2%}，异常偏低",
                    ticker=metrics.ticker,
                    file_path=metrics.file_path,
                    expected="现金/总资产 > 0.1%",
                    actual=f"现金/总资产 = {ratio:.2%}",
                    suggestion="检查现金数据是否缺失或总资产被高估"
                )
        return None

    @staticmethod
    def check_pe_consistency(metrics: FinancialMetrics) -> AuditIssue | None:
        """规则4: PE = 股价 / EPS 一致性"""
        price = metrics.price
        eps = metrics.eps_ttm
        pe = metrics.pe_ttm

        if price is not None and eps is not None and eps > 0 and pe is not None:
            calculated_pe = price / eps
            deviation = abs(calculated_pe - pe) / pe if pe != 0 else float('inf')

            if deviation > 0.15:
                return AuditIssue(
                    severity="ERROR",
                    rule_id="PE-001",
                    message=f"PE计算不一致：报告PE={pe:.2f}x，股价({price})/EPS({eps})={calculated_pe:.2f}x",
                    ticker=metrics.ticker,
                    file_path=metrics.file_path,
                    expected=f"PE ≈ {calculated_pe:.2f}x",
                    actual=f"PE = {pe:.2f}x",
                    suggestion=f"检查股价或EPS数据，偏差达{deviation:.1%}"
                )

        eps_fwd = metrics.eps_expected
        pe_fwd = metrics.pe_forward
        if price is not None and eps_fwd is not None and eps_fwd > 0 and pe_fwd is not None:
            calculated_fwd_pe = price / eps_fwd
            deviation = abs(calculated_fwd_pe - pe_fwd) / pe_fwd if pe_fwd != 0 else float('inf')

            if deviation > 0.15:
                return AuditIssue(
                    severity="ERROR",
                    rule_id="PE-002",
                    message=f"Forward PE不一致：报告={pe_fwd:.2f}x，股价({price})/预期EPS({eps_fwd})={calculated_fwd_pe:.2f}x",
                    ticker=metrics.ticker,
                    file_path=metrics.file_path,
                    expected=f"Forward PE ≈ {calculated_fwd_pe:.2f}x",
                    actual=f"Forward PE = {pe_fwd:.2f}x",
                    suggestion="检查预期EPS或Forward PE数据"
                )
        return None

    @staticmethod
    def check_revenue_profit_scale(metrics: FinancialMetrics) -> AuditIssue | None:
        """规则5: 净利润 <= 营收"""
        rev = metrics.revenue_ttm
        profit = metrics.net_profit_ttm

        if rev is not None and profit is not None and profit > rev * 1.1:
            return AuditIssue(
                severity="CRITICAL",
                rule_id="PROFIT-001",
                message=f"净利润({profit:.2f}亿) > 营收({rev:.2f}亿)，逻辑不可能",
                ticker=metrics.ticker,
                file_path=metrics.file_path,
                expected=f"净利润 <= 营收({rev:.2f}亿)",
                actual=f"净利润 = {profit:.2f}亿",
                suggestion="净利润数据被高估或营收被低估（常见10x单位错误）"
            )

        if rev is not None and profit is not None and rev > 0:
            implied_nm = profit / rev * 100
            reported_nm = metrics.net_margin
            if reported_nm is not None:
                deviation = abs(implied_nm - reported_nm) / reported_nm if reported_nm != 0 else float('inf')
                if deviation > 0.2:
                    return AuditIssue(
                        severity="WARNING",
                        rule_id="PROFIT-002",
                        message=f"净利率不一致：报告={reported_nm:.2f}%，但净利润/营收={implied_nm:.2f}%",
                        ticker=metrics.ticker,
                        file_path=metrics.file_path,
                        expected=f"净利率 ≈ {implied_nm:.2f}%",
                        actual=f"净利率 = {reported_nm:.2f}%",
                        suggestion="检查净利润、营收或净利率数据的一致性"
                    )
        return None

    @staticmethod
    def check_10x_magnification(metrics: FinancialMetrics) -> AuditIssue | None:
        """规则6: 检测可能的10x放大/缩小错误"""
        ta = metrics.total_assets
        cash = metrics.cash

        if ta is not None and cash is not None and cash > 0:
            ratio = ta / cash
            if ratio > 100 and ta > 1000:
                return AuditIssue(
                    severity="WARNING",
                    rule_id="MAGNITUDE-001",
                    message=f"总资产({ta:.2f}亿)是现金({cash:.2f}亿)的{ratio:.0f}倍，量级异常",
                    ticker=metrics.ticker,
                    file_path=metrics.file_path,
                    expected="总资产/现金 通常在2-50倍之间",
                    actual=f"总资产/现金 = {ratio:.0f}倍",
                    suggestion="总资产可能存在10x放大错误，或现金存在10x缩小错误"
                )

        rev = metrics.revenue_ttm
        profit = metrics.net_profit_ttm
        if rev is not None and profit is not None and rev > 0:
            margin = profit / rev
            if margin > 0.5:
                return AuditIssue(
                    severity="WARNING",
                    rule_id="MAGNITUDE-002",
                    message=f"TTM净利润/营收比例高达{margin:.1%}，异常偏高",
                    ticker=metrics.ticker,
                    file_path=metrics.file_path,
                    expected="净利率通常在0-40%",
                    actual=f"净利率 = {margin:.1%}",
                    suggestion="可能是营收被低估或净利润被高估（10x单位错误常见症状）"
                )
        return None

    @staticmethod
    def check_market_cap_consistency(metrics: FinancialMetrics) -> AuditIssue | None:
        """规则7: 市值 = PE * 净利润 一致性"""
        mc = metrics.market_cap
        pe = metrics.pe_ttm
        profit = metrics.net_profit_ttm

        if mc is not None and pe is not None and profit is not None:
            estimated_mc = pe * profit
            if estimated_mc > 0:
                deviation = abs(estimated_mc - mc) / estimated_mc
                if deviation > 0.3:
                    return AuditIssue(
                        severity="WARNING",
                        rule_id="MCAP-001",
                        message=f"市值不一致：报告={mc:.2f}亿，PE({pe})×净利润({profit})={estimated_mc:.2f}亿",
                        ticker=metrics.ticker,
                        file_path=metrics.file_path,
                        expected=f"市值 ≈ {estimated_mc:.2f}亿",
                        actual=f"市值 = {mc:.2f}亿",
                        suggestion="检查市值、PE或净利润数据的一致性"
                    )
        return None

    @staticmethod
    def check_realtime_pe(metrics: FinancialMetrics, snapshot: dict | None) -> AuditIssue | None:
        """规则 REALTIME-PE: 报告 PE 与 akshare 实时真值偏差 > 10% 标 ERROR。"""
        if not snapshot:
            return None
        reported = metrics.pe_ttm
        truth = snapshot.get("pe_ttm")
        if reported is None or truth is None or truth == 0:
            return None
        deviation = abs(reported - truth) / abs(truth)
        if deviation > 0.10:
            return AuditIssue(
                severity="ERROR",
                rule_id="REALTIME-PE",
                message=f"报告 PE={reported:.2f}x 与真值 {truth:.2f}x 偏差 {deviation:.1%}",
                ticker=metrics.ticker,
                file_path=metrics.file_path,
                expected=f"PE ≈ {truth:.2f}x (akshare 真值)",
                actual=f"PE = {reported:.2f}x",
                suggestion="核对报告中 PE 数值；若 LLM 误抄，检查源数据格式",
            )
        return None

    @staticmethod
    def check_realtime_market_cap(metrics: FinancialMetrics, snapshot: dict | None) -> AuditIssue | None:
        """规则 REALTIME-MCAP: 报告市值与 akshare 真值偏差 > 10% 标 ERROR。"""
        if not snapshot:
            return None
        reported = metrics.market_cap
        truth = snapshot.get("market_cap_yi")
        if reported is None or truth is None or truth == 0:
            return None
        deviation = abs(reported - truth) / abs(truth)
        if deviation > 0.10:
            return AuditIssue(
                severity="ERROR",
                rule_id="REALTIME-MCAP",
                message=f"报告市值 {reported:.2f}亿 vs 真值 {truth:.2f}亿，偏差 {deviation:.1%}",
                ticker=metrics.ticker,
                file_path=metrics.file_path,
                expected=f"{truth:.2f}亿",
                actual=f"{reported:.2f}亿",
                suggestion="可能存在 10× 单位错误（万/亿混用）",
            )
        return None

    @classmethod
    def run_all(cls, metrics: FinancialMetrics) -> list[AuditIssue]:
        """运行所有验证规则"""
        issues = []
        rules = [
            cls.check_margin_hierarchy,
            cls.check_asset_composition,
            cls.check_cash_reasonableness,
            cls.check_pe_consistency,
            cls.check_revenue_profit_scale,
            cls.check_10x_magnification,
            cls.check_market_cap_consistency,
        ]
        for rule in rules:
            issue = rule(metrics)
            if issue is not None:
                issues.append(issue)
        return issues


# =============================================================================
# 跨文件一致性检查
# =============================================================================

def check_cross_file_consistency(results: dict[str, AuditResult]) -> list[AuditIssue]:
    """检查同一股票在不同文件中的数据一致性"""
    issues = []

    for ticker, result in results.items():
        if len(result.metrics) < 2:
            continue

        metric_values = defaultdict(list)
        for file_path, metrics in result.metrics.items():
            for field_name, value in asdict(metrics).items():
                if value is not None and field_name not in ('ticker', 'file_path', 'company_name'):
                    metric_values[field_name].append((file_path, value))

        for field_name, values in metric_values.items():
            if len(values) < 2:
                continue

            vals = [v[1] for v in values]
            max_val = max(vals)
            min_val = min(vals)

            if min_val == 0:
                continue

            deviation = (max_val - min_val) / abs(min_val)
            if deviation > 0.2:
                files_str = ", ".join([f"{os.path.basename(v[0])}={v[1]}" for v in values])
                issues.append(AuditIssue(
                    severity="ERROR",
                    rule_id="CROSS-001",
                    message=f"同一指标'{field_name}'在不同文件中偏差达{deviation:.1%}",
                    ticker=ticker,
                    file_path=result.metrics[values[0][0]].file_path,
                    expected=f"各文件{field_name}应一致",
                    actual=files_str,
                    suggestion="检查跨文件数据同步性"
                ))

    return issues


def _normalize_rating_standalone(raw: str) -> str | None:
    """Standalone 5-tier rating normalizer used when tradingagents is unavailable."""
    s = str(raw or "").strip().strip("*:.,`'\" “”")
    lower = s.lower()
    for label in ("buy", "overweight", "hold", "underweight", "sell"):
        if lower == label:
            return label.capitalize()
    cn_map = {"买入": "Buy", "增持": "Overweight", "持有": "Hold", "减持": "Underweight", "卖出": "Sell"}
    return cn_map.get(s)


def check_pm_summary_consistency(batch_dir: str | Path) -> list[AuditIssue]:
    """PM 最终决策 (5_portfolio/decision.md) 与 batch_summary.json 的一致性。

    修复 20260801_batch_my 暴露的审计盲区：旧版 _parse_summary_fields 把 PM
    反引号字段（``**评级：** `Underweight```）误判成 ``Hold``，并回退 trader
    的止损数值覆盖 PM 的显式拒绝（``止损价：不适用（清仓操作无需设置止损）``）。

    规则：
    - PMSUM-001: PM 评级与 summary 评级不一致 (ERROR)
    - PMSUM-002: PM 止损与 summary 止损不一致，或 PM 显式不设止损却被
      summary 写入 trader 数值 (ERROR)

    缺少 batch_summary.json 或对应 5_portfolio/decision.md 时静默跳过，
    避免对目录不完整的批次产生误报。
    """
    issues: list[AuditIssue] = []
    batch_path = Path(batch_dir)
    summary_path = batch_path / "batch_summary.json"
    if not summary_path.exists():
        return issues
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return issues
    rows = data.get("rows", []) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return issues

    try:
        import sys as _sys

        _here = str(Path(__file__).resolve().parent.parent)
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        from tradingagents.agents.utils.rating import normalize_rating_label
    except Exception:
        normalize_rating_label = _normalize_rating_standalone

    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").split(".")[0].strip()
        if not (ticker.isdigit() and len(ticker) == 6):
            continue

        decision_path = None
        for d in batch_path.iterdir():
            if d.is_dir() and _parse_ticker_dir_name(d.name) == ticker:
                decision_path = d / "5_portfolio" / "decision.md"
                break
        if decision_path is None or not decision_path.exists():
            continue
        try:
            text = decision_path.read_text(encoding="utf-8")
        except OSError:
            continue

        # PM 评级（冒号行，容忍 ** 与反引号包裹）
        pm_rating = None
        m = re.search(r"评级[:：]\s*\*?\*?\s*`?\s*([\w一-鿿]+)`?", text)
        if m:
            pm_rating = normalize_rating_label(m.group(1))
        summary_rating = normalize_rating_label(str(row.get("rating") or ""))
        if pm_rating and summary_rating and pm_rating != summary_rating:
            issues.append(AuditIssue(
                severity="ERROR",
                rule_id="PMSUM-001",
                message=f"PM 最终评级 {pm_rating} 与 batch_summary 的 {summary_rating} 不一致",
                ticker=ticker,
                file_path=str(decision_path),
                expected=pm_rating,
                actual=summary_rating,
                suggestion="PM 决策为权威来源，检查汇总解析是否误判（如标题中的 ticker 代码被当成评级）"
            ))

        # PM 显式不设止损（清仓/不适用）→ summary 不应写入任何止损数值
        pm_reject = re.search(
            r"(?:止损位|止损价|止损线)\*?\*?\s*[:：][^\d\n]*(?:不适用|无需设置|清仓)", text
        )
        try:
            summary_stop = float(str(row.get("stop") or "").replace(",", ""))
        except (TypeError, ValueError):
            summary_stop = None
        if summary_stop is not None and summary_stop <= 0:
            summary_stop = None
        if pm_reject is not None and summary_stop is not None:
            issues.append(AuditIssue(
                severity="ERROR",
                rule_id="PMSUM-002",
                message=f"PM 明确不设止损（{pm_reject.group(0)[:40]}），"
                        f"batch_summary 却写入 trader 数值 {summary_stop}",
                ticker=ticker,
                file_path=str(decision_path),
                expected="—",
                actual=str(summary_stop),
                suggestion="PM 显式拒绝止损时不得回退 trader 的止损数值"
            ))
            continue

        m_stop = re.search(
            r"(?:止损位|止损价|止损线|止损)[：:]\s*\*?\*?\s*`?\s*([0-9]+(?:\.[0-9]+)?)", text
        )
        if m_stop and summary_stop is not None:
            pm_stop = float(m_stop.group(1))
            if abs(pm_stop - summary_stop) > 1e-9:
                issues.append(AuditIssue(
                    severity="ERROR",
                    rule_id="PMSUM-002",
                    message=f"PM 止损 {pm_stop} 与 batch_summary 的 {summary_stop} 不一致",
                    ticker=ticker,
                    file_path=str(decision_path),
                    expected=str(pm_stop),
                    actual=str(summary_stop),
                    suggestion="PM 决策为权威来源，检查汇总是否回退了 trader 的旧止损值"
                ))

    return issues


# =============================================================================
# 决策层规则（docs/decision_layer_quality_report.md 第六节）
# =============================================================================
#
# 数据层规则（ValidationRules）校验"数字本身是否正确"；本节规则覆盖
# "原始数据正确但决策仍然出错"的缺陷：
#   ARITH-*    显式算式重算（PB/PEG/翻倍声称）
#   PERSIST-*  持仓浮盈恒等式与快照时点对齐
#   DECISION-* 决策字段关系（目标价 vs 止损、action vs rating、估值锚）
#   SCOPE-*    降权来源仍贡献执行参数 / no_data 标记与内容规模不符
#   TIME-*     分析基准日之后的事件进入证据链（时间穿越）
#   DERIVED-*  PE 反推 EPS 与卖方预测偏离（估值分位失真）
#   RATIO-*    赔率与仓位联动 / 滑点未量化
#   SNAPSHOT-* 结构化降级状态未传播到汇总层

_FULL_DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")
_DIVISION_CLAIM_RE = re.compile(
    r"(?P<a>[\d,]+(?:\.\d+)?)\s*(?:/|÷|除以)\s*(?P<b>[\d,]+(?:\.\d+)?)"
    r"\s*(?:≈|~=|约为?)\s*(?P<c>[\d,]+(?:\.\d+)?)"
)
_PEG_LINE_RE = re.compile(r"PEG\s*[:：]?\s*[^0-9+\-]{0,8}([+-]?\d+(?:\.\d+)?)", re.IGNORECASE)
_PE_TTM_LINE_RE = re.compile(
    r"(?:PE|P/E)\s*(?:[（(]\s*TTM\s*[)）])?[^\d%]{0,8}(\d+(?:\.\d+)?)\s*(?:倍|x)", re.IGNORECASE
)
_GROWTH_LINE_RE = re.compile(r"(?:同比|增长|增速)[^%+\-*\d]{0,10}\+?\s*(\d+(?:\.\d+)?)\s*%")
_DOUBLING_TARGET_RE = re.compile(r"(?:看向|望|目标|向|至|到|空间|上行).*?(?:翻倍)|(?:翻倍).*?(?:空间|上行|目标)")
_COST_PRICE_PNL_RE = re.compile(
    r"成本价?[：:]?\s*([\d.]+)[^\n]{0,40}?现价[：:]?\s*([\d.]+)[^\n]{0,40}?(浮亏|浮盈|盈亏率)[：:]?\s*([+-]?[\d.]+)\s*%"
)
_PM_TARGET_RE = re.compile(r"\*\*Price Target(?:\*\*)?[:：]?\s*\*?\*?\s*([0-9]+(?:\.[0-9]+)?)")
_PM_STOP_RE = re.compile(r"\*\*Stop(?:\*\*)?[:：]?\s*\*?\*?\s*([0-9]+(?:\.[0-9]+)?)")
_PM_ENTRY_RE = re.compile(r"\*\*Entry(?:\*\*)?[:：]?\s*\*?\*?\s*([0-9]+(?:\.[0-9]+)?)")
_PM_RATING_RE = re.compile(r"\*\*Rating(?:\*\*)?[:：]?\s*\*?\*?\s*([A-Za-z一-鿿]+)")
_TRADER_ACTION_RE = re.compile(r"FINAL TRANSACTION PROPOSAL:\s*\*\*([A-Za-z]+)\*\*")
_CLOSE_PRICE_RE = re.compile(r"收盘价[：:]?\s*\*?\*?\s*([0-9]+(?:\.[0-9]+)?)")
_TECH_LEVEL_MARKERS = ("SMA", "EMA", "MA", "日均线", "均线", "布林上轨", "布林中轨", "布林下轨", "筹码上沿", "筹码下沿", "筹码平均成本", "平均成本")
_ANALYST_LABELS = {
    "market": ("市场分析师", "Market Analyst"),
    "sentiment": ("情绪分析师", "Sentiment Analyst"),
    "news": ("新闻分析师", "News Analyst"),
    "fundamentals": ("基本面分析师", "Fundamentals Analyst"),
    "governance": ("治理分析师", "Governance Analyst"),
    "industry": ("行业分析师", "Industry Analyst"),
}


def _f(raw) -> float | None:
    try:
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _parse_iso(raw) -> "datetime | None":
    try:
        return datetime.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _parse_full_date_match(m: re.Match) -> "datetime | None":
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _load_summary_rows(batch_dir: Path) -> dict[str, dict]:
    """读取 batch_summary.json，按裸代码索引行。"""
    summary_path = batch_dir / "batch_summary.json"
    if not summary_path.exists():
        return {}
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return {}
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = _parse_ticker_dir_name(str(row.get("ticker") or ""))
        if code:
            out[code] = row
    return out


class DecisionLayerRules:
    """决策层审计规则集合。静态方法均可独立调用，便于单测。"""

    # ---------- ARITH：显式算式重算 ----------

    @staticmethod
    def check_arith_divisions(text: str, ticker: str, file_path: str) -> list[AuditIssue]:
        """ARITH-001 (CRITICAL): 形如 `A / B ≈ C` 的显式算式重算，误差 > 1% 即报。"""
        issues: list[AuditIssue] = []
        for m in _DIVISION_CLAIM_RE.finditer(text):
            raw = m.group(0)
            # 只认带小数点或千分位的算式声称，避免误抓日期/分数
            if "." not in raw and "," not in raw:
                continue
            a, b, c = _f(m.group("a")), _f(m.group("b")), _f(m.group("c"))
            if a is None or b in (None, 0.0) or c in (None, 0.0):
                continue
            calc = a / b
            # 比值写法（≈0.25）与百分比写法（≈25.21，省去 ×100%）都合法
            dev = min(
                abs(calc - c) / abs(c),
                abs(calc * 100 - c) / abs(c),
            )
            if dev > 0.01:
                issues.append(AuditIssue(
                    severity="CRITICAL",
                    rule_id="ARITH-001",
                    message=f"显式算式不成立：{m.group('a')} / {m.group('b')} ≈ {calc:.4g}（或 {calc * 100:.4g}%），"
                            f"报告写 {m.group('c')}（误差 {dev:.1%}）",
                    ticker=ticker,
                    file_path=file_path,
                    expected=f"{m.group('a')} / {m.group('b')} = {calc:.4g}",
                    actual=f"报告写 {m.group('c')}",
                    suggestion="派生量（PB/PE/股本等）必须由代码从基础量重算，禁止 LLM 直接填写",
                ))
            if len(issues) >= 5:
                break
        return issues

    @staticmethod
    def check_arith_peg(text: str, ticker: str, file_path: str) -> list[AuditIssue]:
        """ARITH-002 (CRITICAL): PEG ≈ PE(TTM) / 同段落同比增速，容差 0.05。

        ARITH-003 (WARNING): PEG ≤ 0 与 PE > 0 同文出现（未解释的口径冲突）。
        """
        issues: list[AuditIssue] = []
        lines = text.splitlines()
        pe_values: list[float] = []
        peg_nonpositive = False
        pe_positive = False
        for i, line in enumerate(lines):
            for m in _PE_TTM_LINE_RE.finditer(line):
                v = _f(m.group(1))
                if v is not None:
                    pe_values.append(v)
                    if v > 0:
                        pe_positive = True
            pm = _PEG_LINE_RE.search(line)
            if not pm:
                continue
            peg = _f(pm.group(1))
            if peg is None:
                continue
            if peg <= 0:
                peg_nonpositive = True
            window = "\n".join(lines[max(0, i - 2):i + 3])
            pe_m = _PE_TTM_LINE_RE.search(window)
            g_m = _GROWTH_LINE_RE.search(window)
            if pe_m is None or g_m is None or peg <= 0:
                continue
            pe, growth = _f(pe_m.group(1)), _f(g_m.group(1))
            if pe in (None, 0.0) or growth in (None, 0.0):
                continue
            expected = pe / growth
            if abs(expected - peg) > 0.05:
                issues.append(AuditIssue(
                    severity="CRITICAL",
                    rule_id="ARITH-002",
                    message=f"PEG 算术错误：PE(TTM) {pe} / 增速 {growth}% = {expected:.2f}，"
                            f"报告写 PEG {peg}",
                    ticker=ticker,
                    file_path=file_path,
                    expected=f"PEG ≈ {expected:.2f}",
                    actual=f"PEG = {peg}",
                    suggestion="PEG 由代码按 PE(TTM)/净利润同比增速重算",
                ))
            if len(issues) >= 1:
                break
        if peg_nonpositive and pe_positive and not any(i.rule_id == "ARITH-002" for i in issues):
            issues.append(AuditIssue(
                severity="WARNING",
                rule_id="ARITH-003",
                message="PEG ≤ 0 与 PE > 0 同时出现：若 TTM 净利为负则 PE 本身无效，"
                        "若 PE 有效则 PEG 不应为负，存在未解释的口径冲突",
                ticker=ticker,
                file_path=file_path,
                suggestion="核实 TTM 净利润符号；PE 与 PEG 必须基于同一口径",
            ))
        return issues

    @staticmethod
    def check_arith_doubling(text: str, ticker: str, file_path: str,
                             base_price: float | None) -> list[AuditIssue]:
        """ARITH-004 (WARNING): "翻倍"级空间声称与可推导的实际涨幅不符（< 80%）。"""
        if base_price in (None, 0.0):
            return []
        issues: list[AuditIssue] = []
        for line in text.splitlines():
            if "翻倍" not in line or not _DOUBLING_TARGET_RE.search(line):
                continue
            prices = [
                float(n) for n in re.findall(r"(?<![\d.])(\d+\.\d+)(?!\d)", line)
                if 0.01 < float(n) < 100000
            ]
            # 整数目标价（"107元"）也算价格
            for n in re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)\s*元", line):
                v = float(n)
                if 0.01 < v < 100000 and v not in prices:
                    prices.append(v)
            # 只保留与基准价同量级的价格（排除 RSI/百分比/净流入金额等数字），
            # 涨幅以基准价为起点计算——"翻倍空间"指当前价 → 目标价
            prices = [p for p in prices if base_price * 0.2 <= p <= base_price * 5]
            if len(prices) < 2 or max(prices) <= base_price * 1.05:
                continue
            gain = max(prices) / base_price - 1
            if 0.20 <= gain < 0.80:
                issues.append(AuditIssue(
                    severity="WARNING",
                    rule_id="ARITH-004",
                    message=f"\"翻倍\"空间声称与实际涨幅不符：区间内最大涨幅 {gain:.1%}，未达到翻倍",
                    ticker=ticker,
                    file_path=file_path,
                    expected="声称翻倍 → 涨幅应 ≥ 80%（容差）",
                    actual=f"可推导涨幅 {gain:.1%}",
                    suggestion="价格百分比声称须与同段落实际涨跌幅核对",
                ))
            if len(issues) >= 3:
                break
        return issues

    # ---------- PERSIST：持仓浮盈与快照时点 ----------

    @staticmethod
    def _holding_triples(text: str) -> list[tuple[float, float, float, str]]:
        """同行内 成本 + 现价 + 浮亏/浮盈/盈亏率 三元组 → (cost, price, claimed_pct, keyword)。"""
        triples = []
        for line in text.splitlines():
            m = _COST_PRICE_PNL_RE.search(line)
            if not m:
                continue
            cost, price, claimed = _f(m.group(1)), _f(m.group(2)), _f(m.group(4))
            if cost in (None, 0.0) or price is None or claimed is None:
                continue
            triples.append((cost, price, claimed, m.group(3)))
        return triples

    @classmethod
    def check_persist_pnl(cls, text: str, ticker: str, file_path: str) -> list[AuditIssue]:
        """PERSIST-001 (CRITICAL): unrealized_pnl_pct ≈ (现价/成本 − 1)，容差 0.5pp。"""
        issues: list[AuditIssue] = []
        for cost, price, claimed, keyword in cls._holding_triples(text):
            if keyword == "浮亏":
                claimed = -abs(claimed)
            elif keyword == "浮盈":
                claimed = abs(claimed)
            actual = (price / cost - 1) * 100
            if abs(claimed - actual) > 0.5:
                issues.append(AuditIssue(
                    severity="CRITICAL",
                    rule_id="PERSIST-001",
                    message=f"浮盈浮亏计算错误：成本 {cost} / 现价 {price} → 实际 {actual:+.2f}%，"
                            f"报告写 {claimed:+.2f}%（误差 {abs(claimed - actual):.1f}pp）",
                    ticker=ticker,
                    file_path=file_path,
                    expected=f"{actual:+.2f}%",
                    actual=f"{claimed:+.2f}%",
                    suggestion="浮盈浮亏必须由代码从 avg_cost / last_price 重算，禁止直接使用同步字段",
                ))
            if len(issues) >= 5:
                break
        return issues

    @classmethod
    def check_persist_snapshot(cls, texts: dict[str, str], ticker: str) -> list[AuditIssue]:
        """PERSIST-002 (WARNING): 持仓快照现价与验证快照收盘价不一致（时点错配）。"""
        closes = [
            _f(m.group(1))
            for text in texts.values()
            for m in _CLOSE_PRICE_RE.finditer(text)
        ]
        closes = [c for c in closes if c]
        if not closes:
            return []
        close = max(set(closes), key=closes.count)
        issues: list[AuditIssue] = []
        for path, text in texts.items():
            for _cost, price, _claimed, _kw in cls._holding_triples(text):
                if price and abs(price - close) / close > 0.02:
                    issues.append(AuditIssue(
                        severity="WARNING",
                        rule_id="PERSIST-002",
                        message=f"持仓快照现价 {price} 与验证快照收盘价 {close} 偏差 "
                                f"{abs(price - close) / close:.1%}，存在时点错配",
                        ticker=ticker,
                        file_path=path,
                        expected=f"持仓现价应接近验证快照收盘 {close}",
                        actual=f"持仓现价 {price}",
                        suggestion="持仓模块应使用与验证快照同一交易日的收盘价",
                    ))
                    if len(issues) >= 3:
                        return issues
        return issues

    # ---------- DECISION：字段关系 ----------

    @staticmethod
    def _pm_fields(texts: dict[str, str]) -> dict[str, float | str | None]:
        out: dict[str, float | str | None] = {}
        decision_text = ""
        for path, text in texts.items():
            if path.endswith("decision.md"):
                decision_text = text
                break
        for key, pat in (("target", _PM_TARGET_RE), ("stop", _PM_STOP_RE), ("entry", _PM_ENTRY_RE)):
            m = pat.search(decision_text)
            out[key] = _f(m.group(1)) if m else None
        m = _PM_RATING_RE.search(decision_text)
        out["rating"] = m.group(1).strip() if m else None
        return out

    @staticmethod
    def check_decision_fields(texts: dict[str, str], ticker: str,
                              summary_row: dict | None) -> list[AuditIssue]:
        """DECISION-001 (CRITICAL): price_target == stop_loss 或 price_target < stop_loss。"""
        fields = DecisionLayerRules._pm_fields(texts)
        target = fields.get("target")
        stop = fields.get("stop")
        if summary_row:
            if target is None:
                target = _f(summary_row.get("target"))
            if stop is None:
                stop = _f(summary_row.get("stop"))
        if target is None or stop is None:
            return []
        decision_path = next((p for p in texts if p.endswith("decision.md")), "")
        if abs(target - stop) / max(stop, 1e-9) <= 0.001:
            return [AuditIssue(
                severity="CRITICAL",
                rule_id="DECISION-001",
                message=f"price_target（{target}）等于 stop_loss（{stop}），对多头仓位零信息量",
                ticker=ticker,
                file_path=decision_path,
                suggestion="目标价必须高于止损价；检查目标价是否被误填为技术位",
            )]
        if target < stop:
            return [AuditIssue(
                severity="CRITICAL",
                rule_id="DECISION-001",
                message=f"price_target（{target}）低于 stop_loss（{stop}），字段关系矛盾",
                ticker=ticker,
                file_path=decision_path,
                expected=f"price_target > stop_loss ({stop})",
                actual=f"price_target = {target}",
                suggestion="检查目标价与止损价是否被写反，或目标价缺估值锚",
            )]
        return []

    @staticmethod
    def check_action_rating(texts: dict[str, str], ticker: str,
                            summary_row: dict | None) -> list[AuditIssue]:
        """DECISION-002 (CRITICAL): Trader action 与 PM rating 方向/执行语义冲突。

        执行语义：Buy=加仓/开仓，Hold=维持，Underweight=减仓，Sell=清仓。
        action=Sell + rating=Underweight（清仓 vs 减半）同样会触发——下游若按
        action 自动执行将多卖出一半仓位。
        """
        action = None
        for path, text in texts.items():
            if path.endswith("trader.md"):
                m = _TRADER_ACTION_RE.search(text)
                if m:
                    action = m.group(1).capitalize()
                break
        rating = DecisionLayerRules._pm_fields(texts).get("rating")
        if summary_row and not rating:
            rating = str(summary_row.get("rating") or "").strip()
        if action not in ("Buy", "Hold", "Sell") or not rating:
            return []
        rating_norm = (rating or "").capitalize()
        action_dir = {"Buy": 1, "Hold": 0, "Sell": -1}[action]
        rating_dir = {"Buy": 1, "Overweight": 1, "Hold": 0,
                      "Underweight": -1, "Sell": -1}.get(rating_norm)
        if rating_dir is None:
            return []
        decision_path = next((p for p in texts if p.endswith("decision.md")), "")
        if action_dir != rating_dir:
            return [AuditIssue(
                severity="CRITICAL",
                rule_id="DECISION-002",
                message=f"Trader action={action} 与 PM rating={rating_norm} 方向相反，"
                        "下游执行字段互相冲突",
                ticker=ticker,
                file_path=decision_path,
                suggestion="PM 的 rating 为权威方向；下游自动化必须以 rating 机械派生 action",
            )]
        if action == "Sell" and rating_norm == "Underweight":
            return [AuditIssue(
                severity="CRITICAL",
                rule_id="DECISION-002",
                message="Trader action=Sell（清仓）与 PM rating=Underweight（减半）执行语义"
                        "差一整级：按 action 自动执行会触发清仓",
                ticker=ticker,
                file_path=decision_path,
                suggestion="明确唯一执行字段：由 PM rating 机械派生 action，禁止两边独立填写",
            )]
        return []

    @staticmethod
    def check_target_anchor(texts: dict[str, str], ticker: str) -> list[AuditIssue]:
        """DECISION-003 (WARNING): price_target 落在均线/布林/筹码位上而缺估值锚。"""
        fields = DecisionLayerRules._pm_fields(texts)
        target = fields.get("target")
        if not isinstance(target, float):
            return []
        levels: set[float] = set()
        for text in texts.values():
            for line in text.splitlines():
                if not any(marker in line for marker in _TECH_LEVEL_MARKERS):
                    continue
                for n in re.findall(r"(?<![\d.])(\d+\.\d+)(?![\d])", line):
                    v = float(n)
                    if 0.01 < v < 100000:
                        levels.add(v)
        for level in levels:
            if abs(target - level) / level <= 0.005:
                decision_path = next((p for p in texts if p.endswith("decision.md")), "")
                return [AuditIssue(
                    severity="WARNING",
                    rule_id="DECISION-003",
                    message=f"price_target {target} 恰为技术位（{level}，均线/布林/筹码），"
                            "目标价疑似直接钉在技术指标上而非估值推导",
                    ticker=ticker,
                    file_path=decision_path,
                    suggestion="目标价必须给出估值锚（PE/PB/DCF 推导）；纯技术位目标说明"
                               "流水线未执行估值推导",
                )]
        return []

    # ---------- SCOPE：降权来源仍被使用 ----------

    @staticmethod
    def _no_data_analysts(texts: dict[str, str]) -> set[str]:
        """从决策层文本（manager/trader/decision 及完整报告）识别被标 no_data 的分析师。"""
        flagged: set[str] = set()
        haystack = "\n".join(texts.values())
        for stem, (cn, en) in _ANALYST_LABELS.items():
            patterns = [
                rf"{cn}[^。\n]{{0,40}}no_data",
                rf"no_data[^。\n]{{0,40}}{cn}",
                rf"\|\s*{re.escape(en)}\s*\|\s*❌\s*no_data",
            ]
            if any(re.search(p, haystack) for p in patterns):
                flagged.add(stem)
        return flagged

    @classmethod
    def check_scope(cls, texts: dict[str, str], ticker: str) -> list[AuditIssue]:
        """SCOPE-001 (CRITICAL): 被判 no_data 的来源贡献了 stop_loss 数值。

        SCOPE-002 (WARNING): 被判 no_data 的报告正文超过 100 行（标记与内容不符）。
        """
        flagged = cls._no_data_analysts(texts)
        if not flagged:
            return []
        issues: list[AuditIssue] = []
        stop = cls._pm_fields(texts).get("stop")
        for stem in sorted(flagged):
            path = next((p for p in texts if f"1_analysts{os.sep}{stem}.md" in p), "")
            text = texts.get(path, "")
            line_count = len([ln for ln in text.splitlines() if ln.strip()])
            if line_count > 100:
                issues.append(AuditIssue(
                    severity="WARNING",
                    rule_id="SCOPE-002",
                    message=f"{stem}.md 被标 no_data 但正文 {line_count} 行（>100），"
                            "标记与内容规模不符：no_data 应只表示整份不可用",
                    ticker=ticker,
                    file_path=path,
                    suggestion="拆分 no_data 为 partial（部分字段缺失）/ empty（无内容），"
                               "partial 只降级具体字段",
                ))
            stop_hit = False
            if isinstance(stop, float) and text:
                # 词边界匹配，避免 "13.0" 子串命中 "113.05"
                stop_hit = any(
                    re.search(rf"(?<![\d.]){re.escape(c)}(?![\d.])", text)
                    for c in {f"{stop:.2f}", f"{stop:g}"}
                )
                if stop_hit:
                    issues.append(AuditIssue(
                        severity="CRITICAL",
                        rule_id="SCOPE-001",
                        message=f"止损位 {stop} 出现在被判 no_data 的 {stem}.md 中——"
                                "降权来源的数值仍被用于生成执行参数",
                        ticker=ticker,
                        file_path=path,
                        suggestion="决策层禁止引用权重 < 0.10 / no_data 来源生成 stop/entry/size",
                    ))
            # 派生引用检查：止损常由 ATR/均线推导而非直接抄数——若决策文本引用的
            # 技术位（均线/布林/筹码）出现在被判 no_data 的报告中，同样算降权来源
            # 贡献了执行参数（航宇 688239 案例：止损与触发位全部来自 no_data 报告）
            if text and not stop_hit:
                fields = cls._pm_fields(texts)
                exclude = {
                    v for v in (fields.get("entry"), stop)
                    if isinstance(v, float)
                }
                closes = [
                    _f(m.group(1)) for t in texts.values()
                    for m in _CLOSE_PRICE_RE.finditer(t)
                ]
                exclude.update(c for c in closes if c)
                cited: list[float] = []
                for line in text.splitlines():
                    if not any(marker in line for marker in _TECH_LEVEL_MARKERS):
                        continue
                    for n in re.findall(r"(?<![\d.])(\d+\.\d+)(?![\d])", line):
                        v = float(n)
                        if not 1.0 <= v < 100000 or any(
                            abs(v - e) / e <= 0.01 for e in exclude if e
                        ):
                            continue
                        for dp, dt_ in texts.items():
                            if not dp.endswith(("decision.md", "trader.md")):
                                continue
                            if re.search(rf"(?<![\d.]){re.escape(n)}(?![\d.])", dt_):
                                cited.append(v)
                                break
                if cited:
                    issues.append(AuditIssue(
                        severity="CRITICAL",
                        rule_id="SCOPE-001",
                        message=f"决策引用的技术位 {sorted(set(cited))} 出自被判 no_data 的 "
                                f"{stem}.md——降权来源的数值仍被用于生成执行参数",
                        ticker=ticker,
                        file_path=path,
                        suggestion="决策层禁止引用权重 < 0.10 / no_data 来源生成 stop/entry/size",
                    ))
        return issues

    # ---------- TIME：时间穿越 ----------

    @staticmethod
    def _resolve_analysis_date(batch_dir: Path, texts: dict[str, str],
                               global_date: "datetime | None" = None) -> "datetime | None":
        """分析基准日：优先取本票验证快照收盘日，退回全批次众数，再退批次目录日期。

        强锚点（"收盘行已核对 / 验证快照 / 分析日期"附近）计 3 票，
        普通"YYYY-MM-DD …收盘"只计 1 票，避免被历史行情日期带偏。
        """
        candidates: list[str] = []
        for text in texts.values():
            for m in re.finditer(
                r"(20\d{2}-\d{2}-\d{2})[^\n]{0,12}(?:收盘行已核对|验证快照|分析日期|分析基准日)", text
            ):
                candidates.extend([m.group(1)] * 3)
            for m in re.finditer(r"(20\d{2}-\d{2}-\d{2})[^\n]{0,25}收盘", text):
                candidates.append(m.group(1))
            for m in re.finditer(r"快照[（(]\s*(20\d{2}-\d{2}-\d{2})\s*[)）]", text):
                candidates.append(m.group(1))
        # 候选日必须在批次运行日前 20 天内：季报日期（2026-06-30）、历史行情日
        # 等强锚点误匹配不应成为基准日；全部越界时保留原样交给后续回退
        m_dir = re.search(r"(20\d{2})(\d{2})(\d{2})", Path(batch_dir).name)
        dir_date = _parse_iso(f"{m_dir.group(1)}-{m_dir.group(2)}-{m_dir.group(3)}") if m_dir else None
        if dir_date is not None and candidates:
            lo = datetime.fromordinal(dir_date.toordinal() - 20)
            # 越界候选直接丢弃：全部越界时交给 global/dir 回退，不保留脏众数
            candidates = [
                c for c in candidates
                if (cd := _parse_iso(c)) is not None and lo <= cd <= dir_date
            ]
        if candidates:
            best = max(set(candidates), key=candidates.count)
            return _parse_iso(best)
        if global_date is not None:
            return global_date
        m = re.search(r"(20\d{2})(\d{2})(\d{2})", Path(batch_dir).name)
        if m:
            return _parse_iso(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
        return None

    @classmethod
    def check_time_travel(cls, texts: dict[str, str], ticker: str,
                          analysis_date: "datetime | None") -> list[AuditIssue]:
        """TIME-001 (CRITICAL): 新闻报告写入 event_date > analysis_date 的事件。

        只扫描新闻分析师文件——PM/风控讨论未来计划（如"10月下旬三季报"）属正常
        前瞻，不构成证据链污染；污染源是新闻报告本身（输出层硬过滤的对象）。
        """
        if analysis_date is None:
            return []
        news_paths = [p for p in texts if f"{os.sep}1_analysts{os.sep}news.md" in p]
        scan_paths = news_paths or list(texts)
        issues: list[AuditIssue] = []
        for path in scan_paths:
            for line in texts[path].splitlines():
                # 财报预约披露日、多日期日程列表是已知未来安排，不构成证据链污染
                if "预约" in line or "披露日" in line or "分析窗口" in line or "数据截至" in line:
                    continue
                for m in _FULL_DATE_RE.finditer(line):
                    if line[m.end():m.end() + 1] in ("、", "/"):
                        continue
                    d = _parse_full_date_match(m)
                    if d is not None and d.date() > analysis_date.date():
                        issues.append(AuditIssue(
                            severity="CRITICAL",
                            rule_id="TIME-001",
                            message=f"分析基准日 {analysis_date.date()} 之后的事件"
                                    f"（{d.date()}）进入分析师报告：{line.strip()[:80]}",
                            ticker=ticker,
                            file_path=path,
                            suggestion="输出层硬过滤：event_date > analysis_date 的条目直接丢弃",
                        ))
                        break
                if len(issues) >= 5:
                    return issues
        return issues

    # ---------- DERIVED：估值分位失真 ----------

    @staticmethod
    def check_derived_eps(metrics_by_file: dict[str, FinancialMetrics],
                          ticker: str) -> list[AuditIssue]:
        """DERIVED-001 (WARNING): PE(TTM) 反推 EPS 与卖方当年预测 EPS 偏离 > 15%。"""
        price = pe = eps_fwd = None
        file_path = ""
        for path, m in metrics_by_file.items():
            if price is None and m.price:
                price = m.price
            if pe is None and m.pe_ttm:
                pe = m.pe_ttm
                file_path = path
            if eps_fwd is None and m.eps_expected:
                eps_fwd = m.eps_expected
        if not price or not pe or not eps_fwd or pe <= 0:
            return []
        implied = price / pe
        if abs(implied - eps_fwd) / abs(eps_fwd) > 0.15:
            return [AuditIssue(
                severity="WARNING",
                rule_id="DERIVED-001",
                message=f"PE(TTM) {pe} 反推 TTM EPS ≈ {implied:.2f} 元，与卖方预测 EPS "
                        f"{eps_fwd:.2f} 元偏离 {abs(implied - eps_fwd) / abs(eps_fwd):.1%}——"
                        "历史分位衡量的是正在恶化的分母",
                ticker=ticker,
                file_path=file_path,
                expected=f"前瞻 PE ≈ {price / eps_fwd:.2f}x",
                actual=f"报告口径 PE(TTM) = {pe}x",
                suggestion="用卖方一致预期 EPS 换算前瞻 PE 后再谈估值分位",
            )]
        return []

    # ---------- RATIO：赔率-仓位联动 / 滑点 ----------

    @staticmethod
    def check_ratio(texts: dict[str, str], ticker: str,
                    summary_row: dict | None) -> list[AuditIssue]:
        """RATIO-001 (WARNING): R/R < 1.5 且仓位 ≥ 标准配置 50%。

        RATIO-002 (INFO): 建议仓位变动量 / 日均成交量 > 5%，滑点风险未量化。
        """
        issues: list[AuditIssue] = []
        fields = DecisionLayerRules._pm_fields(texts)
        target = fields.get("target")
        entry = fields.get("entry")
        stop = fields.get("stop")
        if summary_row:
            entry = entry or _f(summary_row.get("entry"))
            stop = stop or _f(summary_row.get("stop"))
        size_text = ""
        for path, text in texts.items():
            if path.endswith("decision.md"):
                m = re.search(r"\*\*Size\*\*[:：]?\s*(.+)", text)
                if m:
                    size_text = m.group(1)
                break
        if not size_text and summary_row:
            size_text = str(summary_row.get("size") or "")
        if isinstance(target, float) and isinstance(entry, float) and isinstance(stop, float) \
                and entry > stop > 0 and target > entry:
            rr = (target - entry) / (entry - stop)
            size_frac = None
            m = re.search(
                r"标准(?:单票)?配置的?\s*[:：]?\s*(?:约|≈)?\s*"
                r"(\d+(?:\.\d+)?)\s*%\s*[–—~\-～]\s*(\d+(?:\.\d+)?)\s*%"
                r"|标准(?:单票)?配置的?\s*[:：]?\s*(?:约|≈)?\s*"
                r"(\d+(?:\.\d+)?)\s*[–—~\-～]\s*(\d+(?:\.\d+)?)\s*%", size_text
            )
            if m:
                nums = [_f(g) for g in m.groups() if g]
                if nums:
                    size_frac = max(nums) / 100
            else:
                m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:标准|仓位|组合)", size_text)
                if m:
                    size_frac = (_f(m.group(1)) or 0) / 100
            if size_frac is not None and 0 < rr < 1.5 and size_frac >= 0.5:
                decision_path = next((p for p in texts if p.endswith("decision.md")), "")
                issues.append(AuditIssue(
                    severity="WARNING",
                    rule_id="RATIO-001",
                    message=f"风险收益比 {rr:.2f} < 1.5，但仓位给到标准配置的 "
                            f"{size_frac:.0%}——赔率与仓位未联动",
                    ticker=ticker,
                    file_path=decision_path,
                    suggestion="R/R < 1.5 时应等比例削减仓位，或放宽目标价/收紧止损改善赔率",
                ))
        # RATIO-002: 建议股数 vs 日均成交量
        vol = None
        for path, text in texts.items():
            if f"{os.sep}1_analysts{os.sep}market.md" in path:
                m = re.search(r"日均成交量[：:]?\s*([\d.,]+)\s*(万|亿)?股", text)
                if m:
                    vol = _f(m.group(1))
                    if vol is not None:
                        vol *= {"万": 1e4, "亿": 1e8}.get(m.group(2) or "", 1)
                break
        if vol:
            for m in re.finditer(r"\+?\s*([\d,]{3,})\s*股", size_text):
                shares = _f(m.group(1))
                if shares and vol > 0 and shares / vol > 0.05:
                    decision_path = next((p for p in texts if p.endswith("decision.md")), "")
                    issues.append(AuditIssue(
                        severity="INFO",
                        rule_id="RATIO-002",
                        message=f"建议交易 {shares:,.0f} 股 ≈ 日均成交量的 {shares / vol:.1%}"
                                "（>5%），滑点成本未量化",
                        ticker=ticker,
                        file_path=decision_path,
                        suggestion="对低流动性标的应量化滑点并分批执行",
                    ))
                    break
        return issues

    # ---------- SNAPSHOT：结构化降级传播 ----------

    @staticmethod
    def check_snapshot_fallback(texts: dict[str, str], ticker: str,
                                summary_row: dict | None) -> list[AuditIssue]:
        """SNAPSHOT-001 (WARNING): 报告含 STRUCTURED_FALLBACK 标记但汇总层未标记（或反之）。"""
        if summary_row is None:
            return []
        marker_in_files = any("STRUCTURED_FALLBACK" in t for t in texts.values())
        summary_flag = bool(summary_row.get("fallback"))
        if marker_in_files == summary_flag:
            return []
        decision_path = next((p for p in texts if p.endswith("decision.md")), "")
        return [AuditIssue(
            severity="WARNING",
            rule_id="SNAPSHOT-001",
            message=(
                f"报告含 STRUCTURED_FALLBACK 标记但 batch_summary fallback={summary_flag}"
                if marker_in_files else
                "batch_summary fallback=True 但报告中没有 STRUCTURED_FALLBACK 标记"
            ) + "——降级评级未经结构化字段校验，下游无法识别",
            ticker=ticker,
            file_path=decision_path,
            suggestion="结构化降级状态必须传播到 batch_summary 并在摘要层显式标记",
        )]


def run_decision_layer_checks(batch_dir: Path,
                              results: dict[str, AuditResult],
                              texts: dict[str, dict[str, str]]) -> list[AuditIssue]:
    """对整批运行全部决策层规则，返回跨文件问题列表。"""
    issues: list[AuditIssue] = []
    summary_rows = _load_summary_rows(Path(batch_dir))
    # 全批次级别的快照日期众数，作为单票解析不到时的基准日回退
    global_candidates: list[str] = []
    for ticker_texts in texts.values():
        for text in ticker_texts.values():
            for m in re.finditer(r"(20\d{2}-\d{2}-\d{2})[^\n]{0,25}收盘", text):
                global_candidates.append(m.group(1))
    global_date = None
    if global_candidates:
        gd = max(set(global_candidates), key=global_candidates.count)
        gd_parsed = _parse_iso(gd)
        m_dir = re.search(r"(20\d{2})(\d{2})(\d{2})", str(batch_dir))
        dir_date = _parse_iso(f"{m_dir.group(1)}-{m_dir.group(2)}-{m_dir.group(3)}") if m_dir else None
        if gd_parsed is not None and dir_date is not None:
            lo = datetime.fromordinal(dir_date.toordinal() - 20)
            global_date = gd_parsed if lo <= gd_parsed <= dir_date else dir_date
        else:
            global_date = gd_parsed
    for ticker, result in results.items():
        ticker_texts = texts.get(ticker, {})
        if not ticker_texts:
            continue
        summary_row = summary_rows.get(ticker)
        analysis_date = DecisionLayerRules._resolve_analysis_date(
            Path(batch_dir), ticker_texts, global_date
        )
        # 翻倍声称的基准价：优先取 PM 决策的 entry（= 验证快照收盘价），
        # 退回"收盘价"提及的众数
        base_price = DecisionLayerRules._pm_fields(ticker_texts).get("entry")
        if not isinstance(base_price, float) or base_price <= 0:
            closes = [
                _f(m.group(1)) for t in ticker_texts.values()
                for m in _CLOSE_PRICE_RE.finditer(t)
            ]
            closes = [c for c in closes if c]
            base_price = max(set(closes), key=closes.count) if closes else None
        for path, text in ticker_texts.items():
            issues.extend(DecisionLayerRules.check_arith_divisions(text, ticker, path))
            issues.extend(DecisionLayerRules.check_arith_peg(text, ticker, path))
            issues.extend(
                DecisionLayerRules.check_arith_doubling(text, ticker, path, base_price)
            )
            issues.extend(DecisionLayerRules.check_persist_pnl(text, ticker, path))
        issues.extend(DecisionLayerRules.check_persist_snapshot(ticker_texts, ticker))
        issues.extend(DecisionLayerRules.check_decision_fields(ticker_texts, ticker, summary_row))
        issues.extend(DecisionLayerRules.check_action_rating(ticker_texts, ticker, summary_row))
        issues.extend(DecisionLayerRules.check_target_anchor(ticker_texts, ticker))
        issues.extend(DecisionLayerRules.check_scope(ticker_texts, ticker))
        issues.extend(DecisionLayerRules.check_time_travel(ticker_texts, ticker, analysis_date))
        issues.extend(DecisionLayerRules.check_derived_eps(result.metrics, ticker))
        issues.extend(DecisionLayerRules.check_ratio(ticker_texts, ticker, summary_row))
        issues.extend(DecisionLayerRules.check_snapshot_fallback(ticker_texts, ticker, summary_row))
    # 同一论断在 complete_report.md 与分目录文件中重复出现，按 (rule, ticker, 消息) 去重
    seen: set[tuple[str, str, str]] = set()
    deduped: list[AuditIssue] = []
    for issue in issues:
        key = (issue.rule_id, issue.ticker, issue.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


# =============================================================================
# 主审计引擎
# =============================================================================

class ReportAuditor:
    """报告审计主引擎"""

    def __init__(self, batch_dir: str):
        self.batch_dir = Path(batch_dir)
        self.extractor = MetricsExtractor()
        self.results: dict[str, AuditResult] = {}
        # 决策层规则需要原文：path -> text，按 ticker 分组
        self._texts: dict[str, dict[str, str]] = {}
        self._data_reliability = self._load_data_reliability()

    def _load_data_reliability(self) -> dict[str, dict[str, int]]:
        summary_path = self.batch_dir / "batch_summary.json"
        if not summary_path.exists():
            return {}
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if isinstance(payload, dict):
            rows = payload.get("rows")
        elif isinstance(payload, list):
            rows = payload
        else:
            return {}
        if not isinstance(rows, list):
            return {}
        reliability_by_ticker = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_ticker = str(row.get("ticker") or "")
            ticker = _parse_ticker_dir_name(raw_ticker) or raw_ticker
            reliability = row.get("data_reliability")
            if ticker and isinstance(reliability, dict):
                normalized = {
                    key: _nonnegative_int(reliability.get(key))
                    for key in DATA_RELIABILITY_KEYS
                }
                normalized["logical_requests"] = sum(normalized.values())
                normalized["applicable_requests"] = (
                    normalized["logical_requests"] - normalized["not_applicable"]
                )
                normalized["call_count"] = max(
                    normalized["logical_requests"],
                    _nonnegative_int(reliability.get("call_count")),
                )
                reliability_by_ticker[ticker] = normalized
        return reliability_by_ticker

    def _apply_data_reliability(self, result: AuditResult) -> None:
        reliability = self._data_reliability.get(result.ticker)
        if not reliability:
            return
        result.data_reliability = dict(reliability)
        if reliability["missing"]:
            result.issues.append(AuditIssue(
                severity="WARNING",
                rule_id="DATA-RELIABILITY-MISSING",
                message=f"{reliability['missing']} 个适用逻辑请求缺少有效数据",
                ticker=result.ticker,
                file_path=str(self.batch_dir / "batch_summary.json"),
                suggestion="检查缺失请求的数据源、策略适用性和 fallback 结果",
            ))
        if reliability["partial"]:
            result.issues.append(AuditIssue(
                severity="INFO",
                rule_id="DATA-RELIABILITY-PARTIAL",
                message=f"{reliability['partial']} 个适用逻辑请求仅部分可用或已过期",
                ticker=result.ticker,
                file_path=str(self.batch_dir / "batch_summary.json"),
                suggestion="在投资结论中保留对应数据限制和较低置信度",
            ))

    def audit(
        self,
        target_ticker: str | None = None,
        cross_validate: bool = False,
    ) -> dict[str, AuditResult]:
        """执行审计

        cross_validate=True 时调用 akshare_realtime.fetch_realtime_snapshot
        对每只 A 股做 PE / 市值对照，命中偏差 > 10% 即追加 REALTIME-* issue。
        """
        # 兼容纯代码目录(000100)与 名称_代码 目录(TCL科技_000100 / 万华化学_600309.SS)
        ticker_dirs = {}
        for d in self.batch_dir.iterdir():
            if not d.is_dir():
                continue
            code = _parse_ticker_dir_name(d.name)
            if code is not None:
                ticker_dirs[code] = d.name

        if target_ticker:
            target = _parse_ticker_dir_name(target_ticker) or target_ticker
            ticker_dirs = {k: v for k, v in ticker_dirs.items() if k == target}

        snapshot_fn = None
        if cross_validate:
            try:
                # 延迟导入，避免无 cross-validate 时强依赖 tradingagents 包
                import sys as _sys
                _here = str(Path(__file__).resolve().parent.parent)
                if _here not in _sys.path:
                    _sys.path.insert(0, _here)
                from tradingagents.dataflows.akshare_realtime import (
                    fetch_realtime_snapshot as snapshot_fn,
                )
            except Exception as exc:
                print(f"⚠️  cross-validate 模块加载失败，跳过实时对照: {exc}")
                snapshot_fn = None

        for ticker in sorted(ticker_dirs):
            result = self._audit_ticker(ticker, ticker_dirs[ticker])
            self._apply_data_reliability(result)
            if snapshot_fn is not None:
                self._apply_realtime_validation(ticker, result, snapshot_fn)
            self.results[ticker] = result

        # 跨文件一致性检查
        cross_issues = check_cross_file_consistency(self.results)
        for issue in cross_issues:
            if issue.ticker in self.results:
                self.results[issue.ticker].issues.append(issue)

        # PM 最终决策 vs batch_summary 一致性（PMSUM-001/002）
        for issue in check_pm_summary_consistency(self.batch_dir):
            if issue.ticker in self.results:
                self.results[issue.ticker].issues.append(issue)

        # 决策层规则（ARITH/PERSIST/DECISION/TIME/SCOPE/DERIVED/RATIO/SNAPSHOT）
        for issue in run_decision_layer_checks(self.batch_dir, self.results, self._texts):
            if issue.ticker in self.results:
                self.results[issue.ticker].issues.append(issue)

        return self.results

    def _apply_realtime_validation(self, ticker: str, result: AuditResult, snapshot_fn) -> None:
        """Fetch a realtime snapshot for *ticker* and append REALTIME-* issues."""
        qualified = _qualify_a_share(ticker)
        if qualified is None:
            return
        try:
            snapshot = snapshot_fn(qualified)
        except Exception as exc:
            print(f"⚠️  {ticker} 真值快照获取失败: {exc}")
            return
        if not snapshot:
            return
        for metrics in result.metrics.values():
            for fn in (
                ValidationRules.check_realtime_pe,
                ValidationRules.check_realtime_market_cap,
            ):
                issue = fn(metrics, snapshot)
                if issue is not None:
                    result.issues.append(issue)

    def _audit_ticker(self, ticker: str, dir_name: str) -> AuditResult:
        """审计单个股票的所有文件

        ticker 为解析出的代码（如 000100），dir_name 为实际目录名
        （如 TCL科技_000100）。
        """
        ticker_dir = self.batch_dir / dir_name
        result = AuditResult(ticker=ticker)

        # 遍历所有MD文件
        md_files = list(ticker_dir.rglob("*.md"))
        result.files_audited = len(md_files)

        for md_file in md_files:
            try:
                text = md_file.read_text(encoding='utf-8')
                self._texts.setdefault(ticker, {})[str(md_file)] = text
                metrics, extract_issues = self.extractor.extract(text, ticker, str(md_file))
                result.metrics[str(md_file)] = metrics
                result.issues.extend(extract_issues)

                # 一个指标都没提取到 → 可能是目录发现或提取器失效的信号
                if not _has_any_metric(metrics):
                    result.issues.append(AuditIssue(
                        severity="WARNING",
                        rule_id="EXTRACT-001",
                        message="未从该文件提取到任何财务指标",
                        ticker=ticker,
                        file_path=str(md_file),
                        suggestion="若大量文件出现此警告，检查目录发现或提取规则是否失效"
                    ))

                # 运行验证规则
                issues = ValidationRules.run_all(metrics)
                result.issues.extend(issues)

            except Exception as e:
                result.issues.append(AuditIssue(
                    severity="ERROR",
                    rule_id="SYSTEM-001",
                    message=f"读取文件失败: {e}",
                    ticker=ticker,
                    file_path=str(md_file),
                    suggestion="检查文件编码或权限"
                ))

        return result

    def generate_report(self, output_dir: str) -> str:
        """生成审计报告"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = output_path / f"audit_report_{timestamp}.md"
        json_file = output_path / f"audit_report_{timestamp}.json"

        # 生成JSON报告
        report_data = {
            "audit_time": datetime.now().isoformat(),
            "batch_dir": str(self.batch_dir),
            "summary": self._generate_summary(),
            "results": {
                ticker: {
                    "ticker": r.ticker,
                    "files_audited": r.files_audited,
                    "critical": r.critical_count(),
                    "errors": r.error_count(),
                    "warnings": r.warning_count(),
                    "data_reliability": r.data_reliability,
                    "issues": [i.to_dict() for i in r.issues],
                }
                for ticker, r in self.results.items()
            }
        }

        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        # 生成Markdown报告
        md_content = self._generate_markdown_report()
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(md_content)

        return str(report_file)

    def _generate_summary(self) -> dict:
        """生成汇总统计"""
        total_files = sum(r.files_audited for r in self.results.values())
        total_critical = sum(r.critical_count() for r in self.results.values())
        total_errors = sum(r.error_count() for r in self.results.values())
        total_warnings = sum(r.warning_count() for r in self.results.values())
        data_reliability = aggregate_data_reliability([])
        for result in self.results.values():
            for key in data_reliability:
                data_reliability[key] += result.data_reliability.get(key, 0)

        return {
            "total_tickers": len(self.results),
            "total_files_audited": total_files,
            "total_critical": total_critical,
            "total_errors": total_errors,
            "total_warnings": total_warnings,
            "health_score": max(0, 100 - total_critical * 10 - total_errors * 5 - total_warnings * 1),
            "data_reliability": data_reliability,
        }

    def _generate_markdown_report(self) -> str:
        """生成Markdown格式的审计报告"""
        summary = self._generate_summary()

        lines = [
            "# 财务报告审计报告",
            "",
            f"**审计时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**审计目录**: `{self.batch_dir}`",
            "",
            "## 汇总统计",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            f"| 审计股票数 | {summary['total_tickers']} |",
            f"| 审计文件数 | {summary['total_files_audited']} |",
            f"| CRITICAL | {summary['total_critical']} |",
            f"| ERROR | {summary['total_errors']} |",
            f"| WARNING | {summary['total_warnings']} |",
            f"| 健康评分 | {summary['health_score']}/100 |",
            "",
            "---",
            "",
            "## 数据可靠性",
            "",
            "| 指标 | 数量 |",
            "|------|------|",
            *[
                f"| {key} | {summary['data_reliability'][key]} |"
                for key in (*DATA_RELIABILITY_KEYS, "logical_requests", "applicable_requests", "call_count")
            ],
            "",
            "---",
            "",
        ]

        # 按严重程度排序的问题列表
        all_issues = []
        for ticker, result in self.results.items():
            for issue in result.issues:
                all_issues.append((ticker, issue))

        severity_order = {"CRITICAL": 0, "ERROR": 1, "WARNING": 2, "INFO": 3}
        all_issues.sort(key=lambda x: severity_order.get(x[1].severity, 99))

        # CRITICAL 问题
        critical_issues = [(t, i) for t, i in all_issues if i.severity == "CRITICAL"]
        if critical_issues:
            lines.extend([
                "## CRITICAL 问题（必须立即修复）",
                "",
            ])
            for ticker, issue in critical_issues:
                lines.extend(self._format_issue(ticker, issue))

        # ERROR 问题
        error_issues = [(t, i) for t, i in all_issues if i.severity == "ERROR"]
        if error_issues:
            lines.extend([
                "## ERROR 问题（需要修复）",
                "",
            ])
            for ticker, issue in error_issues:
                lines.extend(self._format_issue(ticker, issue))

        # WARNING 问题
        warning_issues = [(t, i) for t, i in all_issues if i.severity == "WARNING"]
        if warning_issues:
            lines.extend([
                "## WARNING 问题（建议核查）",
                "",
            ])
            for ticker, issue in warning_issues:
                lines.extend(self._format_issue(ticker, issue))

        # INFO 发现
        info_issues = [(t, i) for t, i in all_issues if i.severity == "INFO"]
        if info_issues:
            lines.extend([
                "## INFO 发现（数据限制说明）",
                "",
            ])
            for ticker, issue in info_issues:
                lines.extend(self._format_issue(ticker, issue))

        # 各股票详细结果
        lines.extend([
            "",
            "## 各股票审计详情",
            "",
        ])

        for ticker in sorted(self.results.keys()):
            result = self.results[ticker]
            lines.extend([
                f"### {ticker}",
                "",
                f"- 审计文件数: {result.files_audited}",
                f"- CRITICAL: {result.critical_count()}",
                f"- ERROR: {result.error_count()}",
                f"- WARNING: {result.warning_count()}",
                "",
            ])

        return "\n".join(lines)

    def _format_issue(self, ticker: str, issue: AuditIssue) -> list[str]:
        """格式化单个问题"""
        lines = [
            f"### [{ticker}] {issue.rule_id}: {issue.message}",
            "",
            f"- **文件**: `{issue.file_path}`",
        ]
        if issue.expected:
            lines.append(f"- **预期**: {issue.expected}")
        if issue.actual:
            lines.append(f"- **实际**: {issue.actual}")
        if issue.suggestion:
            lines.append(f"- **建议**: {issue.suggestion}")
        lines.append("")
        return lines


# =============================================================================
# CLI入口
# =============================================================================

def _qualify_a_share(code: str) -> str | None:
    """Append the appropriate exchange suffix to a 6-digit A-share code."""
    if not code.isdigit() or len(code) != 6:
        return None
    if code.startswith(("600", "601", "603", "605", "688")):
        return f"{code}.SS"
    if code.startswith(("000", "001", "002", "003", "300")):
        return f"{code}.SZ"
    if code.startswith(("82", "83", "87", "88", "43", "92")):
        return f"{code}.BJ"
    return None


def _parse_ticker_dir_name(dir_name: str) -> str | None:
    """从批次子目录名解析 ticker 代码，解析失败返回 None。

    兼容两种目录命名（见 cli/batch_runner.py 的 _build_ticker_dir_name）：
    - 纯代码: ``000100``
    - 名称_代码: ``TCL科技_000100``、``万华化学_600309.SS``

    A 股返回去除交易所后缀的 6 位数字（供 _qualify_a_share 使用）；
    同时接受港股(1810.HK)与美股字母代码(AAPL)，避免误匹配其他目录。
    """
    code = dir_name.rsplit("_", 1)[-1].strip()
    a_share = re.fullmatch(r"(\d{6})(?:\.(?:SS|SZ|BJ))?", code, re.IGNORECASE)
    if a_share:
        return a_share.group(1)
    if re.fullmatch(r"\d{4,5}\.HK", code, re.IGNORECASE):
        return code.upper()
    if re.fullmatch(r"[A-Za-z]{1,5}(?:\.[A-Za-z]{1,3})?", code):
        return code.upper()
    return None


def _has_any_metric(metrics: FinancialMetrics) -> bool:
    """是否提取到了至少一个财务指标（不计 ticker/file_path/company_name 元数据）。"""
    return any(
        value is not None
        for name, value in asdict(metrics).items()
        if name not in ("ticker", "file_path", "company_name")
    )


def main():
    parser = argparse.ArgumentParser(description="Trading Agents Report Auditor")
    parser.add_argument("batch_dir", help="报告批次目录路径")
    parser.add_argument("--ticker", help="只审计指定股票")
    parser.add_argument("--output", "-o", default="./audit_reports", help="报告输出目录")
    parser.add_argument(
        "--cross-validate",
        action="store_true",
        help="对每只 A 股调用 akshare 实时快照，做 PE / 市值真值对照",
    )

    args = parser.parse_args()

    print(f"🔍 开始审计: {args.batch_dir}")
    if args.ticker:
        print(f"   目标股票: {args.ticker}")
    if args.cross_validate:
        print("   模式: 启用 akshare 实时交叉验证")

    auditor = ReportAuditor(args.batch_dir)
    auditor.audit(
        target_ticker=args.ticker,
        cross_validate=args.cross_validate,
    )

    report_path = auditor.generate_report(args.output)

    summary = auditor._generate_summary()
    print("\n📊 审计完成!")
    print(f"   股票数: {summary['total_tickers']}")
    print(f"   文件数: {summary['total_files_audited']}")
    print(f"   CRITICAL: {summary['total_critical']}")
    print(f"   ERROR: {summary['total_errors']}")
    print(f"   WARNING: {summary['total_warnings']}")
    print(f"   健康评分: {summary['health_score']}/100")
    print(f"\n📄 报告已生成: {report_path}")

    # 如果有CRITICAL问题，返回非零退出码
    if summary['total_critical'] > 0:
        print(f"\n⚠️  发现 {summary['total_critical']} 个CRITICAL问题，请立即修复！")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())

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
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

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
            r"\|\s*预期EPS\s*\|\s*(?:约|约为|about)?\s*([\d.]+(?:\s*-\s*[\d.]+)?)\s*\|",
        ],
        "price": [
            r"当前股价[:：\s]*[\(（]?([\d.]+)[\)）]?",
            r"Current\s+Price[:：\s]*[\(（]?([\d.]+)[\)）]?",
        ],
        "market_cap": [
            r"市值[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)",
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
            r"ROE|股本回报率[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*%",
            r"\|\s*ROE\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*%\s*\|",
        ],
        "roa": [
            r"ROA|资产回报率[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*%",
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
            r"负债权益比|Debt-to-Equity[:：\s]*(?:约|约为|about)?\s*([\d.]+)",
            r"\|\s*负债权益比\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*\|",
        ],
        "current_ratio": [
            r"流动比率|Current\s+Ratio[:：\s]*(?:约|约为|about)?\s*([\d.]+)",
            r"\|\s*流动比率\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*\|",
        ],
        "operating_cash_flow": [
            r"(?:经营|经营活动)现金流[:：\s]*(?:约|约为|about)?\s*([\-\d.]+)\s*(?:亿元|亿)",
            r"\|\s*经营现金流\s*\|\s*(?:约|约为|about)?\s*([\-\d.]+)\s*(?:亿元|亿)\s*\|",
        ],
        "free_cash_flow": [
            r"自由现金流|Free\s+Cash\s+Flow|FCF[:：\s]*(?:约|约为|about)?\s*([\-\d.]+)\s*(?:亿元|亿)",
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
            r"EPS增长|每股收益增长[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*%",
            r"\|\s*预期EPS增长\s*\|\s*(?:约|约为|about)?\s*([\d.]+)\s*%\s*\|",
        ],
        "rd_ratio": [
            r"(?:研发费用|研发投入)[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*(?:亿元|亿)",
        ],
        "dividend_yield": [
            r"股息率|dividend\s+yield[:：\s]*(?:约|约为|about)?\s*([\d.]+)\s*%",
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

    def extract(self, text: str, ticker: str, file_path: str) -> FinancialMetrics:
        """从文本中提取所有指标"""
        text = self._preprocess_text(text)
        metrics = FinancialMetrics(ticker=ticker, file_path=file_path)

        for field_name, patterns in self.PATTERNS.items():
            for pattern in patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                if matches:
                    value_str = matches[-1]
                    if isinstance(value_str, tuple):
                        value_str = value_str[0]

                    parsed = self._parse_value(value_str, field_name)
                    if parsed is not None:
                        setattr(metrics, field_name, parsed)
                        break

        # 提取公司名称
        name_match = re.search(r'#\s*(.+?)[\(（]\d{6}', text)
        if name_match:
            metrics.company_name = name_match.group(1).strip()

        return metrics

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


# =============================================================================
# 主审计引擎
# =============================================================================

class ReportAuditor:
    """报告审计主引擎"""

    def __init__(self, batch_dir: str):
        self.batch_dir = Path(batch_dir)
        self.extractor = MetricsExtractor()
        self.results: dict[str, AuditResult] = {}

    def audit(
        self,
        target_ticker: str | None = None,
        cross_validate: bool = False,
    ) -> dict[str, AuditResult]:
        """执行审计

        cross_validate=True 时调用 akshare_realtime.fetch_realtime_snapshot
        对每只 A 股做 PE / 市值对照，命中偏差 > 10% 即追加 REALTIME-* issue。
        """
        tickers = [d.name for d in self.batch_dir.iterdir() if d.is_dir() and d.name.isdigit()]

        if target_ticker:
            tickers = [t for t in tickers if t == target_ticker]

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

        for ticker in sorted(tickers):
            result = self._audit_ticker(ticker)
            if snapshot_fn is not None:
                self._apply_realtime_validation(ticker, result, snapshot_fn)
            self.results[ticker] = result

        # 跨文件一致性检查
        cross_issues = check_cross_file_consistency(self.results)
        for issue in cross_issues:
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

    def _audit_ticker(self, ticker: str) -> AuditResult:
        """审计单个股票的所有文件"""
        ticker_dir = self.batch_dir / ticker
        result = AuditResult(ticker=ticker)

        # 遍历所有MD文件
        md_files = list(ticker_dir.rglob("*.md"))
        result.files_audited = len(md_files)

        for md_file in md_files:
            try:
                text = md_file.read_text(encoding='utf-8')
                metrics = self.extractor.extract(text, ticker, str(md_file))
                result.metrics[str(md_file)] = metrics

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

        return {
            "total_tickers": len(self.results),
            "total_files_audited": total_files,
            "total_critical": total_critical,
            "total_errors": total_errors,
            "total_warnings": total_warnings,
            "health_score": max(0, 100 - total_critical * 10 - total_errors * 5 - total_warnings * 1),
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

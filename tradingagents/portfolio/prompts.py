"""Prompt builders for portfolio-aware agent integration.

Converts structured Holding/Portfolio data into markdown prompt fragments
that can be injected into specific agents' prompts.
"""
from __future__ import annotations

from tradingagents.portfolio.models import Portfolio, Transaction


def _format_money(value: float | None) -> str:
    """Format a monetary value with Chinese-friendly units."""
    if value is None:
        return "N/A"
    abs_v = abs(value)
    if abs_v >= 1e8:
        return f"{value / 1e8:.2f}亿"
    if abs_v >= 1e4:
        return f"{value / 1e4:.2f}万"
    return f"{value:,.2f}"


def _build_transactions_text(ticker: str, transactions: list[Transaction]) -> str:
    """Format transaction history for a specific ticker into markdown."""
    from tradingagents.portfolio.validators import ticker_matches

    txs = [t for t in transactions if ticker_matches(t.ticker, ticker)]
    if not txs:
        return ""

    # Sort by date descending (newest first)
    txs_sorted = sorted(txs, key=lambda t: t.date, reverse=True)

    lines = ["", "## 近期交易记录", ""]

    for t in txs_sorted[:10]:  # Show last 10 transactions
        fee_str = f"，手续费 {_format_money(t.fee)}" if t.fee else ""
        tag_str = f"（{t.tag}）" if t.tag else ""
        lines.append(
            f"- {t.date} {t.action} {abs(t.shares):,.0f} 股 @ {t.price:.3f}"
            f"{fee_str}{tag_str}"
        )

    # Summary stats
    buy_count = sum(1 for t in txs if t.action == "买入")
    sell_count = sum(1 for t in txs if t.action == "卖出")
    div_count = sum(1 for t in txs if t.action == "分红")
    lines.extend([
        "",
        f"**交易统计**: 买入 {buy_count} 次，卖出 {sell_count} 次"
        + (f"，分红 {div_count} 次" if div_count else "")
        + f"，共 {len(txs)} 笔。",
    ])

    return "\n".join(lines)


def build_pm_prompt(
    ticker: str, portfolio: Portfolio | None, transactions: list[Transaction] | None = None
) -> str:
    """Build holdings context for Portfolio Manager.

    PM needs the full picture: shares, cost, current P&L, and weight
    to make position-aware decisions (e.g. don't add to a heavy loser).
    """
    if not portfolio or not portfolio.holdings:
        return ""

    tx_list = transactions if transactions is not None else portfolio.transactions

    if not portfolio.has_holding(ticker):
        lines = [
            "## 当前持仓信息",
            "",
            "- 当前实际持仓: 无持仓（空仓，0 股）。",
            "⚠️ 注意：请勿将历史记忆（Past Lessons / Decisions）中的历史仓位或成本当成当前现有底仓。",
        ]
        tx_text = _build_transactions_text(ticker, tx_list)
        if tx_text:
            lines.append(tx_text)
        return "\n".join(lines)

    h = portfolio.get_holding(ticker)
    if h is None:
        return ""
    lines = ["## 当前持仓信息", ""]

    if h.name:
        lines.append(f"- 股票名称: {h.name}")
    lines.append(f"- 持仓数量: {h.shares:,.0f} 股")
    lines.append(f"- 成本价: {h.avg_cost:.3f}")

    if h.market_price is not None:
        lines.append(f"- 现价: {h.market_price:.3f}")
    if h.pnl_pct is not None:
        sign = "+" if h.pnl_pct >= 0 else ""
        lines.append(f"- 盈亏率: {sign}{h.pnl_pct * 100:.2f}%")
    if h.weight is not None:
        lines.append(f"- 仓位占比: {h.weight * 100:.2f}%")
    if h.invested_amount is not None:
        lines.append(f"- 投入本金: {_format_money(h.invested_amount)}")
    if h.grid_strategy:
        lines.append(f"- 网格策略: {h.grid_strategy}")

    lines.extend([
        "",
        "请在给出评级时综合考虑当前持仓：",
        "- 若已重仓（仓位占比高）且信号中性，建议维持或减仓；",
        "- 若已亏损且信号看跌，建议评估止损而非加仓；",
        "- 若有网格策略，请结合网格区间评估操作空间。",
    ])

    tx_text = _build_transactions_text(ticker, tx_list)
    if tx_text:
        lines.append(tx_text)

    return "\n".join(lines)


def build_risk_prompt(
    ticker: str, portfolio: Portfolio | None, transactions: list[Transaction] | None = None
) -> str:
    """Build holdings context for Risk Analysts.

    Risk analysts care about concentration risk and drawdown.
    """
    if not portfolio or not portfolio.holdings:
        return ""

    tx_list = transactions if transactions is not None else portfolio.transactions

    if not portfolio.has_holding(ticker):
        lines = [
            "## 当前风险相关信息",
            "",
            "- 该股票当前仓位占比: 0.00%（无持仓/空仓）。",
        ]
        tx_text = _build_transactions_text(ticker, tx_list)
        if tx_text:
            lines.append(tx_text)
        return "\n".join(lines)

    h = portfolio.get_holding(ticker)
    if h is None:
        return ""
    lines = ["## 当前风险相关信息", ""]

    if h.weight is not None:
        lines.append(f"- 该股票仓位占比: {h.weight * 100:.2f}%")
        if h.weight > 0.15:
            lines.append("  ⚠️ 该票仓位已超过组合 15%，属于集中持仓，请注意风险。")
    if h.pnl_pct is not None:
        sign = "+" if h.pnl_pct >= 0 else ""
        lines.append(f"- 当前浮盈/浮亏: {sign}{h.pnl_pct * 100:.2f}%")
        if h.pnl_pct < -0.20:
            lines.append("  ⚠️ 该票已亏损超过 20%，若信号继续看空建议严格止损。")

    tx_text = _build_transactions_text(ticker, tx_list)
    if tx_text:
        lines.append(tx_text)

    lines.append("\n请在风控评估中考虑上述持仓风险。")
    return "\n".join(lines)


def build_trader_prompt(
    ticker: str, portfolio: Portfolio | None, transactions: list[Transaction] | None = None
) -> str:
    """Build holdings context for Trader.

    Trader cares about grid strategies and position sizing.
    """
    if not portfolio or not portfolio.holdings:
        return ""

    tx_list = transactions if transactions is not None else portfolio.transactions

    if not portfolio.has_holding(ticker):
        lines = [
            "## 交易执行参考",
            "",
            "- 当前持仓: 0 股（当前无底仓/空仓）。",
            "⚠️ 注意：请勿将历史记录中的历史持仓当成当前底仓；若给出买入建议，请按全新开仓/首笔建仓规则设定 Position Sizing。",
        ]
        tx_text = _build_transactions_text(ticker, tx_list)
        if tx_text:
            lines.append(tx_text)
        return "\n".join(lines)

    h = portfolio.get_holding(ticker)
    if h is None:
        return ""
    lines = ["## 交易执行参考", ""]

    lines.append(f"- 当前持仓: {h.shares:,.0f} 股，成本 {h.avg_cost:.3f}")

    if h.grid_strategy:
        lines.append(f"- 网格策略: {h.grid_strategy}")
        lines.append("  请在设定 entry price 和 stop-loss 时参考上述网格区间。")

    if h.market_price is not None and h.avg_cost:
        gap = (h.market_price - h.avg_cost) / h.avg_cost
        sign = "+" if gap >= 0 else ""
        lines.append(f"- 现价与成本价差: {sign}{gap * 100:.2f}%")

    tx_text = _build_transactions_text(ticker, tx_list)
    if tx_text:
        lines.append(tx_text)

    lines.append("\n请结合当前持仓成本和网格策略（如有）给出具体的交易方案。")
    return "\n".join(lines)


def build_market_prompt(ticker: str, portfolio: Portfolio | None) -> str:
    """Build holdings context for Market Analyst.

    Market analyst cares about price levels relative to cost basis.
    """
    if not portfolio or not portfolio.has_holding(ticker):
        return ""

    h = portfolio.get_holding(ticker)
    if h is None:
        return ""
    lines = ["## 持仓成本参考", ""]

    lines.append(f"- 成本价: {h.avg_cost:.3f}")
    if h.market_price is not None:
        lines.append(f"- 现价: {h.market_price:.3f}")
        if not h.avg_cost:
            # 零成本持仓（如获赠股份）——无法计算盈亏百分比，跳过。
            pass
        elif h.market_price < h.avg_cost:
            gap = (h.avg_cost - h.market_price) / h.avg_cost
            lines.append(f"- 当前浮亏: {gap * 100:.2f}%（成本线构成心理压力位）")
        else:
            gap = (h.market_price - h.avg_cost) / h.avg_cost
            lines.append(f"- 当前浮盈: {gap * 100:.2f}%（成本线构成支撑）")

    lines.append("\n请在技术分析中考虑成本价附近的可能支撑/压力行为。")
    return "\n".join(lines)

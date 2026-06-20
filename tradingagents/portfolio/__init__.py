"""Portfolio management module for TradingAgents.

Provides a decoupled three-layer architecture:
1. Sync layer (Google Sheet → local JSON)
2. Repository layer (local JSON read/write)
3. Prompt layer (structured holdings → agent prompts)

Usage:
    from tradingagents.portfolio import PortfolioRepository, PortfolioSyncService

    # Sync from Google Sheet
    sync = PortfolioSyncService(sheet_id="...", worksheet="total")
    portfolio = sync.sync()

    # Read from local cache
    repo = PortfolioRepository()
    portfolio = repo.load()
"""
from __future__ import annotations

from tradingagents.portfolio.models import Holding, Portfolio, PortfolioMetadata, Transaction
from tradingagents.portfolio.prompts import (
    build_market_prompt,
    build_pm_prompt,
    build_risk_prompt,
    build_trader_prompt,
)
from tradingagents.portfolio.repository import PortfolioRepository
from tradingagents.portfolio.sync import PortfolioSyncService
from tradingagents.portfolio.transaction_sync import TransactionSyncService
from tradingagents.portfolio.validators import normalize_ticker, validate_holding

__all__ = [
    "Holding",
    "Portfolio",
    "PortfolioMetadata",
    "Transaction",
    "PortfolioRepository",
    "PortfolioSyncService",
    "TransactionSyncService",
    "build_pm_prompt",
    "build_risk_prompt",
    "build_trader_prompt",
    "build_market_prompt",
    "normalize_ticker",
    "validate_holding",
]

"""Portfolio domain models.

Defines the core data structures for holdings management:
- Holding: a single position
- Portfolio: the complete holdings collection
- PortfolioMetadata: sync source and timestamp
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Holding:
    """A single position in the portfolio."""

    ticker: str
    name: str = ""
    shares: float = 0.0
    avg_cost: float = 0.0
    market_price: float | None = None
    invested_amount: float | None = None
    market_value: float | None = None
    pnl_amount: float | None = None
    pnl_pct: float | None = None
    weight: float | None = None
    grid_strategy: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict, excluding None values."""
        return {k: v for k, v in asdict(self).items() if v is not None and v != ""}

    @classmethod
    def from_dict(cls, data: dict[str, Any], ticker: str | None = None) -> Holding:
        """Deserialize from dict.

        Args:
            data: The holding dict. May omit ``ticker`` when the caller already
                knows it (e.g. legacy flat holdings_context format).
            ticker: Fallback ticker when ``data`` does not contain the key.
        """
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        if "ticker" not in filtered and ticker:
            filtered["ticker"] = ticker
        return cls(**filtered)


@dataclass
class PortfolioMetadata:
    """Metadata about the portfolio data source and sync state."""

    version: str = "1.0"
    updated_at: str | None = None
    source_type: str | None = None
    source_sheet_id: str | None = None
    source_worksheet: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PortfolioMetadata:
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class Transaction:
    """A single trade transaction."""

    date: str
    ticker: str
    name: str = ""
    price: float = 0.0
    action: str = ""  # 买入/卖出/分红
    shares: float = 0.0  # 正数为买入，负数为卖出
    fee: float | None = None
    cash_change: float | None = None  # 负数为买入支出，正数为卖出收入
    tag: str | None = None  # 手动建仓/分批卖出/分红

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v != ""}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transaction:
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class Portfolio:
    """The complete portfolio holdings with metadata and summary."""

    holdings: dict[str, Holding] = field(default_factory=dict)
    metadata: PortfolioMetadata = field(default_factory=PortfolioMetadata)
    summary: dict[str, Any] = field(default_factory=dict)
    transactions: list[Transaction] = field(default_factory=list)

    def get_holding(self, ticker: str) -> Holding | None:
        """Get a specific holding by ticker."""
        return self.holdings.get(ticker)

    def has_holding(self, ticker: str) -> bool:
        """Check if portfolio contains a specific ticker."""
        return ticker in self.holdings

    def total_invested(self) -> float:
        """Sum of all invested amounts."""
        return sum(
            h.invested_amount or h.shares * h.avg_cost
            for h in self.holdings.values()
        )

    def total_market_value(self) -> float:
        """Sum of all market values."""
        return sum(
            h.market_value or (h.market_price or h.avg_cost) * h.shares
            for h in self.holdings.values()
        )

    def total_pnl(self) -> float:
        """Total P&L amount."""
        return self.total_market_value() - self.total_invested()

    def to_dict(self) -> dict[str, Any]:
        """Serialize entire portfolio to dict for JSON storage."""
        return {
            "metadata": self.metadata.to_dict(),
            "holdings": {
                ticker: h.to_dict() for ticker, h in self.holdings.items()
            },
            "summary": self.summary,
            "transactions": [t.to_dict() for t in self.transactions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Portfolio:
        """Deserialize from dict."""
        holdings = {
            ticker: Holding.from_dict(h_data, ticker=ticker)
            for ticker, h_data in data.get("holdings", {}).items()
        }
        metadata = PortfolioMetadata.from_dict(data.get("metadata", {}))
        summary = data.get("summary", {})
        transactions = [
            Transaction.from_dict(t_data)
            for t_data in data.get("transactions", [])
        ]
        return cls(
            holdings=holdings,
            metadata=metadata,
            summary=summary,
            transactions=transactions,
        )

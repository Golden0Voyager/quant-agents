"""Batch-specific dashboard that extends the generic AnalysisDashboard."""


from cli.dashboard import AnalysisDashboard


class BatchDashboard(AnalysisDashboard):
    """Holds mutable state for the batch analysis dashboard.

    Extends AnalysisDashboard with batch-level counters and
    delegates all rendering to cli.dashboard.
    """

    def __init__(self, total: int, profile_name: str):
        super().__init__()
        self.total = total
        self.profile_name = profile_name
        self.completed = 0
        self.failed = 0
        self.skipped = 0
        self.skipped_tickers: list[str] = []
        self.current_ticker: str | None = None
        self.readiness_ready: int = 0
        self.readiness_total: int = 0
        self.per_ticker_meta: dict[str, dict] = {}  # ticker -> {stage, progress, agent}

    def update_progress(self, current_ticker: str | None, completed: int, failed: int) -> None:
        self.current_ticker = current_ticker
        self.completed = completed
        self.failed = failed

    def update_ticker_meta(self, ticker: str, stage: str, progress: float, agent: str) -> None:
        self.per_ticker_meta[ticker] = {
            "stage": stage,
            "progress": progress,
            "agent": agent,
        }

    def mark_skipped(self, ticker: str) -> None:
        """Record a ticker as skipped (already completed from a previous run)."""
        self.skipped += 1
        self.skipped_tickers.append(ticker)

    def reset_for_next_stock(self) -> None:
        """Clear per-stock state when moving to the next ticker."""
        self.reset_per_stock(clear_messages=False)

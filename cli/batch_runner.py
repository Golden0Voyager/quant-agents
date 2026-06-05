import os

# Batch mode is unattended — tqdm progress bars from akshare/yfinance/third-party
# libraries spam the terminal and break the Rich TUI layout. Disable globally.
os.environ["TQDM_DISABLE"] = "1"

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from cli.batch_dashboard import BatchDashboard
from cli.dashboard import create_dashboard_layout, update_dashboard_display, process_stream_chunk
from cli.stats_handler import StatsCallbackHandler
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from rich.live import Live
from rich.console import Console

console = Console()


class BatchRunner:
    """Orchestrates unattended multi-stock analysis."""

    def __init__(
        self,
        tickers: list[str],
        profile_config: dict,
        output_dir: Path,
        checkpoint: bool = False,
        holdings: dict | None = None,
        workers: int = 1,
    ):
        self.tickers = tickers
        self.profile_config = profile_config
        self.output_dir = Path(output_dir)
        self.checkpoint = checkpoint
        self.holdings = self._resolve_holdings(holdings)
        self.workers = workers
        self.completed_tickers: set[str] = set()
        self.failures: dict[str, str] = {}
        self.summaries: dict[str, dict] = {}
        self.dashboard = BatchDashboard(total=len(tickers), profile_name=profile_config.get("name", "default"))
        # Set by run() once the Live context owns these — _refresh_display() reads
        # them. When unset (e.g. tests calling _run_single directly), refresh is a no-op.
        self._layout = None
        self._start_time: Optional[float] = None
        # Protect shared mutable state across worker threads
        self._lock = threading.Lock()

    def _refresh_display(self, stats_handler=None) -> None:
        """Push current dashboard state into the Live-managed layout.

        Safe no-op when no Live context is active.
        """
        if self._layout is None or self._start_time is None:
            return
        elapsed = time.time() - self._start_time
        update_dashboard_display(
            self._layout,
            self.dashboard,
            ticker=self.dashboard.current_ticker or "",
            stats_handler=stats_handler,
            start_time=self._start_time,
            batch_completed=self.dashboard.completed,
            batch_total=self.dashboard.total,
            batch_failed=self.dashboard.failed,
            profile_name=self.dashboard.profile_name,
        )

    @staticmethod
    def _resolve_holdings(holdings: dict | None) -> dict:
        """Return provided holdings, or load from local cache if None."""
        if holdings is not None:
            return holdings
        try:
            from tradingagents.portfolio import PortfolioRepository
            repo = PortfolioRepository()
            if repo.exists():
                portfolio = repo.load()
                return {
                    ticker: {
                        "shares": h.shares,
                        "avg_cost": h.avg_cost,
                        "market_price": h.market_price,
                        "pnl_pct": h.pnl_pct,
                        "weight": h.weight,
                        "grid_strategy": h.grid_strategy,
                        "name": h.name,
                    }
                    for ticker, h in portfolio.holdings.items()
                }
        except Exception:
            pass
        return {}

    @staticmethod
    def _parse_report_analysis_date(path: Path) -> str | None:
        """Parse the Analysis Date line from a complete_report.md header."""
        try:
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines()[:30]:
                if line.startswith("Analysis Date:"):
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
        return None

    @staticmethod
    def _build_ticker_dir_name(ticker: str, company_name: str = "") -> str:
        """Build directory name: 中文名称_代码 or 代码."""
        name = (company_name or "").strip()
        if name:
            return f"{name}_{ticker}"
        return ticker

    def _is_already_completed(self, ticker: str) -> bool:
        """Check if ticker was already analysed for the target date.

        Scans the current output directory *and* all historical batch_* folders
        under the same ``reports/`` root.  A report is considered a match only
        when its embedded ``Analysis Date`` equals the configured
        ``analysis_date`` (falls back to today for legacy reports).
        """
        target_date = self.profile_config.get("analysis_date") or datetime.now().strftime("%Y-%m-%d")

        # Candidate folder names: raw ticker + resolved suffix variants + named variants
        candidates = {ticker}
        try:
            from tradingagents.ticker_resolver import resolve_ticker
            resolved = resolve_ticker(ticker)
            candidates.add(resolved["ticker"])
            company_name = resolved.get("company_name", "")
            if company_name:
                candidates.add(self._build_ticker_dir_name(ticker, company_name))
                candidates.add(self._build_ticker_dir_name(resolved["ticker"], company_name))
        except Exception:
            pass

        def _has_matching_report(path: Path) -> bool:
            if not path.exists() or path.stat().st_size == 0:
                return False
            report_date = self._parse_report_analysis_date(path)
            if report_date:
                return report_date == target_date
            # Fallback for legacy reports without Analysis Date line
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            return mtime.date() == datetime.now().date()

        # 1. Check current batch directory
        for cand in candidates:
            if _has_matching_report(self.output_dir / cand / "complete_report.md"):
                return True

        # 2. Check historical batch_* directories
        reports_dir = self.output_dir.parent
        if reports_dir.exists():
            for batch_dir in reports_dir.glob("batch_*"):
                for cand in candidates:
                    if _has_matching_report(batch_dir / cand / "complete_report.md"):
                        return True

        return False

    def _build_config(self) -> dict:
        config = DEFAULT_CONFIG.copy()
        config["max_debate_rounds"] = self.profile_config.get("research_depth", 1)
        config["max_risk_discuss_rounds"] = self.profile_config.get("research_depth", 1)
        config["quick_think_llm"] = (
            self.profile_config.get("shallow_thinker")
            or self.profile_config.get("quick_think_llm")
            or config["quick_think_llm"]
        )
        config["deep_think_llm"] = (
            self.profile_config.get("deep_thinker")
            or self.profile_config.get("deep_think_llm")
            or config["deep_think_llm"]
        )
        config["backend_url"] = self.profile_config.get("backend_url")
        config["llm_provider"] = self.profile_config.get("llm_provider", config["llm_provider"]).lower()
        config["google_thinking_level"] = self.profile_config.get("google_thinking_level")
        config["openai_reasoning_effort"] = self.profile_config.get("openai_reasoning_effort")
        config["anthropic_effort"] = self.profile_config.get("anthropic_effort")
        config["output_language"] = self.profile_config.get("output_language", "English")
        config["checkpoint_enabled"] = self.checkpoint
        return config

    def _run_single(self, ticker: str) -> dict:
        """Run analysis for a single ticker. Returns final state dict."""
        from cli.main import (
            ANALYST_ORDER,
            save_report_to_disk,
        )
        from tradingagents.ticker_resolver import resolve_ticker

        # Resolve ticker (A-share numeric codes get .SS/.SZ/.BJ suffix)
        resolved = resolve_ticker(ticker)
        resolved_ticker = resolved["ticker"]
        company_name = resolved.get("company_name", "")

        config = self._build_config()
        selected_analyst_keys = [a for a in ANALYST_ORDER if a in self.profile_config.get("analysts", [])]
        if not selected_analyst_keys:
            selected_analyst_keys = ["market"]
        self.dashboard.init_for_analysis(selected_analyst_keys)

        stats_handler = StatsCallbackHandler()
        graph = TradingAgentsGraph(
            selected_analyst_keys,
            config=config,
            debug=True,
            callbacks=[stats_handler],
        )

        trade_date = self.profile_config.get("analysis_date") or datetime.now().strftime("%Y-%m-%d")

        # Load transaction history for injection
        transactions_context: list[dict] = []
        try:
            from tradingagents.portfolio import PortfolioRepository
            repo = PortfolioRepository()
            if repo.exists():
                portfolio = repo.load()
                transactions_context = [t.to_dict() for t in portfolio.transactions]
        except Exception:
            pass

        init_state = graph.propagator.create_initial_state(
            resolved_ticker,
            trade_date,
            holdings_context=self.holdings,
            transactions_context=transactions_context,
        )
        if company_name:
            init_state["company_name"] = company_name
        args = graph.propagator.get_graph_args(callbacks=[stats_handler])

        # ── Checkpoint / resume support ────────────────────────────
        # When checkpoint is enabled, recompile the graph with a per-ticker
        # SqliteSaver so a crashed / stuck run can resume from the last
        # successful node instead of restarting from scratch.
        checkpointer_ctx = None
        if config.get("checkpoint_enabled"):
            from tradingagents.graph.checkpointer import (
                get_checkpointer,
                checkpoint_step,
                clear_checkpoint,
                thread_id,
            )

            checkpointer_ctx = get_checkpointer(
                config["data_cache_dir"], resolved_ticker
            )
            saver = checkpointer_ctx.__enter__()
            graph.graph = graph.workflow.compile(checkpointer=saver)

            step = checkpoint_step(
                config["data_cache_dir"], resolved_ticker, trade_date
            )
            if step is not None:
                self.dashboard.add_message(
                    "Resume",
                    f"▶ Resuming {ticker} from checkpoint (step {step})",
                )
            else:
                self.dashboard.add_message(
                    "Info", f"Starting fresh analysis for {ticker}"
                )

            # Inject thread_id so the same ticker+date resumes correctly.
            tid = thread_id(resolved_ticker, trade_date)
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid

        try:
            trace = []
            processed_ids: set = set()
            max_debate = config.get("max_debate_rounds", 1)
            max_risk = config.get("max_risk_discuss_rounds", 1)

            for chunk in graph.graph.stream(init_state, **args):
                processed_ids = process_stream_chunk(
                    self.dashboard,
                    chunk,
                    max_debate_rounds=max_debate,
                    max_risk_rounds=max_risk,
                    processed_ids=processed_ids,
                )
                self._refresh_display(stats_handler=stats_handler)
                trace.append(chunk)

            final_state = trace[-1] if trace else {}

            # Save report
            ticker_dir_name = self._build_ticker_dir_name(ticker, company_name)
            ticker_dir = self.output_dir / ticker_dir_name
            ticker_dir.mkdir(parents=True, exist_ok=True)
            save_report_to_disk(final_state, ticker, ticker_dir)

            # Extract summary (lock-protected for concurrent batch mode)
            with self._lock:
                self._extract_summary(ticker, final_state)

            # Clear checkpoint on successful completion to avoid stale state.
            if config.get("checkpoint_enabled"):
                clear_checkpoint(
                    config["data_cache_dir"], resolved_ticker, trade_date
                )

            return final_state
        finally:
            # Always close the checkpointer context and restore the plain graph.
            if checkpointer_ctx is not None:
                checkpointer_ctx.__exit__(None, None, None)
                graph.graph = graph.workflow.compile()

    def _extract_summary(self, ticker: str, final_state: dict) -> None:
        """Extract portfolio decision for batch summary.

        Supports both English (structured-output) and Chinese (free-text fallback)
        decision formats. When the LLM falls back to free text because the provider
        lacks structured-output support, the prose may use Chinese labels such as
        ``评级：减持`` or ``止损价 24.30``.
        """
        import re

        decision = final_state.get("final_trade_decision", "")
        company = final_state.get("company_name", "")

        # Fallback: try to extract company name from report text if missing in state
        if not company and decision:
            # Try patterns like "## 600050.SS - 中国联通 投资分析" or "**公司名称**: 中国联通"
            company_patterns = [
                r"#+\s*(?:\S+\s+)?-\s*([^\n\(（]{2,20}?)\s*(?:\(|（|\n|$)",
                r"(?:公司名称|Company Name)[:：]\s*([^\n]{2,20})",
                r"关于\s*([^\n\(（]{2,20}?)\s*(?:\(|（|\d{6})",
            ]
            for pattern in company_patterns:
                m = re.search(pattern, decision)
                if m:
                    candidate = m.group(1).strip()
                    # Filter out pure ticker codes or numeric values
                    if candidate and not re.match(r"^\d+$", candidate):
                        company = candidate
                        break

        def _find_strict_numeric(names: str) -> str:
            """Extract numeric/percentage values only."""
            # Key-value: strictly capture numeric values (digits, decimal, %)
            # Tolerates surrounding markdown bold ** and trailing unit words.
            patterns = [
                rf"(?:^|\n|\|)\s*\*?\*?(?:{names})\*?\*?\s*[:：]\s*\*?\*?([0-9]+%?(?:\.[0-9]+)?)\s*(?:USD|CNY|元|%)?\*?\*?(?:\s|$|\|)",
                rf"(?:^|\n|\|)\s*\*?\*?(?:{names})\*?\*?\s*[:：]\s*\*?\*?([0-9]+%?(?:\.[0-9]+)?(?:\s*-\s*[0-9]+%?(?:\.[0-9]+)?)?)\s*(?:USD|CNY|元|%)?\*?\*?(?:\s|$|\|)",
            ]
            for pattern in patterns:
                m = re.search(pattern, decision, re.IGNORECASE | re.MULTILINE)
                if m:
                    return m.group(1).strip()
            return ""

        def _find_flexible(names: str) -> str:
            """Fallback: more lenient matching for non-standard formats."""
            m = re.search(
                rf"(?:^|\n|\|)\s*\*?\*?(?:{names})\*?\*?\s*[:：]\s*\*?\*?([^\n|]+?)(?:\*\*|\n|\||$)",
                decision,
                re.IGNORECASE | re.MULTILINE,
            )
            if m:
                return m.group(1).strip()
            return ""

        def _find(names: str) -> str:
            """Prefer strict numeric extraction, fallback to flexible."""
            val = _find_strict_numeric(names)
            if val:
                return val
            return _find_flexible(names)

        # Rating — key-value or quoted like 「持有」评级
        rating_raw = ""
        rating_m = re.search(
            r"(?:\*\*)?(?:Rating|Decision|评级|建议|决策|结论)(?:\*\*)?\s*[:：]\s*(?:\*\*)?([\w一-鿿]+)(?:\*\*)?",
            decision,
            re.IGNORECASE,
        )
        if rating_m:
            rating_raw = rating_m.group(1)
        else:
            rating_m = re.search(
                r"[\"「【]([\w一-鿿]+)[\"」】]\s*(?:评级|建议|决策|结论)",
                decision,
                re.IGNORECASE,
            )
            if rating_m:
                rating_raw = rating_m.group(1)

        from tradingagents.agents.utils.rating import parse_rating

        rating = parse_rating(rating_raw) if rating_raw else "—"

        entry = _find(r"Entry|entry_price|入场价|买入价|目标价")
        stop = _find(r"Stop|stop_loss|止损价|止损线|止损")
        size = _find(r"Size|position_size|position_sizing|仓位上限|仓位|持仓比例|仓位占比")

        def _clean(val: str) -> str:
            if not val:
                return val
            # Remove prefix symbols
            val = re.sub(r"^[≤≥~≈]\s*", "", val)
            # Remove CJK characters and common non-numeric text
            val = re.sub(r"[一-鿿]", "", val)
            # Remove everything except digits, dot, dash (for ranges), and %
            val = re.sub(r"[^0-9.\-%]", "", val)
            # Clean up leading/trailing non-numeric chars
            val = re.sub(r"^[^0-9.]+", "", val)
            val = re.sub(r"[^0-9.%]+$", "", val)
            return val.strip()

        def _validate_numeric(val: str) -> bool:
            """Check if cleaned value contains at least one digit."""
            return bool(val) and bool(re.search(r"[0-9]", val))

        cleaned_entry = _clean(entry)
        cleaned_stop = _clean(stop)
        cleaned_size = _clean(size)

        self.summaries[ticker] = {
            "company": company or ticker,
            "rating": rating,
            "entry": cleaned_entry if _validate_numeric(cleaned_entry) else "—",
            "stop": cleaned_stop if _validate_numeric(cleaned_stop) else "—",
            "size": cleaned_size if _validate_numeric(cleaned_size) else "—",
        }

    def run(self) -> None:
        """Run the full batch."""
        layout = create_dashboard_layout()
        start_time = time.time()
        self.dashboard.update_progress(self.tickers[0] if self.tickers else None, 0, 0)

        self._layout = layout
        self._start_time = start_time

        with Live(layout, refresh_per_second=4) as live:
            # Push an initial frame so the user sees something other than empty
            # panels for the few seconds before the first node fires.
            update_dashboard_display(
                layout,
                self.dashboard,
                ticker=self.dashboard.current_ticker or "",
                start_time=start_time,
                batch_completed=0,
                batch_total=self.dashboard.total,
                batch_failed=0,
                profile_name=self.dashboard.profile_name,
            )

            for idx, ticker in enumerate(self.tickers):
                if self._is_already_completed(ticker):
                    self.completed_tickers.add(ticker)
                    self.dashboard.mark_skipped(ticker)
                    self.dashboard.add_message("Skip", f"⏭ {ticker} — report already exists, skipping")
                    self.dashboard.update_progress(
                        ticker,
                        len(self.completed_tickers) - len(self.failures),
                        len(self.failures),
                    )
                    update_dashboard_display(
                        layout,
                        self.dashboard,
                        ticker=self.dashboard.current_ticker or ticker,
                        start_time=start_time,
                        batch_completed=len(self.completed_tickers) - len(self.failures),
                        batch_total=self.dashboard.total,
                        batch_failed=len(self.failures),
                        profile_name=self.dashboard.profile_name,
                    )
                    continue

                self.dashboard.reset_for_next_stock()
                self.dashboard.update_progress(ticker, len(self.completed_tickers), len(self.failures))
                self.dashboard.update_agent_status("Market Analyst", "in_progress")
                update_dashboard_display(
                    layout,
                    self.dashboard,
                    ticker=ticker,
                    start_time=start_time,
                    batch_completed=len(self.completed_tickers),
                    batch_total=self.dashboard.total,
                    batch_failed=len(self.failures),
                    profile_name=self.dashboard.profile_name,
                )

                try:
                    self._run_single(ticker)
                    self.completed_tickers.add(ticker)
                except Exception as e:
                    self.failures[ticker] = str(e)
                    self.completed_tickers.add(ticker)
                    # Write failure log
                    self.output_dir.mkdir(parents=True, exist_ok=True)
                    failures_path = self.output_dir / "failures.log"
                    with open(failures_path, "a", encoding="utf-8") as f:
                        f.write(f"{ticker}: {e}\n")

                self.dashboard.update_progress(ticker, len(self.completed_tickers) - len(self.failures), len(self.failures))
                update_dashboard_display(
                    layout,
                    self.dashboard,
                    ticker=ticker,
                    start_time=start_time,
                    batch_completed=self.dashboard.completed,
                    batch_total=self.dashboard.total,
                    batch_failed=self.dashboard.failed,
                    profile_name=self.dashboard.profile_name,
                )

        # Live closed — release the layout reference so any stray _refresh_display
        # call from finalization code becomes a no-op instead of writing to a dead layout.
        self._layout = None
        self._start_time = None

    def generate_summary(self) -> Path:
        """Generate batch_summary.md and batch_summary.json. Returns path to markdown."""
        lines = ["# Batch Analysis Report\n"]
        lines.append("| Ticker | Company | Rating | Entry | Stop | Size | Status | Details |")
        lines.append("|--------|---------|--------|-------|------|------|--------|---------|")

        all_tickers = sorted(
            set(self.tickers) | set(self.summaries.keys()) | set(self.failures.keys())
        )
        json_rows = []
        for ticker in all_tickers:
            if ticker in self.failures:
                lines.append(f"| {ticker} | — | — | — | — | — | ❌ | — |")
                json_rows.append({
                    "ticker": ticker,
                    "company": None,
                    "rating": None,
                    "entry": None,
                    "stop": None,
                    "size": None,
                    "status": "failed",
                    "error": self.failures[ticker],
                })
            else:
                s = self.summaries.get(ticker, {})
                dir_name = self._build_ticker_dir_name(ticker, s.get("company", ""))
                lines.append(
                    f"| {ticker} | {s.get('company', ticker)} | {s.get('rating', '—')} | "
                    f"{s.get('entry', '—')} | {s.get('stop', '—')} | {s.get('size', '—')} | ✅ | "
                    f"[Report](./{dir_name}/complete_report.md) |"
                )
                json_rows.append({
                    "ticker": ticker,
                    "company": s.get("company", ticker),
                    "rating": s.get("rating"),
                    "entry": s.get("entry"),
                    "stop": s.get("stop"),
                    "size": s.get("size"),
                    "status": "success",
                    "error": None,
                })

        # Append failure details so errors are still readable without widening the table
        if self.failures:
            lines.append("\n## Failures\n")
            for ticker, error in sorted(self.failures.items()):
                lines.append(f"- **{ticker}**: {error}")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        md_path = self.output_dir / "batch_summary.md"
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Write JSON summary for downstream processing
        json_path = self.output_dir / "batch_summary.json"
        json_path.write_text(json.dumps(json_rows, ensure_ascii=False, indent=2), encoding="utf-8")

        return md_path

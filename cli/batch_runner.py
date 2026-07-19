import os

from tradingagents.agents.utils.structured import FALLBACK_MARKER, parse_confidence

# Batch mode is unattended — tqdm progress bars from akshare/yfinance/third-party
# libraries spam the terminal and break the Rich TUI layout. Disable globally.
os.environ["TQDM_DISABLE"] = "1"

import json
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.live import Live

from cli.batch_dashboard import BatchDashboard
from cli.dashboard import (
    ANALYST_ORDER,
    create_dashboard_layout,
    process_stream_chunk,
    update_dashboard_display,
)
from cli.stats_handler import StatsCallbackHandler
from tradingagents.default_config import default_config
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients.pricing import get_usd_to_cny_rate

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
        force: bool = False,
    ):
        self.tickers = tickers
        self.profile_config = profile_config
        self.output_dir = Path(output_dir)
        self.checkpoint = checkpoint
        self.force = force
        self.holdings = self._resolve_holdings(holdings)
        self.workers = workers
        self._headless = True  # default: unattended; interactive callers set False
        self._batch_mode = workers > 1  # batch summary mode for concurrent runs
        self.completed_tickers: set[str] = set()
        self.failures: dict[str, str] = {}
        self.summaries: dict[str, dict] = {}
        # Batch-wide token / cost rollup, populated by _accumulate_stats().
        # Per-ticker snapshot lives under ["per_ticker"][ticker] so the
        # summary table can show a per-row Tokens / Cost breakdown.
        self.batch_stats: dict = {
            "tokens_in": 0,
            "tokens_out": 0,
            "llm_calls": 0,
            "cost_by_model": {},
            "calls_by_model": {},
            "tokens_by_model": {},
            "per_ticker": {},
        }
        self.dashboard = BatchDashboard(total=len(tickers), profile_name=profile_config.get("name", "default"))
        # Set by run() once the Live context owns these — _refresh_display() reads
        # them. When unset (e.g. tests calling _run_single directly), refresh is a no-op.
        self._layout = None
        self._start_time: float | None = None
        # Protect shared mutable state across worker threads
        self._lock = threading.RLock()

    def _refresh_display(self, stats_handler=None) -> None:
        """Push current dashboard state into the Live-managed layout.

        Safe no-op when no Live context is active.
        """
        if self._layout is None or self._start_time is None:
            return
        elapsed = time.time() - self._start_time  # noqa: F841

        if self._batch_mode:
            ticker = self.dashboard.current_ticker
            if ticker:
                with self._lock:
                    self.dashboard.update_ticker_meta(
                        ticker,
                        stage=self.dashboard.current_stage,
                        progress=self.dashboard.overall_progress,
                        agent=self.dashboard.current_agent or "",
                    )

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
            batch_mode=self._batch_mode,
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
    def _resolve_company_name(ticker: str, fallback_dir_name: str = "") -> str:
        """Resolve a human-readable company name for *ticker*.

        Tries ``resolve_ticker`` first, then falls back to extracting a name
        from a directory name like ``比亚迪_002594.SZ``.  Returns an empty
        string when nothing can be determined.
        """
        try:
            from tradingagents.ticker_resolver import resolve_ticker

            resolved = resolve_ticker(ticker)
            name = resolved.get("company_name", "")
            if name:
                return name
        except Exception:
            pass
        if "_" in fallback_dir_name:
            candidate = fallback_dir_name.split("_", 1)[0]
            if candidate and candidate != fallback_dir_name:
                return candidate
        return ""

    @staticmethod
    def _build_ticker_dir_name(ticker: str, company_name: str = "") -> str:
        """Build directory name: 中文名称_代码 or 代码."""
        name = (company_name or "").strip()
        if name:
            return f"{name}_{ticker}"
        return ticker

    @staticmethod
    def _is_outside_trading_hours() -> bool:
        """Check if A-share market is currently closed.

        Returns True during weekends, before open (00:00-09:29 CST),
        and after close (15:00-24:00 CST) on weekdays.
        """
        now = datetime.now(UTC) + timedelta(hours=8)
        if now.weekday() >= 5:
            return True
        t = now.hour * 100 + now.minute
        return t < 930 or t >= 1500

    @staticmethod
    def _get_last_trading_day() -> datetime:
        """Return the CST datetime of the most recent trading day's 15:00 close.

        Returns the previous trading day's 15:00 CST when called before open
        or during weekends; returns today's 15:00 CST when called after close
        on a trading day.
        """
        now = datetime.now(UTC) + timedelta(hours=8)
        wd = now.weekday()
        hm = now.hour * 100 + now.minute

        if wd == 5:
            last = now - timedelta(days=1)
        elif wd == 6:
            last = now - timedelta(days=2)
        elif wd == 0 and hm < 930:
            last = now - timedelta(days=3)
        elif 1 <= wd <= 4 and hm < 930:
            last = now - timedelta(days=1)
        else:
            last = now

        return last.replace(hour=15, minute=0, second=0, microsecond=0)

    def _is_already_completed(self, ticker: str) -> bool:
        """Return True if a matching report already exists for the target date.

        Thin boolean wrapper around :meth:`_find_existing_report` so the skip
        path can reuse the located report to backfill the batch summary.
        """
        return self._find_existing_report(ticker) is not None

    def _find_existing_report(self, ticker: str) -> Path | None:
        """Locate an existing ``complete_report.md`` for the target date.

        Scans the current output directory *and* all historical batch_* folders
        under the same ``reports/`` root.  A report is considered a match only
        when its embedded ``Analysis Date`` equals the configured
        ``analysis_date`` (falls back to today for legacy reports).

        Returns the matching report path, or ``None`` when no report matches.
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
            path = self.output_dir / cand / "complete_report.md"
            if _has_matching_report(path):
                return path

        # 2. Check historical batch_* directories
        reports_dir = self.output_dir.parent
        if reports_dir.exists():
            for batch_dir in reports_dir.glob("batch_*"):
                for cand in candidates:
                    path = batch_dir / cand / "complete_report.md"
                    if _has_matching_report(path):
                        return path

        # ── Phase 2: Pre-market / after-hours fallback ────────────────────
        # If the market is currently closed (weekend, before-open, after-close),
        # try locating a report from the LAST trading day.  Only accept it when
        # the file was modified at or after 15:00 CST (market close) — reports
        # generated during trading hours may use incomplete data.
        if self._is_outside_trading_hours():
            last_close = self._get_last_trading_day()
            last_date = last_close.strftime("%Y-%m-%d")
            if last_date != target_date:
                last_close_ts = last_close.timestamp()

                def _has_post_close_report(path: Path) -> bool:
                    if not path.exists() or path.stat().st_size == 0:
                        return False
                    report_date = self._parse_report_analysis_date(path)
                    if report_date != last_date:
                        return False
                    return path.stat().st_mtime >= last_close_ts

                for cand in candidates:
                    path = self.output_dir / cand / "complete_report.md"
                    if _has_post_close_report(path):
                        return path

                if reports_dir.exists():
                    for batch_dir in reports_dir.glob("batch_*"):
                        for cand in candidates:
                            path = batch_dir / cand / "complete_report.md"
                            if _has_post_close_report(path):
                                return path

        return None

    def _build_config(self) -> dict:
        config = default_config()
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
        """Run analysis for a single ticker. Returns final state dict.

        Shares the core pipeline with single-ticker runs via
        ``TradingAgentsGraph.propagate()`` so batch mode gets the verified
        market snapshot, memory-log write-back, decision storage, state
        logging, and checkpoint resume for free. The dashboard keeps its
        per-chunk live updates through the ``on_chunk`` stream callback.
        """
        from cli.main import save_report_to_disk
        from tradingagents.ticker_resolver import resolve_ticker

        # Resolve ticker (A-share numeric codes get .SS/.SZ/.BJ suffix)
        resolved = resolve_ticker(ticker)
        resolved_ticker = resolved["ticker"]
        company_name = resolved.get("company_name", "")

        config = self._build_config()
        selected_analyst_keys = [a for a in ANALYST_ORDER if a in self.profile_config.get("analysts", [])]
        if not selected_analyst_keys:
            selected_analyst_keys = ["market"]
        self.dashboard.init_for_analysis(selected_analyst_keys, clear_messages=False)

        stats_handler = StatsCallbackHandler()
        # debug=False: _run_graph() pretty-prints trailing messages to stdout
        # when debug is on, which would break the Rich Live layout now that
        # batch mode shares that execution path.
        graph = TradingAgentsGraph(
            selected_analyst_keys,
            config=config,
            debug=False,
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

        max_debate = config.get("max_debate_rounds", 1)
        max_risk = config.get("max_risk_discuss_rounds", 1)
        processed_ids: set = set()

        def on_chunk(snapshot: dict) -> None:
            """Feed each merged-state snapshot to the dashboard (per stream chunk)."""
            nonlocal processed_ids
            processed_ids = process_stream_chunk(
                self.dashboard,
                snapshot,
                max_debate_rounds=max_debate,
                max_risk_rounds=max_risk,
                processed_ids=processed_ids,
            )
            self._refresh_display(stats_handler=stats_handler)

        # ── Checkpoint / resume support ────────────────────────────
        # propagate() owns the checkpoint lifecycle (compile with saver,
        # resume via stream(None), clear on success). Here we only surface
        # the resume intent on the dashboard, as before.
        if config.get("checkpoint_enabled"):
            from tradingagents.graph.checkpointer import checkpoint_step

            step = checkpoint_step(config["data_cache_dir"], resolved_ticker, trade_date)
            if step is not None:
                self.dashboard.add_message(
                    "Resume",
                    f"▶ Resuming {ticker} from checkpoint (step {step})",
                )
            else:
                self.dashboard.add_message("Info", f"Starting fresh analysis for {ticker}")

        final_state, _signal = graph.propagate(
            resolved_ticker,
            trade_date,
            confirmed_name=company_name or None,
            on_chunk=on_chunk,
            holdings_context=self.holdings,
            transactions_context=transactions_context,
            company_display_name=company_name or None,
        )

        # Save report
        ticker_dir_name = self._build_ticker_dir_name(ticker, company_name)
        ticker_dir = self.output_dir / ticker_dir_name
        ticker_dir.mkdir(parents=True, exist_ok=True)
        save_report_to_disk(final_state, ticker, ticker_dir)

        # Extract summary (lock-protected for concurrent batch mode)
        with self._lock:
            self._extract_summary(ticker, final_state)
            self._accumulate_stats(ticker, stats_handler)

        return final_state

    def _extract_summary(self, ticker: str, final_state: dict) -> None:
        """Extract the portfolio decision for the batch summary.

        Reads rating / entry / stop / size out of the Portfolio Manager's
        decision, falling back to the Trader's proposal for the numeric levels
        (the PM is told to leave those blank when the Trader omits them, but the
        Trader is required to always provide them). Supports both English
        (structured-output) and Chinese (free-text fallback) decision formats.
        """
        import re

        decision = final_state.get("final_trade_decision", "")
        trader = final_state.get("trader_investment_plan", "")
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

        fields = self._parse_summary_fields(decision, trader)

        # Detect structured-output fallback so the batch summary can flag a
        # degraded (free-text) decision. The fallback flag is kept independent
        # of confidence: a degraded format does not by itself mean the analysis
        # is low-confidence, so we no longer force "low" here — the free-text
        # path re-surfaces a canonical **Confidence** line when the model stated
        # one (see structured.invoke_structured_or_freetext), and we parse it
        # below just like a structured decision.
        is_fallback = bool(
            final_state.get("structured_fallback_agents")
            or final_state.get("_structured_fallback")
            or FALLBACK_MARKER in decision
        )
        fields["fallback"] = is_fallback
        fields["confidence"] = parse_confidence(decision) or "—"

        self.summaries[ticker] = {"company": company or ticker, **fields}

    @staticmethod
    def _parse_summary_fields(decision: str, trader: str) -> dict:
        """Parse rating / entry / stop / size from decision text, with Trader fallback.

        ``decision`` is the Portfolio Manager's final decision (authoritative
        for the rating); ``trader`` is the Trader's proposal, used as a fallback
        source for the numeric entry / stop / size levels the PM often omits.
        """
        import re

        from tradingagents.agents.utils.rating import parse_rating

        def _find_strict_numeric(text: str, names: str) -> str:
            """Extract numeric/percentage values only, tolerating markdown bold."""
            patterns = [
                rf"(?:^|\n|\|)\s*\*?\*?(?:{names})\*?\*?\s*[:：]\s*\*?\*?([0-9]+%?(?:\.[0-9]+)?)\s*(?:USD|CNY|元|%)?\*?\*?(?:\s|$|\|)",
                rf"(?:^|\n|\|)\s*\*?\*?(?:{names})\*?\*?\s*[:：]\s*\*?\*?([0-9]+%?(?:\.[0-9]+)?(?:\s*-\s*[0-9]+%?(?:\.[0-9]+)?)?)\s*(?:USD|CNY|元|%)?\*?\*?(?:\s|$|\|)",
            ]
            for pattern in patterns:
                m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
                if m:
                    return m.group(1).strip()
            return ""

        def _find_flexible(text: str, names: str) -> str:
            """Fallback: more lenient matching for non-standard formats."""
            m = re.search(
                rf"(?:^|\n|\|)\s*\*?\*?(?:{names})\*?\*?\s*[:：]\s*\*?\*?([^\n|]+?)(?:\*\*|\n|\||$)",
                text,
                re.IGNORECASE | re.MULTILINE,
            )
            return m.group(1).strip() if m else ""

        def _find(names: str, prefer_flexible: bool = False) -> str:
            """Prefer the PM decision, fall back to the Trader's proposal.

            When ``prefer_flexible`` is True (used for the ``Size`` column),
            the free-text finder is tried first so trailing natural language
            such as ``5% of portfolio`` is preserved alongside the percentage.
            Strict numeric is still a fallback so ``5%``-only recommendations
            resolve the same way as before.
            """
            if prefer_flexible:
                for text in (decision, trader):
                    val = _find_flexible(text, names)
                    if val:
                        return val
                for text in (decision, trader):
                    val = _find_strict_numeric(text, names)
                    if val:
                        return val
                return ""
            for text in (decision, trader):
                val = _find_strict_numeric(text, names)
                if val:
                    return val
            for text in (decision, trader):
                val = _find_flexible(text, names)
                if val:
                    return val
            return ""

        def _find_rating_label(text: str) -> str:
            m = re.search(
                r"(?:\*\*)?(?:Rating|Decision|评级|建议|决策|结论)(?:\*\*)?\s*[:：]\s*(?:\*\*)?([\w一-鿿]+)(?:\*\*)?",
                text,
                re.IGNORECASE,
            )
            if m:
                return m.group(1)
            m = re.search(
                r"[\"「【]([\w一-鿿]+)[\"」】]\s*(?:评级|建议|决策|结论)",
                text,
                re.IGNORECASE,
            )
            return m.group(1) if m else ""

        # Rating: PM decision is authoritative; fall back to the Trader's label,
        # then the Trader's "FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**" line.
        rating_raw = _find_rating_label(decision) or _find_rating_label(trader)
        if not rating_raw:
            m = re.search(r"FINAL TRANSACTION PROPOSAL:\s*\*?\*?([A-Za-z]+)", trader, re.IGNORECASE)
            if m:
                rating_raw = m.group(1)
        rating = parse_rating(rating_raw) if rating_raw else "—"

        entry = _find(r"Entry Price|Entry|entry_price|入场价|买入价|目标价")
        stop = _find(r"Stop Loss|Stop|stop_loss|止损价|止损线|止损")
        size = _find(
            r"Position Sizing|Size|position_size|position_sizing|仓位上限|仓位|持仓比例|仓位占比",
            prefer_flexible=True,
        )

        def _clean(val: str) -> str:
            """Normalize an extracted summary field for display in the batch table.

            The portfolio manager often packs multiple recommendations into a
            single field, e.g. ``加仓1000股（约22%现有仓位），总仓位控制在10%以内``;
            the trader pairs a number-of-shares with a target-weight range. The
            previous implementation stripped every CJK character and kept only
            digits, dots, dashes and percent signs, which collapsed the latter
            case into ``100022%10%`` — a meaningless digit soup. This version
            instead preserves the primary recommendation in readable form:

            1. Drop parenthetical annotations (e.g. ``（约22%现有仓位）``).
            2. Truncate at the first parallel-delimiter so secondary advice
               like ``保留2,700股`` or ``单只个股不超过25%`` does not bleed
               into the table cell.
            3. Collapse whitespace.

            Returns the trimmed prefix, which the caller renders directly.
            """
            if not val:
                return val
            val = val.strip()
            # Remove parenthetical annotations, Chinese 「（...）」 and ASCII 「(...)」
            val = re.sub(r"（[^）]*）", "", val)
            val = re.sub(r"\([^)]*\)", "", val)
            # Truncate at the first parallel separator or conjunction.
            # The lookarounds ensure the comma sits between non-digit
            # characters, so numeric thousands-separators like
            # ``1,800`` are preserved.
            val = re.split(
                r"(?<=\D)[，,。；;|](?=\D)|(?<=\D)保留|目标仓位|目标|此外|同时|但需|需控制|分批",
                val,
                maxsplit=1,
            )[0]
            # Collapse runs of whitespace (incl. newlines / non-breaking spaces).
            val = re.sub(r"\s+", " ", val)
            return val.strip()

        def _ok(val: str) -> bool:
            return bool(val) and bool(re.search(r"[0-9]", val))

        ce, cs, csz = _clean(entry), _clean(stop), _clean(size)
        return {
            "rating": rating,
            "entry": ce if _ok(ce) else "—",
            "stop": cs if _ok(cs) else "—",
            "size": csz if _ok(csz) else "—",
        }

    def _accumulate_stats(self, ticker: str, stats_handler) -> None:
        """Roll the per-ticker StatsCallbackHandler snapshot into the batch totals.

        Skips silently when the handler is missing or returns no token info
        (e.g. a dry-run or a test that didn't wire callbacks). Per-model cost
        is summed across the batch; per-ticker snapshot is preserved so
        ``generate_summary`` can render a per-row Tokens / Cost column.

        Defensive against non-dict stats payloads (e.g. test mocks that
        return a bare ``MagicMock`` from ``get_stats()``): if any value is
        not a number we treat the whole payload as empty and move on,
        rather than crashing the batch.
        """
        if stats_handler is None or not hasattr(stats_handler, "get_stats"):
            return
        try:
            stats = stats_handler.get_stats()
            if not isinstance(stats, dict):
                return
        except Exception:
            return
        in_tokens = stats.get("tokens_in", 0)
        out_tokens = stats.get("tokens_out", 0)
        cost_by_model = stats.get("cost_by_model")
        llm_calls = stats.get("llm_calls", 0)
        calls_by_model = stats.get("llm_calls_by_model")
        tokens_by_model = stats.get("tokens_by_model")
        if not isinstance(in_tokens, (int, float)) or isinstance(in_tokens, bool):
            in_tokens = 0
        if not isinstance(out_tokens, (int, float)) or isinstance(out_tokens, bool):
            out_tokens = 0
        if not isinstance(cost_by_model, dict):
            cost_by_model = {}
        if not isinstance(llm_calls, (int, float)) or isinstance(llm_calls, bool):
            llm_calls = 0
        if not isinstance(calls_by_model, dict):
            calls_by_model = {}
        if not isinstance(tokens_by_model, dict):
            tokens_by_model = {}
        with self._lock:
            self.batch_stats["tokens_in"] += in_tokens
            self.batch_stats["tokens_out"] += out_tokens
            self.batch_stats["llm_calls"] += llm_calls
            for model, count in calls_by_model.items():
                try:
                    self.batch_stats["calls_by_model"][model] = self.batch_stats["calls_by_model"].get(
                        model, 0
                    ) + int(count)
                except (TypeError, ValueError):
                    continue
            for model, bucket in tokens_by_model.items():
                if not isinstance(bucket, dict):
                    continue
                try:
                    prev = self.batch_stats["tokens_by_model"].setdefault(model, {"in": 0, "out": 0})
                    prev["in"] += int(bucket.get("in", 0))
                    prev["out"] += int(bucket.get("out", 0))
                except (TypeError, ValueError):
                    continue
            for model, cost in cost_by_model.items():
                try:
                    self.batch_stats["cost_by_model"][model] = self.batch_stats["cost_by_model"].get(
                        model, 0.0
                    ) + float(cost)
                except (TypeError, ValueError):
                    continue
            self.batch_stats["per_ticker"][ticker] = {
                "tokens_in": in_tokens,
                "tokens_out": out_tokens,
                "llm_calls": llm_calls,
                "cost": stats.get("cost") if isinstance(stats.get("cost"), (int, float)) else None,
                "cost_by_model": dict(cost_by_model),
                "calls_by_model": dict(calls_by_model),
                "tokens_by_model": {k: {"in": int(v.get("in", 0)), "out": int(v.get("out", 0))}
                                    for k, v in tokens_by_model.items() if isinstance(v, dict)},
            }

    def _extract_summary_from_report(self, ticker: str, report_path: Path) -> None:
        """Parse an on-disk ``complete_report.md`` into a batch-summary entry.

        Used when a ticker is skipped because its report already exists.  We
        extract the company name, the Portfolio Manager's decision, and the
        Trader's proposal from the saved markdown, then route them through
        :meth:`_parse_summary_fields` so the final table shows the same data
        as if the analysis had just run.
        """
        import re

        # Company name — try resolve_ticker first, then fall back to directory name
        ticker_dir = report_path.parent
        company = self._resolve_company_name(ticker, fallback_dir_name=ticker_dir.name)

        # Read the report; skip if it is empty or unreadable.
        try:
            text = report_path.read_text(encoding="utf-8")
        except Exception:
            self.summaries[ticker] = {
                "company": company or ticker,
                "rating": "—",
                "entry": "—",
                "stop": "—",
                "size": "—",
            }
            return

        if not text:
            self.summaries[ticker] = {
                "company": company or ticker,
                "rating": "—",
                "entry": "—",
                "stop": "—",
                "size": "—",
            }
            return

        # Extract the Portfolio Manager section (the authoritative rating source).
        decision = ""
        pm_match = re.search(
            r"(?:^|\n)(?:#{1,2}\s*V\.\s*Portfolio Manager Decision|###\s*Decision)[\s\S]*?"
            r"(?=(?:\n(?:#{2}(?!#)|#{1}(?!\#))\s*|$))",
            text,
        )
        if pm_match:
            decision = pm_match.group(0).strip()

        # Extract the Trader section (fallback for entry/stop/size).
        trader = ""
        trader_match = re.search(
            r"(?:^|\n)(?:#{1,2}\s*III\.\s*Trading Team Plan|###\s*Trader)[\s\S]*?"
            r"(?=(?:\n(?:#{2}(?!#)|#{1}(?!\#))\s*|$))",
            text,
        )
        if trader_match:
            trader = trader_match.group(0).strip()

        fields = self._parse_summary_fields(decision, trader)
        self.summaries[ticker] = {"company": company or ticker, **fields}

    def _copy_existing_report(self, ticker: str, report_path: Path) -> None:
        """Copy an existing report into the current batch output directory.

        When a ticker has already been analyzed today (from a previous batch),
        this copies ``complete_report.md`` and all sub-folders
        (``1_analysts/``, ``2_research/``, ``3_trading/``, ``4_risk/``,
        ``5_portfolio/``) into the current batch's ticker directory so the
        report is physically present even though no new analysis ran.
        """
        src_dir = report_path.parent
        if not src_dir.is_dir():
            return

        company = self._resolve_company_name(ticker, fallback_dir_name=src_dir.name)

        dst_dir = self.output_dir / self._build_ticker_dir_name(ticker, company)
        if dst_dir.resolve() == src_dir.resolve():
            return

        src_report = src_dir / "complete_report.md"
        if src_report.exists():
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_report, dst_dir / "complete_report.md")

        for subdir in sorted(src_dir.iterdir()):
            if subdir.is_dir() and subdir.name[0].isdigit():
                dst_subdir = dst_dir / subdir.name
                dst_subdir.mkdir(parents=True, exist_ok=True)
                for f in subdir.iterdir():
                    if f.is_file():
                        shutil.copy2(f, dst_subdir / f.name)

    def run(self) -> None:
        """Run the full batch."""
        layout = create_dashboard_layout()
        start_time = time.time()
        self.dashboard.update_progress(self.tickers[0] if self.tickers else None, 0, 0)

        self._layout = layout
        self._start_time = start_time

        # ── Pre-check: prompt user about existing reports BEFORE Live context ──
        skip_tickers: set[str] = set()
        for ticker in self.tickers:
            existing_report = self._find_existing_report(ticker)
            if existing_report is not None and not self.force:
                if not self._headless:
                    import questionary

                    choice = questionary.select(
                        f"检测到 {ticker} 已有完整分析报告，请选择：",
                        choices=[
                            questionary.Choice("跳过，查看已有报告", value="skip"),
                            questionary.Choice("强制重新生成", value="regenerate"),
                        ],
                        default="skip",
                    ).ask()
                    if choice != "regenerate":
                        skip_tickers.add(ticker)
                        # Copy report now (outside Live context)
                        self._copy_existing_report(ticker, existing_report)
                        with self._lock:
                            self._extract_summary_from_report(ticker, existing_report)
                        self.completed_tickers.add(ticker)
                        self.dashboard.mark_skipped(ticker)
                        self.dashboard.add_message("Copy", f"📋 {ticker} — report exists, copying from previous batch")
                else:
                    # Headless mode: skip silently
                    skip_tickers.add(ticker)
                    self._copy_existing_report(ticker, existing_report)
                    with self._lock:
                        self._extract_summary_from_report(ticker, existing_report)
                    self.completed_tickers.add(ticker)
                    self.dashboard.mark_skipped(ticker)
                    self.dashboard.add_message("Copy", f"📋 {ticker} — report exists, copying from previous batch")

        # ── Data readiness pre-check: pre-load cacheable data for all tickers ──
        from tradingagents.agents.utils.data_readiness import check_batch_readiness

        self.dashboard.add_message(
            "System",
            f"预处理 {len(self.tickers)} 个标的行情缓存…",
        )
        ready, total = check_batch_readiness(
            self.tickers,
            self.profile_config.get("analysis_date") or datetime.now().strftime("%Y-%m-%d"),
            self.profile_config.get("analysts", []),
        )
        self.dashboard.readiness_ready = ready
        self.dashboard.readiness_total = total
        self.dashboard.add_message(
            "System",
            f"数据预检: {ready}/{total} 就绪",
        )

        with Live(layout, refresh_per_second=4):
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

            if self.workers <= 1:
                # ── Sequential path: detailed per-ticker Live dashboard ──
                for _idx, ticker in enumerate(self.tickers):
                    # Already decided (skip or regenerate) above the Live context
                    if ticker in skip_tickers:
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
                            stats_handler=self._batch_stats_adapter(),
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
                        stats_handler=self._batch_stats_adapter(),
                    )

                    try:
                        self._run_single(ticker)
                        with self._lock:
                            self.completed_tickers.add(ticker)
                            self.dashboard.update_ticker_meta(ticker, "Completed", 1.0, "Done")
                    except Exception as e:
                        with self._lock:
                            self.failures[ticker] = str(e)
                            self.completed_tickers.add(ticker)
                            self.dashboard.update_ticker_meta(ticker, "Failed", 0.0, str(e)[:20])
                        # Write failure log
                        self.output_dir.mkdir(parents=True, exist_ok=True)
                        failures_path = self.output_dir / "failures.log"
                        with open(failures_path, "a", encoding="utf-8") as f:
                            f.write(f"{ticker}: {e}\n")

                    self.dashboard.update_progress(
                        ticker, len(self.completed_tickers) - len(self.failures), len(self.failures)
                    )
                    update_dashboard_display(
                        layout,
                        self.dashboard,
                        ticker=ticker,
                        start_time=start_time,
                        batch_completed=self.dashboard.completed,
                        batch_total=self.dashboard.total,
                        batch_failed=self.dashboard.failed,
                        stats_handler=self._batch_stats_adapter(),
                        profile_name=self.dashboard.profile_name,
                    )
            else:
                # ── Concurrent path: process tickers via ThreadPoolExecutor ──
                active_tickers = [t for t in self.tickers if t not in skip_tickers]
                self.dashboard.add_message(
                    "System",
                    f"并发处理 {len(active_tickers)} 个标的 (workers={self.workers})",
                )

                with ThreadPoolExecutor(max_workers=self.workers) as executor:
                    futures = []
                    for ticker in active_tickers:
                        future = executor.submit(self._run_single, ticker)
                        futures.append((future, ticker))

                    running_tickers: set[str] = set(active_tickers)
                    for future, ticker in futures:
                        try:
                            future.result()
                            with self._lock:
                                self.completed_tickers.add(ticker)
                                self.dashboard.update_ticker_meta(ticker, "Completed", 1.0, "Done")
                        except Exception as e:
                            with self._lock:
                                self.failures[ticker] = str(e)
                                self.completed_tickers.add(ticker)
                                self.dashboard.update_ticker_meta(ticker, "Failed", 0.0, str(e)[:20])
                            # Write failure log
                            self.output_dir.mkdir(parents=True, exist_ok=True)
                            failures_path = self.output_dir / "failures.log"
                            with open(failures_path, "a", encoding="utf-8") as f:
                                f.write(f"{ticker}: {e}\n")

                        with self._lock:
                            running_tickers.discard(ticker)

                        self.dashboard.update_progress(
                            ticker,
                            len(self.completed_tickers) - len(self.failures),
                            len(self.failures),
                        )
                        with self._lock:
                            running_tickers_snapshot = running_tickers.copy()
                        update_dashboard_display(
                            layout,
                            self.dashboard,
                            ticker=ticker,
                            start_time=start_time,
                            batch_completed=self.dashboard.completed,
                            batch_total=self.dashboard.total,
                            batch_failed=self.dashboard.failed,
                            stats_handler=self._batch_stats_adapter(),
                            profile_name=self.dashboard.profile_name,
                            batch_mode=True,
                            running_tickers=running_tickers_snapshot,
                        )

        # Live closed — release the layout reference so any stray _refresh_display
        # call from finalization code becomes a no-op instead of writing to a dead layout.
        self._layout = None
        self._start_time = None

    def generate_summary(self) -> Path:
        """Generate batch_summary.md and batch_summary.json. Returns path to markdown."""
        has_stats = bool(self.batch_stats.get("per_ticker"))
        lines = ["# Batch Analysis Report\n"]
        if has_stats:
            lines.append("| Ticker | Company | Rating | Entry | Stop | Size | Confidence | Tokens | Cost | Status | Details |")
            lines.append("|--------|---------|--------|-------|------|------|------------|--------|------|--------|---------|")
        else:
            lines.append("| Ticker | Company | Rating | Entry | Stop | Size | Confidence | Status | Details |")
            lines.append("|--------|---------|--------|-------|------|------|------------|--------|---------|")

        all_tickers = sorted(set(self.tickers) | set(self.summaries.keys()) | set(self.failures.keys()))
        json_rows = []
        for ticker in all_tickers:
            per_ticker_stats = self.batch_stats["per_ticker"].get(ticker, {})
            tokens_cell, cost_cell = self._format_token_cost_cells(per_ticker_stats)
            if ticker in self.failures:
                if has_stats:
                    lines.append(f"| {ticker} | — | — | — | — | — | — | — | — | ❌ | — |")
                else:
                    lines.append(f"| {ticker} | — | — | — | — | — | — | ❌ | — |")
                json_rows.append(
                    {
                        "ticker": ticker,
                        "company": None,
                        "rating": None,
                        "entry": None,
                        "stop": None,
                        "size": None,
                        "llm_calls": None,
                        "tokens_in": None,
                        "tokens_out": None,
                        "cost": None,
                        "calls_by_model": {},
                        "tokens_by_model": {},
                        "cost_by_model": {},
                        "status": "failed",
                        "error": self.failures[ticker],
                    }
                )
            else:
                s = self.summaries.get(ticker, {})
                dir_name = self._build_ticker_dir_name(ticker, s.get("company", ""))
                confidence = s.get("confidence", "—")
                details = f"[Report](./{dir_name}/complete_report.md)"
                if s.get("fallback"):
                    details += " [fallback]"
                if has_stats:
                    lines.append(
                        f"| {ticker} | {s.get('company', ticker)} | {s.get('rating', '—')} | "
                        f"{s.get('entry', '—')} | {s.get('stop', '—')} | {s.get('size', '—')} | "
                        f"{confidence} | {tokens_cell} | {cost_cell} | ✅ | {details} |"
                    )
                else:
                    lines.append(
                        f"| {ticker} | {s.get('company', ticker)} | {s.get('rating', '—')} | "
                        f"{s.get('entry', '—')} | {s.get('stop', '—')} | {s.get('size', '—')} | "
                        f"{confidence} | ✅ | {details} |"
                    )
                json_rows.append(
                    {
                        "ticker": ticker,
                        "company": s.get("company", ticker),
                        "rating": s.get("rating"),
                        "entry": s.get("entry"),
                        "stop": s.get("stop"),
                        "size": s.get("size"),
                        "confidence": s.get("confidence"),
                        "fallback": s.get("fallback", False),
                        "llm_calls": per_ticker_stats.get("llm_calls"),
                        "tokens_in": per_ticker_stats.get("tokens_in"),
                        "tokens_out": per_ticker_stats.get("tokens_out"),
                        "cost": per_ticker_stats.get("cost"),
                        "calls_by_model": dict(per_ticker_stats.get("calls_by_model", {})),
                        "tokens_by_model": {
                            k: {"in": int(v.get("in", 0)), "out": int(v.get("out", 0))}
                            for k, v in (per_ticker_stats.get("tokens_by_model") or {}).items()
                        },
                        "cost_by_model": dict(per_ticker_stats.get("cost_by_model", {})),
                        "status": "success",
                        "error": None,
                    }
                )

        # Append failure details so errors are still readable without widening the table
        if self.failures:
            lines.append("\n## Failures\n")
            for ticker, error in sorted(self.failures.items()):
                lines.append(f"- **{ticker}**: {error}")

        # Footer with batch-wide token / cost rollup
        if has_stats:
            lines.append("\n## Batch Cost\n")
            tin = self.batch_stats["tokens_in"]
            tout = self.batch_stats["tokens_out"]
            tin_str = self._format_number(tin)
            tout_str = self._format_number(tout)
            lines.append(f"- **Total tokens**: {tin_str}↑ {tout_str}↓")
            lines.append(f"- **Total LLM calls**: {self.batch_stats['llm_calls']}")
            cost_by_model = self.batch_stats["cost_by_model"]
            if cost_by_model:
                total_cost = sum(cost_by_model.values())
                cny_rate = get_usd_to_cny_rate()
                lines.append(f"- **Total cost**: ${total_cost:.4f}（¥{total_cost * cny_rate:.2f}）")
                lines.append("- **By model**:")
                for model, cost in sorted(cost_by_model.items(), key=lambda kv: -kv[1]):
                    lines.append(f"  - {model}: ${cost:.4f}（¥{cost * cny_rate:.2f}）")
            else:
                lines.append("- **Total cost**: — (no priced models in this batch)")

            calls_by_model = self.batch_stats.get("calls_by_model", {})
            tokens_by_model = self.batch_stats.get("tokens_by_model", {})
            if calls_by_model or tokens_by_model:
                lines.append("\n## Batch Usage\n")
                lines.append("| Model | Calls | Tokens In | Tokens Out | Cost |")
                lines.append("|-------|-------|-----------|------------|------|")
                models = sorted(set(calls_by_model) | set(tokens_by_model) | set(cost_by_model))
                total_calls = 0
                total_tin = 0
                total_tout = 0
                total_cost = 0.0
                for model in models:
                    calls = calls_by_model.get(model, 0)
                    t_in = tokens_by_model.get(model, {}).get("in", 0)
                    t_out = tokens_by_model.get(model, {}).get("out", 0)
                    cost = cost_by_model.get(model)
                    total_calls += calls
                    total_tin += t_in
                    total_tout += t_out
                    if cost is not None:
                        total_cost += cost
                        cost_cell = f"${cost:.4f}"
                    else:
                        cost_cell = "—"
                    lines.append(
                        f"| {model} | {calls} | {self._format_number(t_in)} | "
                        f"{self._format_number(t_out)} | {cost_cell} |"
                    )
                total_cost_cell = f"${total_cost:.4f}" if cost_by_model else "—"
                lines.append(
                    f"| **Total** | **{total_calls}** | **{self._format_number(total_tin)}↑** | "
                    f"**{self._format_number(total_tout)}↓** | **{total_cost_cell}** |"
                )

        self.output_dir.mkdir(parents=True, exist_ok=True)
        md_path = self.output_dir / "batch_summary.md"
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Write JSON summary for downstream processing
        json_path = self.output_dir / "batch_summary.json"
        json_output = {
            "rows": json_rows,
            "totals": {
                "tokens_in": self.batch_stats.get("tokens_in", 0),
                "tokens_out": self.batch_stats.get("tokens_out", 0),
                "llm_calls": self.batch_stats.get("llm_calls", 0),
                "cost_by_model": dict(self.batch_stats.get("cost_by_model", {})),
                "calls_by_model": dict(self.batch_stats.get("calls_by_model", {})),
                "tokens_by_model": {
                    k: {"in": int(v.get("in", 0)), "out": int(v.get("out", 0))}
                    for k, v in self.batch_stats.get("tokens_by_model", {}).items()
                },
            },
        }
        json_output["totals"]["total_cost_usd"] = sum(json_output["totals"]["cost_by_model"].values())
        json_output["totals"]["usd_to_cny_rate"] = get_usd_to_cny_rate()
        json_output["totals"]["total_cost_cny"] = (
            json_output["totals"]["total_cost_usd"] * json_output["totals"]["usd_to_cny_rate"]
        )
        json_path.write_text(json.dumps(json_output, ensure_ascii=False, indent=2), encoding="utf-8")

        return md_path

    def _batch_stats_adapter(self):
        """Adapt ``self.batch_stats`` to the StatsCallbackHandler-shaped object
        that ``update_dashboard_display`` expects. Lets the cross-ticker
        footer reflect the running batch total (tokens / cost / by-model
        breakdown) without per-ticker numbers, which the in-ticker chunk
        refresh already shows.

        The adapter reads ``self.batch_stats`` lazily on every
        ``get_stats()`` call so it always reflects the latest accumulated
        numbers — a previous version snapshotted at construction time and
        froze the totals for the adapter's lifetime, which left the
        footer permanently showing zeros.
        """
        outer = self

        class _Adapter:
            def get_stats(inner_self) -> dict:
                snapshot = outer.batch_stats
                cost_by_model = dict(snapshot.get("cost_by_model") or {})
                calls_by_model = dict(snapshot.get("calls_by_model") or {})
                tokens_by_model = {
                    k: {"in": v.get("in", 0), "out": v.get("out", 0)}
                    for k, v in (snapshot.get("tokens_by_model") or {}).items()
                }
                total_cost = sum(cost_by_model.values()) if cost_by_model else None
                return {
                    "llm_calls": snapshot.get("llm_calls", 0),
                    "tool_calls": 0,
                    "tokens_in": snapshot.get("tokens_in", 0),
                    "tokens_out": snapshot.get("tokens_out", 0),
                    "cost": total_cost,
                    "cost_by_model": cost_by_model,
                    "calls_by_model": calls_by_model,
                    "tokens_by_model": tokens_by_model,
                }

        return _Adapter()

    @staticmethod
    def _format_number(n: int | float) -> str:
        """Render a number with k/M abbreviation."""
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1000:
            return f"{n / 1000:.1f}k"
        return str(n)

    @staticmethod
    def _format_token_cost_cells(stats: dict) -> tuple[str, str]:
        """Render Tokens / Cost cells for one ticker row, or '—' when unknown."""
        in_tokens = stats.get("tokens_in") or 0
        out_tokens = stats.get("tokens_out") or 0
        if in_tokens or out_tokens:
            tokens_cell = (
                f"{BatchRunner._format_number(in_tokens)}↑ "
                f"{BatchRunner._format_number(out_tokens)}↓"
            )
        else:
            tokens_cell = "—"
        cost = stats.get("cost")
        cost_cell = f"${cost:.4f}" if cost is not None else "—"
        return tokens_cell, cost_cell

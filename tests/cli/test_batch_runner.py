import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from cli.batch_runner import BatchRunner


@pytest.fixture(autouse=True)
def _mock_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


def test_batch_runner_skips_completed(tmp_path):
    """If a ticker already has complete_report.md, skip it."""
    runner = BatchRunner(
        tickers=["AAPL"],
        profile_config={"llm_provider": "openai", "output_language": "English"},
        output_dir=tmp_path / "reports",
    )
    # Simulate pre-existing report
    ticker_dir = runner.output_dir / "AAPL"
    ticker_dir.mkdir(parents=True)
    (ticker_dir / "complete_report.md").write_text("done")

    with patch.object(runner, "_run_single") as mock_run:
        runner.run()
        mock_run.assert_not_called()


def test_batch_runner_records_failure(tmp_path):
    """If _run_single raises, record failure and continue."""
    runner = BatchRunner(
        tickers=["AAPL", "MSFT"],
        profile_config={"llm_provider": "openai", "output_language": "English"},
        output_dir=tmp_path / "reports",
    )

    def side_effect(ticker):
        if ticker == "AAPL":
            raise RuntimeError("API error")

    with patch.object(runner, "_run_single", side_effect=side_effect):
        runner.run()

    assert runner.failures == {"AAPL": "API error"}
    assert "AAPL" in runner.completed_tickers
    assert "MSFT" in runner.completed_tickers


def test_generate_summary_creates_markdown(tmp_path):
    runner = BatchRunner(
        tickers=["AAPL", "MSFT"],
        profile_config={"llm_provider": "openai", "output_language": "English"},
        output_dir=tmp_path / "reports",
    )
    runner.summaries = {
        "AAPL": {"rating": "Buy", "entry": "210", "stop": "200", "size": "5%", "company": "Apple"},
        "MSFT": {"rating": "Hold", "entry": "—", "stop": "—", "size": "—", "company": "Microsoft"},
    }
    runner.failures = {"TSLA": "Invalid ticker"}
    path = runner.generate_summary()
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "AAPL" in content
    assert "Buy" in content
    assert "TSLA" in content
    assert "Invalid ticker" in content


def test_refresh_display_noop_without_layout(tmp_path):
    """_refresh_display() is safe to call before run() initializes the layout."""
    runner = BatchRunner(
        tickers=["AAPL"],
        profile_config={"llm_provider": "openai", "output_language": "English"},
        output_dir=tmp_path / "reports",
    )
    # Should not raise even though _layout / _start_time are None.
    runner._refresh_display()
    assert runner._layout is None


def test_refresh_display_invokes_update_when_layout_set(tmp_path):
    """When run() has set _layout and _start_time, _refresh_display pushes state."""
    runner = BatchRunner(
        tickers=["AAPL"],
        profile_config={"llm_provider": "openai", "output_language": "English"},
        output_dir=tmp_path / "reports",
    )
    runner._layout = MagicMock()
    runner._start_time = 0.0

    with patch("cli.batch_runner.update_dashboard_display") as mock_update:
        runner._refresh_display()
        mock_update.assert_called_once()
        # First positional arg is layout, second is dashboard
        args, kwargs = mock_update.call_args
        assert args[0] is runner._layout
        assert args[1] is runner.dashboard
        assert "start_time" in kwargs


def test_run_single_refreshes_per_chunk(tmp_path):
    """_run_single must call _refresh_display() once per stream chunk so the
    Live-managed layout reflects in-flight progress instead of freezing on the
    initial frame for the entire 5-30 min single-stock analysis."""
    runner = BatchRunner(
        tickers=["AAPL"],
        profile_config={
            "llm_provider": "openai",
            "output_language": "English",
            "analysts": ["market"],
            "analysis_date": "2026-05-15",
        },
        output_dir=tmp_path / "reports",
    )
    # Pretend run() has already opened the Live context.
    runner._layout = MagicMock()
    runner._start_time = 0.0

    # Fake graph: 3 chunks then final state has the keys save_report_to_disk needs.
    fake_chunks = [
        {"messages": []},
        {"investment_debate_state": {"bull_history": "x"}},
        {"final_trade_decision": "Rating: Hold\nEntry: 100\nStop: 90\nSize: 1%"},
    ]
    fake_graph = MagicMock()
    fake_graph.graph.stream.return_value = iter(fake_chunks)
    fake_graph.propagator.create_initial_state.return_value = {"messages": []}
    fake_graph.propagator.get_graph_args.return_value = {}

    with patch("cli.batch_runner.TradingAgentsGraph", return_value=fake_graph), \
         patch("cli.batch_runner.StatsCallbackHandler"), \
         patch("tradingagents.ticker_resolver.resolve_ticker",
               return_value={"ticker": "AAPL", "company_name": "Apple Inc."}), \
         patch("cli.main.save_report_to_disk"), \
         patch("cli.main.run_translation_pipeline"), \
         patch.object(runner, "_refresh_display") as mock_refresh:
        runner._run_single("AAPL")

    # One refresh per chunk yielded.
    assert mock_refresh.call_count == len(fake_chunks)


def test_parallel_run_completes_all_tickers(tmp_path):
    """With workers > 1, all tickers are processed concurrently."""
    runner = BatchRunner(
        tickers=["AAPL", "MSFT", "GOOGL"],
        profile_config={"llm_provider": "openai", "output_language": "English"},
        output_dir=tmp_path / "reports",
        workers=3,
    )
    with patch.object(runner, "_run_single") as mock_run:
        runner.run()
        assert mock_run.call_count == 3
    assert len(runner.completed_tickers) == 3
    assert "AAPL" in runner.completed_tickers
    assert "MSFT" in runner.completed_tickers
    assert "GOOGL" in runner.completed_tickers


def test_parallel_run_records_failure_without_blocking(tmp_path):
    """A failing ticker must not block the successful ones in parallel mode."""
    runner = BatchRunner(
        tickers=["AAPL", "MSFT", "GOOGL"],
        profile_config={"llm_provider": "openai", "output_language": "English"},
        output_dir=tmp_path / "reports",
        workers=2,
    )

    def side_effect(ticker):
        if ticker == "MSFT":
            raise RuntimeError("API error")
        import time
        time.sleep(0.05)

    with patch.object(runner, "_run_single", side_effect=side_effect):
        runner.run()

    assert runner.failures == {"MSFT": "API error"}
    assert "MSFT" in runner.completed_tickers
    assert "AAPL" in runner.completed_tickers
    assert "GOOGL" in runner.completed_tickers


def test_parallel_run_with_workers_1_uses_sequential_path(tmp_path):
    """workers=1 must route through the sequential path (Live dashboard enabled)."""
    runner = BatchRunner(
        tickers=["AAPL"],
        profile_config={"llm_provider": "openai", "output_language": "English"},
        output_dir=tmp_path / "reports",
        workers=1,
    )
    with patch.object(runner, "_run_single") as mock_run:
        runner.run()
        mock_run.assert_called_once_with("AAPL")


# --- size field normalization: regression for batch_summary table squashing ---

@pytest.mark.parametrize("raw,expected", [
    # Single-clause recommendations (English / concise Chinese) must survive intact.
    ("5% of portfolio", "5% of portfolio"),
    ("10-12% of portfolio", "10-12% of portfolio"),
    ("5% of portfolio (initial 10-15%, target 15-20%)", "5% of portfolio"),
    ("3%-5%", "3%-5%"),
    ("减持现有仓位的20-30%", "减持现有仓位的20-30%"),
    ("不超过总资金的 5%", "不超过总资金的 5%"),
    ("总组合市值的3%-5%", "总组合市值的3%-5%"),
    # Multi-clause recommendations must drop secondary advice at the first delimiter.
    ("减持1,800股，保留2,700股（约60%仓位）", "减持1,800股"),
    ("卖出150股(当前300股的50%)，保留150股核心头寸", "卖出150股"),
    ("加仓1000股（约22%现有仓位），总仓位控制在10%以内", "加仓1000股"),
    ("加仓300股（占总计划仓位30%，与现有900股合并后总仓位1200股）", "加仓300股"),
    # Regression: previously the entries below collapsed into "100022%10%"-style digit soup.
    ("总仓位15%，现有700股基础上分批加仓至1200股（而非激进派建议的1500股），单只个股不超过组合总仓位25%", "总仓位15%"),
])
def test_parse_summary_size_preserves_readable_text(raw, expected):
    """Regression for #bug-2026-06-06-size-squash: portfolio size fields with multiple
    recommendations were squashed into a digit soup (e.g. ``100022%10%``) by the
    previous CJK-stripping cleaner. The new cleaner drops parenthetical annotations
    and truncates at the first parallel-delimiter, so the table cell stays readable.
    """
    result = BatchRunner._parse_summary_fields(
        decision=f"**Rating**: Overweight\n**Size**: {raw}\n",
        trader="",
    )
    assert result["size"] == expected, f"input: {raw!r}\n  got: {result['size']!r}"


def test_parse_summary_size_preserves_numeric_for_entry_stop():
    """Numeric entry/stop fields must not be touched by the size-preserving cleaner."""
    result = BatchRunner._parse_summary_fields(
        decision=(
            "**Rating**: Overweight\n"
            "**Entry**: 27.2\n"
            "**Stop**: 26.5\n"
            "**Size**: 5% of portfolio\n"
        ),
        trader="",
    )
    assert result["entry"] == "27.2"
    assert result["stop"] == "26.5"
    assert result["size"] == "5% of portfolio"

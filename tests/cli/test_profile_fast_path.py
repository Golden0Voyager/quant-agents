"""Unit tests for the A1-A5 interactive-flow fixes in cli.main.analyze / get_user_selections.

Covers: A2 resolve-crash protection, A3 readiness gate wiring (decline + Esc
back-chain), A5 analysis_date propagation into profile_config.
"""

from __future__ import annotations

from unittest import mock

import pytest

import cli.main as m


@pytest.fixture
def no_fs_side_effects(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "display_welcome", lambda: None)
    monkeypatch.setattr(m, "_prompt_sync_holdings_interactive", lambda: None)
    monkeypatch.setattr(m, "_resolve_holdings", lambda **kwargs: None)


def _profile() -> dict:
    return {
        "analysts": ["market"],
        "research_depth": 1,
        "llm_provider": "openai",
        "backend_url": None,
        "shallow_thinker": "gpt-4.1",
        "deep_thinker": "gpt-5",
        "output_language": "中文",
    }


def _readiness_mock(warning_count: int) -> list:
    ready = mock.patch("tradingagents.agents.utils.data_readiness.check_data_readiness")
    display = mock.patch("tradingagents.agents.utils.data_readiness.display_readiness_report")
    report = ready.start()
    display.start()
    report.return_value.warning_count = warning_count
    return [ready, display]


def _stop(patches: list) -> None:
    for p in patches:
        p.stop()


# m.analyze()'s real defaults are typer.Option objects (always truthy), which
# would flip the headless/clear-checkpoints branches when called directly.
_ANALYZE_KWARGS = {
    "checkpoint": False,
    "clear_checkpoints": False,
    "force": False,
    "profile": None,
    "watchlist": None,
    "tickers": None,
    "config": None,
    "output_dir": None,
    "holdings_sheet": None,
    "holdings_worksheet": "total",
    "sync_holdings": False,
    "workers": 1,
}


@pytest.mark.unit
class TestWizardStep0Esc:
    def test_confirm_esc_returns_back_value(self):
        with (
            mock.patch.object(m, "get_ticker", return_value="000001"),
            mock.patch.object(
                m,
                "resolve_ticker",
                return_value={"ticker": "000001.SZ", "company_name": "平安银行"},
            ),
            mock.patch("questionary.confirm") as confirm,
        ):
            confirm.return_value.ask.return_value = None
            result = m.get_user_selections(allow_back=True)
        assert result == m.BACK_VALUE

    def test_resolve_failure_does_not_crash(self):
        env = {
            "TRADINGAGENTS_LLM_PROVIDER": "openai",
            "TRADINGAGENTS_DEEP_THINK_LLM": "kimi-k2.5",
            "TRADINGAGENTS_QUICK_THINK_LLM": "deepseek-v4-pro",
            "TRADINGAGENTS_OUTPUT_LANGUAGE": "Japanese",
        }
        fake_cfg = dict(m.DEFAULT_CONFIG)
        fake_cfg.update(
            {
                "llm_provider": "openai",
                "quick_think_llm": "deepseek-v4-pro",
                "deep_think_llm": "kimi-k2.5",
                "output_language": "Japanese",
            }
        )
        ready = _readiness_mock(0)
        try:
            with (
                mock.patch.dict("os.environ", env, clear=False),
                mock.patch.object(m, "DEFAULT_CONFIG", fake_cfg),
                mock.patch.object(m, "get_ticker", return_value="000001"),
                mock.patch.object(m, "resolve_ticker", side_effect=Exception("no ticker")),
                mock.patch.object(m, "get_analysis_date", return_value="2026-05-29"),
                mock.patch.object(m, "select_analysts", return_value=[]),
                mock.patch.object(m, "select_research_depth", return_value=1),
                mock.patch("questionary.confirm") as confirm,
                mock.patch.object(m, "ensure_api_key"),
                mock.patch.object(m, "select_llm_provider"),
                mock.patch.object(m, "ask_output_language"),
                mock.patch.object(m, "select_shallow_thinking_agent"),
                mock.patch.object(m, "select_deep_thinking_agent"),
            ):
                confirm.return_value.ask.return_value = True
                sel = m.get_user_selections()
        finally:
            _stop(ready)
        assert sel is not None and sel != m.BACK_VALUE
        assert sel["ticker"] == "000001.SZ"


@pytest.mark.unit
class TestSingleProfileFastPath:
    def _run_analyze(self, resolve_ticker, analysis_date="2026-05-29"):
        with (
            mock.patch.object(m, "ask_mode", return_value="single"),
            mock.patch("questionary.select") as select,
            mock.patch.object(m, "select_profile_interactive", return_value=_profile()),
            mock.patch.object(m, "get_ticker", return_value="000001"),
            mock.patch.object(m, "resolve_ticker", side_effect=resolve_ticker),
            mock.patch("questionary.confirm") as confirm,
            mock.patch.object(m, "get_analysis_date", return_value=analysis_date),
            mock.patch.object(m, "run_batch_analysis") as run_batch,
        ):
            select.return_value.ask.return_value = "yes"
            confirm.return_value.ask.return_value = True
            ready = _readiness_mock(0)
            try:
                m.analyze(**_ANALYZE_KWARGS)
            finally:
                _stop(ready)
        return run_batch

    def test_date_prompt_wired_into_profile_config(self, no_fs_side_effects):
        resolved = {"ticker": "000001.SZ", "company_name": "平安银行"}
        run_batch = self._run_analyze(resolve_ticker=lambda t: resolved)
        run_batch.assert_called_once()
        args, kwargs = run_batch.call_args
        assert args[0] == ["000001.SZ"]
        assert args[1]["analysis_date"] == "2026-05-29"

    def test_resolve_failure_does_not_crash(self, no_fs_side_effects):
        run_batch = self._run_analyze(resolve_ticker=Exception("boom"))
        run_batch.assert_called_once()
        args, _ = run_batch.call_args
        assert args[0] == ["000001.SZ"]


@pytest.mark.unit
class TestBatchProfileGate:
    def _run_analyze(self, *, warnings, confirm_answer, date_side_effect=None):
        tickers = ["000001.SZ", "300750.SZ"]
        with (
            mock.patch.object(m, "ask_mode", side_effect=["batch", m.BACK_VALUE]),
            mock.patch.object(
                m,
                "select_watchlist_interactive",
                side_effect=[("watch", tickers), (m.BACK_VALUE, [])],
            ),
            mock.patch.object(
                m,
                "select_profile_interactive",
                side_effect=[_profile(), m.BACK_VALUE],
            ),
            mock.patch.object(
                m,
                "get_analysis_date",
                side_effect=date_side_effect or ["2026-05-29"],
            ),
            mock.patch("questionary.confirm") as confirm,
            mock.patch.object(m, "ask_workers", return_value=1),
            mock.patch.object(m, "resolve_ticker", return_value={"ticker": "000001.SZ"}),
            mock.patch.object(m, "run_batch_analysis") as run_batch,
        ):
            confirm.return_value.ask.return_value = confirm_answer
            ready = _readiness_mock(warnings)
            try:
                m.analyze(**_ANALYZE_KWARGS)
            finally:
                _stop(ready)
        return run_batch

    def test_happy_path_sets_analysis_date(self, no_fs_side_effects):
        run_batch = self._run_analyze(warnings=0, confirm_answer=True)
        run_batch.assert_called_once()
        args, kwargs = run_batch.call_args
        assert args[0] == ["000001.SZ", "300750.SZ"]
        assert args[1]["analysis_date"] == "2026-05-29"
        assert kwargs.get("watchlist_name") == "watch"

    def test_readiness_decline_cancels_run(self, no_fs_side_effects):
        run_batch = self._run_analyze(warnings=1, confirm_answer=False)
        run_batch.assert_not_called()

    def test_readiness_esc_reasks_date_then_back_chain(self, no_fs_side_effects):
        run_batch = self._run_analyze(
            warnings=1,
            confirm_answer=None,
            date_side_effect=["2026-05-29", m.BACK_VALUE],
        )
        run_batch.assert_not_called()

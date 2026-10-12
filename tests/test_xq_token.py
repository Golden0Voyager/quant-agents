"""Unit tests for tradingagents.dataflows.xq_token."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tradingagents.dataflows import xq_token


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Isolate process-wide token state and the environment between tests."""
    with xq_token._LOCK:
        xq_token._KNOWN_BAD.clear()
        xq_token._VALIDATED_AT.clear()
    monkeypatch.delenv("XUEQIU_TOKEN", raising=False)
    monkeypatch.delenv("XQ_TOKEN", raising=False)
    yield
    with xq_token._LOCK:
        xq_token._KNOWN_BAD.clear()
        xq_token._VALIDATED_AT.clear()


def _api_error() -> Exception:
    from akshare.exceptions import APIError

    return APIError("Xueqiu quote endpoint requires a valid xq_a_token")


@pytest.mark.unit
class TestNormalizeXqToken:
    def test_bare_value(self):
        assert xq_token.normalize_xq_token("0e4ab06b") == "0e4ab06b"

    def test_cookie_form(self):
        assert xq_token.normalize_xq_token("xq_a_token=0e4ab06b;") == "0e4ab06b"

    def test_cookie_form_with_space(self):
        assert xq_token.normalize_xq_token(" xq_a_token=0e4ab06b ; ") == "0e4ab06b"


@pytest.mark.unit
class TestCurrentEnvToken:
    def test_reads_xueqiu_token(self, monkeypatch):
        monkeypatch.setenv("XUEQIU_TOKEN", "xq_a_token=abc123;")
        assert xq_token.current_env_token() == "abc123"

    def test_xq_token_fallback(self, monkeypatch):
        monkeypatch.setenv("XQ_TOKEN", "def456")
        assert xq_token.current_env_token() == "def456"

    def test_none_when_unset(self):
        assert xq_token.current_env_token() is None


@pytest.mark.unit
class TestProbe:
    def test_ok_on_data(self):
        with patch("akshare.stock_individual_spot_xq") as mock_xq:
            mock_xq.return_value = pd.DataFrame({"item": ["现价"], "value": [10.0]})
            assert xq_token._probe("good") == "ok"

    def test_auth_on_api_error(self):
        with patch("akshare.stock_individual_spot_xq") as mock_xq:
            mock_xq.side_effect = _api_error()
            assert xq_token._probe("dead") == "auth"

    def test_network_on_other_errors(self):
        with patch("akshare.stock_individual_spot_xq") as mock_xq:
            mock_xq.side_effect = ConnectionError("boom")
            assert xq_token._probe("x") == "network"

    def test_network_on_empty_frame(self):
        with patch("akshare.stock_individual_spot_xq") as mock_xq:
            mock_xq.return_value = pd.DataFrame()
            assert xq_token._probe("x") == "network"


@pytest.mark.unit
class TestUpdateXqToken:
    def test_valid_token_sets_env_and_persists(self, monkeypatch):
        monkeypatch.setattr(xq_token, "_persist_to_dotenv", MagicMock(return_value=None))
        with patch("akshare.stock_individual_spot_xq") as mock_xq:
            mock_xq.return_value = pd.DataFrame({"item": ["现价"], "value": [10.0]})
            result = xq_token.update_xq_token("xq_a_token=fresh123;")
        assert result == "fresh123"
        assert xq_token.current_env_token() == "fresh123"

    def test_invalid_token_raises_and_does_not_touch_env(self, monkeypatch):
        monkeypatch.setattr(xq_token, "_persist_to_dotenv", MagicMock())
        with patch("akshare.stock_individual_spot_xq") as mock_xq:
            mock_xq.side_effect = _api_error()
            with pytest.raises(ValueError, match="未通过雪球实测探针"):
                xq_token.update_xq_token("dead")
        xq_token._persist_to_dotenv.assert_not_called()

    def test_empty_token_raises(self):
        with pytest.raises(ValueError, match="为空"):
            xq_token.update_xq_token("   ")

    def test_update_clears_known_bad(self, monkeypatch):
        xq_token.mark_known_bad("fresh123")
        monkeypatch.setattr(xq_token, "_persist_to_dotenv", lambda t: None)
        with patch("akshare.stock_individual_spot_xq") as mock_xq:
            mock_xq.return_value = pd.DataFrame({"item": ["现价"], "value": [10.0]})
            xq_token.update_xq_token("fresh123")
        assert not xq_token.is_known_bad("fresh123")


@pytest.mark.unit
class TestHandlePossibleExpiry:
    def test_marks_bad_and_warns_on_auth(self, caplog):
        with patch("akshare.stock_individual_spot_xq") as mock_xq:
            mock_xq.side_effect = _api_error()
            with caplog.at_level("WARNING", logger="tradingagents.dataflows.xq_token"):
                xq_token.handle_possible_expiry("dead")
        assert xq_token.is_known_bad("dead")
        assert any("已失效" in r.message for r in caplog.records)
        assert any("update_xq_token.py" in r.message for r in caplog.records)

    def test_ok_probe_keeps_token_usable(self, caplog):
        with patch("akshare.stock_individual_spot_xq") as mock_xq:
            mock_xq.return_value = pd.DataFrame({"item": ["现价"], "value": [10.0]})
            xq_token.handle_possible_expiry("good")
        assert not xq_token.is_known_bad("good")

    def test_network_probe_does_not_mark_bad(self):
        with patch("akshare.stock_individual_spot_xq") as mock_xq:
            mock_xq.side_effect = ConnectionError("boom")
            xq_token.handle_possible_expiry("maybe")
        assert not xq_token.is_known_bad("maybe")

    def test_idempotent_for_known_bad(self):
        xq_token.mark_known_bad("dead")
        with patch("akshare.stock_individual_spot_xq") as mock_xq:
            xq_token.handle_possible_expiry("dead")
        mock_xq.assert_not_called()


@pytest.mark.unit
class TestFetchRealtimeSnapshotIntegration:
    """fetch_realtime_snapshot wiring: known-bad skip + expiry handling."""

    def _eastmoney_info(self):
        return pd.DataFrame({"item": ["股票简称", "总市值"], "value": ["茅台", 2.2e12]})

    def test_known_bad_token_skips_xq_and_uses_fallback(self, monkeypatch, caplog):
        from tradingagents.dataflows.akshare_realtime import fetch_realtime_snapshot

        monkeypatch.setenv("XUEQIU_TOKEN", "dead")
        xq_token.mark_known_bad("dead")
        with patch("tradingagents.dataflows.akshare_realtime.ak") as mock_ak:
            mock_ak.stock_individual_info_em.return_value = self._eastmoney_info()
            snap = fetch_realtime_snapshot("600519.SS")
        mock_ak.stock_individual_spot_xq.assert_not_called()
        assert snap is not None
        assert "eastmoney_info" in snap["sources"]
        assert "xueqiu" not in snap["sources"]

    def test_xq_failure_triggers_expiry_handler(self, monkeypatch):
        from tradingagents.dataflows import akshare_realtime
        from tradingagents.dataflows.akshare_realtime import fetch_realtime_snapshot

        monkeypatch.setenv("XUEQIU_TOKEN", "dead")
        handler = MagicMock()
        monkeypatch.setattr(akshare_realtime.xq_token_mod, "handle_possible_expiry", handler)
        with (
            patch("tradingagents.dataflows.akshare_realtime.ak") as mock_ak,
            patch("tradingagents.dataflows.akshare_realtime.time.sleep"),
        ):
            from akshare.exceptions import APIError

            mock_ak.stock_individual_spot_xq.side_effect = APIError("400016")
            mock_ak.stock_individual_info_em.return_value = self._eastmoney_info()
            snap = fetch_realtime_snapshot("600519.SS")
        handler.assert_called_once_with("dead")
        assert snap is not None
        assert "eastmoney_info" in snap["sources"]

    def test_no_token_configured_skips_xq_silently(self, monkeypatch, caplog):
        from tradingagents.dataflows.akshare_realtime import fetch_realtime_snapshot

        with patch("tradingagents.dataflows.akshare_realtime.ak") as mock_ak:
            mock_ak.stock_individual_info_em.return_value = self._eastmoney_info()
            snap = fetch_realtime_snapshot("600519.SS")
        mock_ak.stock_individual_spot_xq.assert_not_called()
        assert snap is not None

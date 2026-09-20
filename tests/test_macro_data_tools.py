"""Tests for macro_data_tools module.

The ``@tool``-decorated functions are ``StructuredTool`` objects that
delegate to ``route_to_vendor``. Tests use ``.invoke()`` and mock
``route_to_vendor`` at module level.
"""

from unittest.mock import patch

import pytest


def _make_mock_vendor(return_value: str = "mock_data"):
    return patch(
        "tradingagents.agents.utils.macro_data_tools.route_to_vendor",
        return_value=return_value,
    )


# ===================================================================
# get_macro_indicators
# ===================================================================


@pytest.mark.unit
class TestGetMacroIndicators:
    def test_calls_route_to_vendor(self):
        from tradingagents.agents.utils.macro_data_tools import get_macro_indicators

        with _make_mock_vendor("macro report") as mock_route:
            result = get_macro_indicators.invoke({
                "indicator": "cpi",
                "curr_date": "2026-07-15",
                "look_back_days": 365,
            })

        mock_route.assert_called_once_with(
            "get_macro_indicators", "cpi", "2026-07-15", 365
        )
        assert result == "macro report"

    def test_default_curr_date_and_lookback(self):
        from tradingagents.agents.utils.macro_data_tools import get_macro_indicators

        with _make_mock_vendor("report") as mock_route:
            get_macro_indicators.invoke({"indicator": "unemployment"})

        mock_route.assert_called_once_with(
            "get_macro_indicators", "unemployment", None, None
        )

    def test_different_indicator_and_date(self):
        from tradingagents.agents.utils.macro_data_tools import get_macro_indicators

        with _make_mock_vendor() as mock_route:
            get_macro_indicators.invoke({
                "indicator": "10y_treasury",
                "curr_date": "2026-06-01",
                "look_back_days": 90,
            })

        mock_route.assert_called_once_with(
            "get_macro_indicators", "10y_treasury", "2026-06-01", 90
        )


# ===================================================================
# Cross-cutting: error propagation
# ===================================================================


@pytest.mark.unit
class TestMacroDataToolsErrorPropagation:
    """All functions propagate exceptions from route_to_vendor."""

    def test_get_macro_indicators_propagates(self):
        from tradingagents.agents.utils.macro_data_tools import get_macro_indicators

        with _make_mock_vendor() as mock_route:
            mock_route.side_effect = RuntimeError("vendor error")
            with pytest.raises(RuntimeError, match="vendor error"):
                get_macro_indicators.invoke({
                    "indicator": "cpi",
                    "curr_date": "2026-07-15",
                    "look_back_days": 365,
                })


# ===================================================================
# Tool decorator metadata
# ===================================================================


@pytest.mark.unit
class TestMacroDataToolsMetadata:
    """Verify the function is a @tool-decorated StructuredTool."""

    def test_get_macro_indicators_is_tool(self):
        from tradingagents.agents.utils.macro_data_tools import get_macro_indicators

        assert hasattr(get_macro_indicators, "name")
        assert hasattr(get_macro_indicators, "args")
        assert hasattr(get_macro_indicators, "invoke")
        assert get_macro_indicators.name == "get_macro_indicators"


# ===================================================================
# get_us_macro
# ===================================================================


@pytest.mark.unit
class TestGetUsMacro:
    def test_calls_route_to_vendor_with_default_periods(self):
        from tradingagents.agents.utils.macro_data_tools import get_us_macro

        with _make_mock_vendor("us macro data") as mock_route:
            result = get_us_macro.invoke({})

        mock_route.assert_called_once_with("get_us_macro", 120)
        assert result == "us macro data"

    def test_custom_periods(self):
        from tradingagents.agents.utils.macro_data_tools import get_us_macro

        with _make_mock_vendor() as mock_route:
            get_us_macro.invoke({"periods": 250})

        mock_route.assert_called_once_with("get_us_macro", 250)


# ===================================================================
# get_cftc_cot
# ===================================================================


@pytest.mark.unit
class TestGetCftcCot:
    def test_with_instrument(self):
        from tradingagents.agents.utils.macro_data_tools import get_cftc_cot

        with _make_mock_vendor("cot data") as mock_route:
            result = get_cftc_cot.invoke({"instrument": "白银"})

        mock_route.assert_called_once_with("get_cftc_cot", "白银", 52)
        assert result == "cot data"

    def test_without_instrument_defaults(self):
        from tradingagents.agents.utils.macro_data_tools import get_cftc_cot

        with _make_mock_vendor() as mock_route:
            get_cftc_cot.invoke({})

        mock_route.assert_called_once_with("get_cftc_cot", None, 52)

    def test_custom_periods(self):
        from tradingagents.agents.utils.macro_data_tools import get_cftc_cot

        with _make_mock_vendor() as mock_route:
            get_cftc_cot.invoke({"instrument": "黄金", "periods": 104})

        mock_route.assert_called_once_with("get_cftc_cot", "黄金", 104)


# ===================================================================
# get_eia_petroleum
# ===================================================================


@pytest.mark.unit
class TestGetEiaPetroleum:
    def test_with_series_id(self):
        from tradingagents.agents.utils.macro_data_tools import get_eia_petroleum

        with _make_mock_vendor("eia data") as mock_route:
            result = get_eia_petroleum.invoke({"series_id": "PET.WCESTUS1.W"})

        mock_route.assert_called_once_with("get_eia_petroleum", "PET.WCESTUS1.W", 156)
        assert result == "eia data"

    def test_without_series_id_defaults(self):
        from tradingagents.agents.utils.macro_data_tools import get_eia_petroleum

        with _make_mock_vendor() as mock_route:
            get_eia_petroleum.invoke({})

        mock_route.assert_called_once_with("get_eia_petroleum", None, 156)


# ===================================================================
# Policy registration
# ===================================================================


@pytest.mark.unit
class TestMacroArchiveToolPolicies:
    def test_all_tools_have_registered_policies(self):
        from tradingagents.dataflows.data_policy import policy_for

        for method in ("get_us_macro", "get_cftc_cot", "get_eia_petroleum"):
            policy = policy_for(method)
            assert "smartmoney_db" in policy.allowed_vendors

    def test_no_legacy_policy_warning(self):
        """Registered policies must not fall back to the warned legacy path."""
        from tradingagents.dataflows.data_policy import UnknownToolPolicyError, policy_for

        for method in ("get_us_macro", "get_cftc_cot", "get_eia_petroleum"):
            try:
                policy_for(method)
            except UnknownToolPolicyError:  # pragma: no cover - failure path
                pytest.fail(f"{method} has no registered ToolPolicy")

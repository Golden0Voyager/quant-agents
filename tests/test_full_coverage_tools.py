"""Tests for the full-table coverage tool wrappers and their policies.

Each ``@tool`` wrapper delegates to ``route_to_vendor`` in its own module;
tests mock it at module level and assert the call arguments, plus check
every method resolves a registered ToolPolicy.
"""

from unittest.mock import patch

import pytest

# (tool name, utils module, invoke kwargs, expected route_to_vendor args)
_WRAPPERS = [
    ("get_placement_announcements", "news_data_tools", {"symbol": "600519.SS", "periods": 5},
     ("get_placement_announcements", "600519.SS", 5)),
    ("get_stock_repurchase", "news_data_tools", {"symbol": "600519.SS"},
     ("get_stock_repurchase", "600519.SS", 10)),
    ("get_south_flow", "fund_flow_tools", {"market": "南向", "periods": 30},
     ("get_south_flow", "南向", 30)),
    ("get_dividend_summary", "fundamental_data_tools", {"symbol": "600519.SS"},
     ("get_dividend_summary", "600519.SS")),
    ("get_ah_premium", "core_stock_tools", {"symbol": "688981.SS", "periods": 30},
     ("get_ah_premium", "688981.SS", 30)),
    ("get_etf_daily", "core_stock_tools", {"ts_code": "510300", "periods": 60},
     ("get_etf_daily", "510300", 60)),
    ("get_cb_quotation", "core_stock_tools", {},
     ("get_cb_quotation",)),
    ("get_cb_redeem", "core_stock_tools", {},
     ("get_cb_redeem",)),
    ("get_cb_index", "core_stock_tools", {"index_code": "JSL_EW", "periods": 60},
     ("get_cb_index", "JSL_EW", 60)),
    ("get_gold_price", "commodity_data_tools", {"periods": 30},
     ("get_gold_price", 30)),
    ("get_hk_tech_index", "macro_data_tools", {"periods": 60},
     ("get_hk_tech_index", 60)),
    ("get_fx_rate", "macro_data_tools", {"currency": "美元", "periods": 30},
     ("get_fx_rate", "美元", 30)),
    ("get_central_bank_balance", "macro_data_tools", {"periods": 24},
     ("get_central_bank_balance", 24)),
    ("get_option_sentiment", "market_breadth_tools", {"periods": 30},
     ("get_option_sentiment", 30)),
    ("get_index_futures_basis", "market_breadth_tools", {"futures_code": "IF0", "periods": 30},
     ("get_index_futures_basis", "IF0", 30)),
    ("get_sector_daily", "market_breadth_tools", {"sector_name": "半导体", "periods": 60},
     ("get_sector_daily", "半导体", 60)),
    ("get_sector_valuation", "market_breadth_tools", {"sector_name": "半导体"},
     ("get_sector_valuation", "半导体", 120)),
]

_METHODS = [name for name, *_ in _WRAPPERS]


@pytest.mark.unit
class TestFullCoverageToolWrappers:
    @pytest.mark.parametrize(
        ("tool_name", "module", "invoke_kwargs", "expected_call"),
        _WRAPPERS,
        ids=_METHODS,
    )
    def test_delegates_to_route_to_vendor(self, tool_name, module, invoke_kwargs, expected_call):
        import importlib

        mod = importlib.import_module(f"tradingagents.agents.utils.{module}")
        tool = getattr(mod, tool_name)

        with patch.object(mod, "route_to_vendor", return_value="mock_data") as mock_route:
            result = tool.invoke(invoke_kwargs)

        mock_route.assert_called_once_with(*expected_call)
        assert result == "mock_data"


@pytest.mark.unit
class TestFullCoveragePolicies:
    def test_all_methods_have_registered_policies(self):
        from tradingagents.dataflows.data_policy import policy_for

        for method in _METHODS:
            policy = policy_for(method)
            assert "smartmoney_db" in policy.allowed_vendors, method

    def test_per_stock_methods_are_ashare_only(self):
        from tradingagents.dataflows.data_policy import policy_for

        for method in (
            "get_placement_announcements",
            "get_stock_repurchase",
            "get_dividend_summary",
            "get_ah_premium",
            "get_etf_daily",
        ):
            policy = policy_for(method)
            assert policy.applicable_markets == frozenset({"XSHG"}), method

    def test_no_legacy_policy_warning(self):
        """Registered policies must not fall back to the warned legacy path."""
        from tradingagents.dataflows.data_policy import UnknownToolPolicyError, policy_for

        for method in _METHODS:
            try:
                policy_for(method)
            except UnknownToolPolicyError:  # pragma: no cover - failure path
                pytest.fail(f"{method} has no registered ToolPolicy")

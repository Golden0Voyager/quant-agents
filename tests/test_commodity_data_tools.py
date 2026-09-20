"""Tests for commodity_data_tools module.

Both ``@tool``-decorated functions are ``StructuredTool`` objects that
delegate to ``route_to_vendor``. Tests use ``.invoke()`` and mock
``route_to_vendor`` at module level, mirroring test_fund_flow_tools.py.
"""

from unittest.mock import patch

import pytest


def _make_mock_vendor(return_value: str = "mock_data"):
    return patch(
        "tradingagents.agents.utils.commodity_data_tools.route_to_vendor",
        return_value=return_value,
    )


# ===================================================================
# get_lithium_spot
# ===================================================================


@pytest.mark.unit
class TestGetLithiumSpot:
    def test_calls_route_to_vendor_with_default_periods(self):
        from tradingagents.agents.utils.commodity_data_tools import get_lithium_spot

        with _make_mock_vendor("lithium data") as mock_route:
            result = get_lithium_spot.invoke({})

        mock_route.assert_called_once_with("get_lithium_spot", 60)
        assert result == "lithium data"

    def test_custom_periods(self):
        from tradingagents.agents.utils.commodity_data_tools import get_lithium_spot

        with _make_mock_vendor() as mock_route:
            get_lithium_spot.invoke({"periods": 120})

        mock_route.assert_called_once_with("get_lithium_spot", 120)


# ===================================================================
# get_commodity_futures
# ===================================================================


@pytest.mark.unit
class TestGetCommodityFutures:
    def test_calls_route_to_vendor_with_default_periods(self):
        from tradingagents.agents.utils.commodity_data_tools import get_commodity_futures

        with _make_mock_vendor("futures data") as mock_route:
            result = get_commodity_futures.invoke({"variety": "AG"})

        mock_route.assert_called_once_with("get_commodity_futures", "AG", 60)
        assert result == "futures data"

    def test_custom_periods(self):
        from tradingagents.agents.utils.commodity_data_tools import get_commodity_futures

        with _make_mock_vendor() as mock_route:
            get_commodity_futures.invoke({"variety": "LC", "periods": 30})

        mock_route.assert_called_once_with("get_commodity_futures", "LC", 30)


# ===================================================================
# Policy registration
# ===================================================================


@pytest.mark.unit
class TestCommodityToolPolicies:
    def test_both_tools_have_registered_policies(self):
        from tradingagents.dataflows.data_policy import policy_for

        for method in ("get_lithium_spot", "get_commodity_futures"):
            policy = policy_for(method)
            assert "smartmoney_db" in policy.allowed_vendors

    def test_no_legacy_policy_warning(self):
        """Registered policies must not fall back to the warned legacy path."""
        from tradingagents.dataflows.data_policy import UnknownToolPolicyError, policy_for

        for method in ("get_lithium_spot", "get_commodity_futures"):
            try:
                policy_for(method)
            except UnknownToolPolicyError:  # pragma: no cover - failure path
                pytest.fail(f"{method} has no registered ToolPolicy")

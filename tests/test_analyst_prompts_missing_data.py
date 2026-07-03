import inspect

import pytest

from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
from tradingagents.agents.analysts.governance_analyst import create_governance_analyst
from tradingagents.agents.analysts.industry_analyst import create_industry_analyst
from tradingagents.agents.analysts.market_analyst import create_market_analyst
from tradingagents.agents.analysts.news_analyst import create_news_analyst

MISSING_DATA_PROTOCOL_MARKERS = [
    "Missing Data Protocol",
    "NO_DATA_AVAILABLE",
    "data_availability",
    "Never fabricate",
]


@pytest.mark.unit
class TestMissingDataProtocolInPrompts:

    _ANALYST_FACTORIES = {
        "market": create_market_analyst,
        "news": create_news_analyst,
        "fundamentals": create_fundamentals_analyst,
        "governance": create_governance_analyst,
        "industry": create_industry_analyst,
    }

    def _get_source(self, factory_name: str) -> str:
        return inspect.getsource(self._ANALYST_FACTORIES[factory_name])

    def test_market_analyst_has_missing_data_protocol(self):
        source = self._get_source("market")
        for marker in MISSING_DATA_PROTOCOL_MARKERS:
            assert marker in source, f"market_analyst missing '{marker}'"

    def test_news_analyst_has_missing_data_protocol(self):
        source = self._get_source("news")
        for marker in MISSING_DATA_PROTOCOL_MARKERS:
            assert marker in source, f"news_analyst missing '{marker}'"

    def test_fundamentals_analyst_has_missing_data_protocol(self):
        source = self._get_source("fundamentals")
        for marker in MISSING_DATA_PROTOCOL_MARKERS:
            assert marker in source, f"fundamentals_analyst missing '{marker}'"

    def test_governance_analyst_has_missing_data_protocol(self):
        source = self._get_source("governance")
        for marker in MISSING_DATA_PROTOCOL_MARKERS:
            assert marker in source, f"governance_analyst missing '{marker}'"

    def test_industry_analyst_has_missing_data_protocol(self):
        source = self._get_source("industry")
        for marker in MISSING_DATA_PROTOCOL_MARKERS:
            assert marker in source, f"industry_analyst missing '{marker}'"

"""Unit tests for tradingagents/dataflows/cailianpress_vendor.py.

Mocks ``requests.get`` to avoid real API calls. Follows the same patterns
as ``test_eastmoney_sentiment.py`` and ``test_akshare_vendor_mocked.py``.
"""
from __future__ import annotations

import hashlib
from unittest.mock import ANY, MagicMock, patch

import pytest

from tradingagents.dataflows import cailianpress_vendor

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fake API response fixtures
# ---------------------------------------------------------------------------

_FAKE_ROLL_DATA = [
    {
        "id": 12345,
        "ctime": 1784810000,  # ~2026-07-23
        "title": "突发：央行宣布降准50个基点",
        "brief": "中国人民银行决定自2026年7月25日起下调金融机构存款准备金率0.5个百分点，释放长期资金约1万亿元。",
        "level": "A",
        "stock_list": [
            {"name": "工商银行", "StockID": "601398", "RiseRange": 0.5},
            {"name": "招商银行", "StockID": "600036", "RiseRange": 1.2},
        ],
    },
    {
        "id": 12346,
        "ctime": 1784780000,  # slightly earlier
        "title": "贵州茅台公布半年报",
        "brief": "贵州茅台上半年实现营收同比增长15.2%",
        "level": "B",
        "stock_list": [
            {"name": "贵州茅台", "StockID": "600519"},
        ],
    },
    {
        "id": 12347,
        "ctime": 1784750000,
        "title": "宁德时代与特斯拉签订供货协议",
        "brief": "宁德时代与特斯拉签订长期供货协议",
        "level": "C",
        "stock_list": None,
    },
]

_FAKE_API_RESPONSE = {
    "error": 0,
    "data": {
        "roll_data": _FAKE_ROLL_DATA,
    },
}

_EMPTY_API_RESPONSE = {
    "error": 0,
    "data": {
        "roll_data": [],
    },
}

_ERROR_API_RESPONSE = {
    "error": 1,
    "data": None,
}


# ===================================================================
# Helpers: _generate_signature
# ===================================================================


class TestGenerateSignature:
    """Tests for the internal _generate_signature helper."""

    def test_deterministic_output(self):
        """Same input should produce same signature."""
        params = {"a": "1", "b": "2"}
        sig1 = cailianpress_vendor._generate_signature(params)
        sig2 = cailianpress_vendor._generate_signature(params)
        assert sig1 == sig2

    def test_matches_algorithm(self):
        """Verify the SHA1→MD5 chain manually."""
        params = {"app_name": "CailianpressWeb", "os": "web"}
        expected_raw = "app_name=CailianpressWeb&os=web"
        expected_sha1 = hashlib.sha1(expected_raw.encode("utf-8")).hexdigest()
        expected_md5 = hashlib.md5(expected_sha1.encode("utf-8")).hexdigest()
        assert cailianpress_vendor._generate_signature(params) == expected_md5

    def test_sorted_by_key(self):
        """Keys should be sorted alphabetically before signing."""
        # If sorted, "a" comes before "z"
        unsorted = {"z": "last", "a": "first", "m": "middle"}
        expected_raw = "a=first&m=middle&z=last"
        expected = hashlib.md5(
            hashlib.sha1(expected_raw.encode("utf-8")).hexdigest().encode("utf-8")
        ).hexdigest()
        assert cailianpress_vendor._generate_signature(unsorted) == expected

    def test_empty_params(self):
        """Empty dict should still produce a valid signature."""
        sig = cailianpress_vendor._generate_signature({})
        assert isinstance(sig, str)
        assert len(sig) == 32  # MD5 hex digest


# ===================================================================
# Helpers: _get_request_params
# ===================================================================


class TestGetRequestParams:
    """Tests for the internal _get_request_params helper."""

    def test_contains_expected_keys(self):
        """Params should include app_name, os, sv, rn, and sign."""
        params = cailianpress_vendor._build_request_params(limit=20)
        assert params["app_name"] == "CailianpressWeb"
        assert params["os"] == "web"
        assert params["sv"] == "8.4.6"
        assert params["rn"] == "20"
        assert "sign" in params

    def test_limit_is_string(self):
        """rn must be a string per the API contract."""
        params = cailianpress_vendor._build_request_params(limit=10)
        assert params["rn"] == "10"
        assert isinstance(params["rn"], str)

    def test_sign_is_valid_md5(self):
        """The sign field should be a valid 32-char MD5 hex digest."""
        params = cailianpress_vendor._build_request_params(limit=15)
        sig = params["sign"]
        assert len(sig) == 32
        # All hex chars
        int(sig, 16)  # raises ValueError if invalid


# ===================================================================
# Main function: fetch_cailianpress_telegrams
# ===================================================================


class TestFetchCailianpressTelegrams:
    """Tests for fetch_cailianpress_telegrams()."""

    def _mock_response(self, json_data: dict, status_code: int = 200) -> MagicMock:
        """Build a mock requests.Response."""
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data
        resp.raise_for_status.return_value = None
        return resp

    # -- Happy path -------------------------------------------------------

    def test_happy_path(self):
        """Valid response should produce a formatted markdown string."""
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = self._mock_response(_FAKE_API_RESPONSE)

            result = cailianpress_vendor.fetch_cailianpress_telegrams()

        # Title should appear
        assert "# 财联社电报" in result
        assert "Flash News - Cailianpress Telegraph" in result
        # All three telegrams should appear
        assert "降准50个基点" in result
        assert "贵州茅台公布半年报" in result
        assert "宁德时代" in result
        # Stock lists should appear
        assert "工商银行(601398) +0.50%" in result
        assert "招商银行(600036) +1.20%" in result
        assert "贵州茅台(600519)" in result
        # Important marker for level A
        assert "⚠️" in result
        # Timestamps
        assert "2026" in result
        # Detail links
        assert "cls.cn/detail/12345" in result
        assert "cls.cn/detail/12346" in result
        assert "cls.cn/detail/12347" in result
        # Count line
        assert "3 条电报" in result
        assert "Cailianpress" in result

    def test_happy_path_with_limit(self):
        """limit parameter should cap the number of telegrams returned."""
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = self._mock_response(_FAKE_API_RESPONSE)

            result = cailianpress_vendor.fetch_cailianpress_telegrams(limit=2)

        # Only 2 should appear
        assert "2 条电报" in result
        # First two (newest) should be present
        assert "降准50个基点" in result
        assert "贵州茅台公布半年报" in result
        # Third should NOT appear
        assert "宁德时代" not in result

    def test_last_time_passed_in_params(self):
        """When last_time is provided, it should appear in the request params URL."""
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = self._mock_response(_FAKE_API_RESPONSE)

            cailianpress_vendor.fetch_cailianpress_telegrams(last_time=1700000000)

        # The URL should contain last_time=1700000000
        call_url = str(mock_get.call_args[0][0])
        assert "last_time=1700000000" in call_url

    def test_uses_requests_get(self):
        """requests.get should be called with the correct URL and headers."""
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = self._mock_response(_FAKE_API_RESPONSE)

            cailianpress_vendor.fetch_cailianpress_telegrams()

        mock_get.assert_called_once()
        call_args = mock_get.call_args
        # URL should contain the base
        assert "cls.cn/nodeapi/updateTelegraphList" in str(call_args[0][0])
        # Headers should be set
        headers = call_args[1]["headers"]
        assert headers["User-Agent"].startswith("Mozilla/")
        assert "application/json" in headers["Accept"]

    # -- Error handling ---------------------------------------------------

    def test_api_returns_error_code(self):
        """When API returns non-zero error code, RuntimeError should be raised."""
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = self._mock_response(_ERROR_API_RESPONSE)

            with pytest.raises(RuntimeError, match="Cailianpress API returned error"):
                cailianpress_vendor.fetch_cailianpress_telegrams()

    def test_empty_roll_data_raises(self):
        """When roll_data is an empty list, RuntimeError should be raised."""
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = self._mock_response(_EMPTY_API_RESPONSE)

            with pytest.raises(RuntimeError, match="No telegrams returned"):
                cailianpress_vendor.fetch_cailianpress_telegrams()

    def test_network_failure_raises(self):
        """When requests.get raises, RuntimeError should be raised."""
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.side_effect = ConnectionError("DNS resolution failed")

            with pytest.raises(RuntimeError, match="Cailianpress API request failed"):
                cailianpress_vendor.fetch_cailianpress_telegrams()

    def test_http_error_raises(self):
        """HTTP errors (e.g., 500) should propagate as RuntimeError."""
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("HTTP 500 Server Error")

        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = resp

            with pytest.raises(RuntimeError, match="Cailianpress API request failed"):
                cailianpress_vendor.fetch_cailianpress_telegrams()

    def test_invalid_json_raises(self):
        """When response is not valid JSON, RuntimeError should be raised."""
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.side_effect = ValueError("Expecting value")

        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = resp

            with pytest.raises(RuntimeError, match="Cailianpress API request failed"):
                cailianpress_vendor.fetch_cailianpress_telegrams()

    def test_missing_data_key_raises(self):
        """When response has no 'data' key, RuntimeError should be raised."""
        resp = self._mock_response({"error": 0, "data": None})
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = resp

            with pytest.raises(RuntimeError, match="Cailianpress API returned error"):
                cailianpress_vendor.fetch_cailianpress_telegrams()

    # -- Edge cases -------------------------------------------------------

    def test_no_stock_list_still_renders(self):
        """Telegrams without stock_list should still appear (no crash)."""
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = self._mock_response(_FAKE_API_RESPONSE)

            result = cailianpress_vendor.fetch_cailianpress_telegrams()

        # The C-level telegram has stock_list=None, should still appear
        assert "宁德时代" in result
        # No stock line should follow it
        assert "宁德时代" in result  # just verify it rendered

    def test_no_brief_falls_back_to_title(self):
        """When brief is empty, the title should be used instead."""
        roll = [
            {
                "id": 999,
                "ctime": 1784810000,
                "title": "简讯标题",
                "brief": "",
                "level": "C",
                "stock_list": [],
            }
        ]
        resp = {"error": 0, "data": {"roll_data": roll}}
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = self._mock_response(resp)
            result = cailianpress_vendor.fetch_cailianpress_telegrams()

        assert "简讯标题" in result
        # no quote block since brief == title
        assert "> 简讯标题" not in result  # brief and title are the same

    def test_red_keyword_triggers_important_flag(self):
        """Even level C telegrams with red keywords should be marked important."""
        roll = [
            {
                "id": 1000,
                "ctime": 1784810000,
                "title": "利好 某公司业绩大增",
                "brief": "某公司公告净利润大幅增长",
                "level": "C",
                "stock_list": [],
            }
        ]
        resp = {"error": 0, "data": {"roll_data": roll}}
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = self._mock_response(resp)
            result = cailianpress_vendor.fetch_cailianpress_telegrams()

        # Should have the warning emoji because "利好" is a RED_KEYWORD
        assert "⚠️" in result

    def test_level_a_automatically_important(self):
        """Level A telegrams should always be marked important."""
        roll = [
            {
                "id": 1001,
                "ctime": 1784810000,
                "title": "普通新闻",
                "brief": "普通新闻内容",
                "level": "A",
                "stock_list": [],
            }
        ]
        resp = {"error": 0, "data": {"roll_data": roll}}
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = self._mock_response(resp)
            result = cailianpress_vendor.fetch_cailianpress_telegrams()

        assert "⚠️" in result

    def test_level_c_not_important(self):
        """Level C telegrams without red keywords should NOT be marked important."""
        roll = [
            {
                "id": 1002,
                "ctime": 1784810000,
                "title": "日常行业新闻",
                "brief": "某行业动态更新",
                "level": "C",
                "stock_list": [],
            }
        ]
        resp = {"error": 0, "data": {"roll_data": roll}}
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = self._mock_response(resp)
            result = cailianpress_vendor.fetch_cailianpress_telegrams()

        assert "⚠️" not in result

    def test_sorted_by_timestamp_descending(self):
        """Telegrams should be sorted newest-first regardless of API order."""
        # Provide data in reverse-chronological order (oldest first)
        roll = [
            {
                "id": 1,
                "ctime": 1000,  # oldest
                "title": "Old news",
                "brief": "",
                "level": "C",
                "stock_list": [],
            },
            {
                "id": 2,
                "ctime": 3000,  # newest
                "title": "New news",
                "brief": "",
                "level": "C",
                "stock_list": [],
            },
            {
                "id": 3,
                "ctime": 2000,  # middle
                "title": "Middle news",
                "brief": "",
                "level": "C",
                "stock_list": [],
            },
        ]
        resp = {"error": 0, "data": {"roll_data": roll}}
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = self._mock_response(resp)
            result = cailianpress_vendor.fetch_cailianpress_telegrams()

        # Find positions in the output string
        new_pos = result.index("New news")
        mid_pos = result.index("Middle news")
        old_pos = result.index("Old news")
        assert new_pos < mid_pos < old_pos, "Should be sorted newest-first"

    def test_brief_truncated_to_200_chars(self):
        """Very long brief should be truncated with ellipsis."""
        long_brief = "数据" * 150  # 300 chars
        roll = [
            {
                "id": 2000,
                "ctime": 1784810000,
                "title": "长文新闻",
                "brief": long_brief,
                "level": "C",
                "stock_list": [],
            }
        ]
        resp = {"error": 0, "data": {"roll_data": roll}}
        with patch.object(cailianpress_vendor.requests, "get") as mock_get:
            mock_get.return_value = self._mock_response(resp)
            result = cailianpress_vendor.fetch_cailianpress_telegrams()

        assert "..." in result
        # Find the quote line
        lines = [l for l in result.split("\n") if l.startswith("> ")]
        assert len(lines) == 1
        # Content after "> " should be 200 chars + "..." = 203 chars
        quote_content = lines[0][2:]
        assert len(quote_content) == 203, (
            f"Expected 203 chars (200 truncated + ...), got {len(quote_content)}"
        )

    # -- Tool integration -------------------------------------------------

    def test_called_via_news_data_tools(self):
        """Verify the get_cailianpress_telegrams tool calls into the vendor correctly."""
        from tradingagents.agents.utils.news_data_tools import get_cailianpress_telegrams

        with patch.object(cailianpress_vendor, "fetch_cailianpress_telegrams") as mock_fetch:
            mock_fetch.return_value = "# 财联社电报"
            result = get_cailianpress_telegrams.invoke({"limit": 10})

        assert result == "# 财联社电报"
        mock_fetch.assert_called_once_with(limit=10)

    def test_tool_invoke_error_returns_placeholder(self):
        """When the vendor raises, the tool should return NO_DATA_AVAILABLE."""
        from tradingagents.agents.utils.news_data_tools import get_cailianpress_telegrams

        with patch.object(cailianpress_vendor, "fetch_cailianpress_telegrams") as mock_fetch:
            mock_fetch.side_effect = RuntimeError("API down")
            result = get_cailianpress_telegrams.invoke({})

        assert "NO_DATA_AVAILABLE" in result
        assert "Cailianpress telegrams unavailable" in result
        assert "RuntimeError" in result

    def test_importable_from_agent_utils(self):
        """get_cailianpress_telegrams should be importable from agent_utils."""
        from tradingagents.agents.utils.agent_utils import get_cailianpress_telegrams

        # It's a LangChain @tool which should be callable
        assert hasattr(get_cailianpress_telegrams, "name")

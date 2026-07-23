import unittest
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from tradingagents.llm_clients.google_client import GoogleClient
from tradingagents.llm_clients.retry_utils import RetryConfig


@pytest.mark.unit
class TestGoogleApiKeyStandardization(unittest.TestCase):
    """Verify GoogleClient accepts unified api_key parameter."""

    @patch("tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI")
    def test_api_key_handling(self, mock_chat):
        test_cases = [
            ("unified api_key is mapped", {"api_key": "test-key-123"}, "test-key-123"),
            ("legacy google_api_key still works", {"google_api_key": "legacy-key-456"}, "legacy-key-456"),
            ("unified api_key takes precedence", {"api_key": "unified", "google_api_key": "legacy"}, "unified"),
        ]

        for msg, kwargs, expected_key in test_cases:
            with self.subTest(msg=msg):
                mock_chat.reset_mock()
                client = GoogleClient("gemini-3.5-flash", **kwargs)
                client.get_llm()
                call_kwargs = mock_chat.call_args[1]
                self.assertEqual(call_kwargs.get("google_api_key"), expected_key)


# =========================================================================
# Tests merged from test_final_push.py and test_llm_and_minor.py
# =========================================================================


class GoogleClientEdgeTests(unittest.TestCase):
    """Lines 17, 33, 50-58."""

    def test_normalized_chat_google_invoke(self):
        from tradingagents.llm_clients.google_client import NormalizedChatGoogleGenerativeAI

        client = NormalizedChatGoogleGenerativeAI(model="gemini-3-pro", google_api_key="test")
        raw_response = MagicMock()
        raw_response.content = "normalized"
        with (
            patch.object(NormalizedChatGoogleGenerativeAI, "invoke", wraps=client.invoke),
            patch("langchain_google_genai.ChatGoogleGenerativeAI.invoke", return_value=raw_response),
            patch("tradingagents.llm_clients.google_client.normalize_content", return_value=raw_response),
        ):
            result = client.invoke("hello")
        self.assertEqual(result.content, "normalized")

    def test_get_llm_with_base_url(self):
        import tradingagents.llm_clients.google_client as mod
        from tradingagents.llm_clients.google_client import GoogleClient

        captured = {}
        with patch.object(mod, "NormalizedChatGoogleGenerativeAI", lambda **kwargs: captured.__setitem__("kwargs", kwargs)):
            client = GoogleClient(model="gemini-3-pro", base_url="https://custom.google.com", google_api_key="test")
            client.get_llm()
        self.assertEqual(captured["kwargs"]["base_url"], "https://custom.google.com")

    def test_get_llm_without_base_url(self):
        import tradingagents.llm_clients.google_client as mod
        from tradingagents.llm_clients.google_client import GoogleClient

        captured = {}
        with patch.object(mod, "NormalizedChatGoogleGenerativeAI", lambda **kwargs: captured.__setitem__("kwargs", kwargs)):
            client = GoogleClient(model="gemini-3-pro", google_api_key="test")
            client.get_llm()
        self.assertNotIn("base_url", captured["kwargs"])

    def test_thinking_level_gemini_3_pro_minimal_to_low(self):
        import tradingagents.llm_clients.google_client as mod
        from tradingagents.llm_clients.google_client import GoogleClient

        captured = {}
        with patch.object(mod, "NormalizedChatGoogleGenerativeAI", lambda **kwargs: captured.__setitem__("kwargs", kwargs)):
            client = GoogleClient(model="gemini-3-pro", thinking_level="minimal", google_api_key="test")
            client.get_llm()
        self.assertEqual(captured["kwargs"]["thinking_level"], "low")

    def test_thinking_level_gemini_3_flash_preserves_minimal(self):
        import tradingagents.llm_clients.google_client as mod
        from tradingagents.llm_clients.google_client import GoogleClient

        captured = {}
        with patch.object(mod, "NormalizedChatGoogleGenerativeAI", lambda **kwargs: captured.__setitem__("kwargs", kwargs)):
            client = GoogleClient(model="gemini-3-flash", thinking_level="minimal", google_api_key="test")
            client.get_llm()
        self.assertEqual(captured["kwargs"]["thinking_level"], "minimal")

    def test_thinking_level_gemini_25_high_sets_budget(self):
        import tradingagents.llm_clients.google_client as mod
        from tradingagents.llm_clients.google_client import GoogleClient

        captured = {}
        with patch.object(mod, "NormalizedChatGoogleGenerativeAI", lambda **kwargs: captured.__setitem__("kwargs", kwargs)):
            client = GoogleClient(model="gemini-2.5-pro", thinking_level="high", google_api_key="test")
            client.get_llm()
        self.assertEqual(captured["kwargs"]["thinking_level"], "high")

    def test_thinking_level_gemini_25_low_sets_budget(self):
        import tradingagents.llm_clients.google_client as mod
        from tradingagents.llm_clients.google_client import GoogleClient

        captured = {}
        with patch.object(mod, "NormalizedChatGoogleGenerativeAI", lambda **kwargs: captured.__setitem__("kwargs", kwargs)):
            client = GoogleClient(model="gemini-2.5-flash", thinking_level="low", google_api_key="test")
            client.get_llm()
        self.assertEqual(captured["kwargs"]["thinking_level"], "low")

    def test_get_llm_missing_model_raises(self):
        from tradingagents.llm_clients.google_client import GoogleClient
        client = GoogleClient("", google_api_key="test")
        with self.assertRaises(ValueError):
            client.get_llm()

    def test_google_api_key_resolution(self):
        import tradingagents.llm_clients.google_client as mod
        from tradingagents.llm_clients.google_client import GoogleClient

        captured = {}
        with patch.object(mod, "NormalizedChatGoogleGenerativeAI", lambda **kwargs: captured.__setitem__("kwargs", kwargs)):
            client = GoogleClient(model="gemini-3-pro", api_key="unified-key", google_api_key="specific-key")
            client.get_llm()
        self.assertEqual(captured["kwargs"]["google_api_key"], "unified-key")

    def test_google_api_key_fallback(self):
        import tradingagents.llm_clients.google_client as mod
        from tradingagents.llm_clients.google_client import GoogleClient

        captured = {}
        with patch.object(mod, "NormalizedChatGoogleGenerativeAI", lambda **kwargs: captured.__setitem__("kwargs", kwargs)):
            client = GoogleClient(model="gemini-3-pro", google_api_key="specific-key")
            client.get_llm()
        self.assertEqual(captured["kwargs"]["google_api_key"], "specific-key")


class TestGoogleClient(unittest.TestCase):

    def test_validate_model(self):
        """Line 62–64: validate_model delegates."""
        from tradingagents.llm_clients.google_client import GoogleClient

        with patch(
            "tradingagents.llm_clients.google_client.validate_model",
            return_value=True,
        ):
            client = GoogleClient("gemini-2.5-flash")
            self.assertTrue(client.validate_model())

    @patch("tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI")
    def test_thinking_level_gemini3_pro_minimal_to_low(self, mock_chat):
        """Lines 51–53: Gemini 3 Pro with 'minimal' is remapped to 'low'."""
        from tradingagents.llm_clients.google_client import GoogleClient

        client = GoogleClient(
            "gemini-3-pro", thinking_level="minimal", api_key="x"
        )
        client.get_llm()
        call_kwargs = mock_chat.call_args[1]
        self.assertEqual(call_kwargs["thinking_level"], "low")

    @patch("tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI")
    def test_thinking_level_gemini3_flash_minimal_passes(self, mock_chat):
        """Line 55: Gemini 3 Flash accepts 'minimal'."""
        from tradingagents.llm_clients.google_client import GoogleClient

        client = GoogleClient(
            "gemini-3-flash", thinking_level="minimal", api_key="x"
        )
        client.get_llm()
        call_kwargs = mock_chat.call_args[1]
        self.assertEqual(call_kwargs["thinking_level"], "minimal")

    @patch("tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI")
    def test_thinking_level_gemini3_pro_high_passes(self, mock_chat):
        """Line 55: Gemini 3 Pro with 'high' passes through."""
        from tradingagents.llm_clients.google_client import GoogleClient

        client = GoogleClient(
            "gemini-3-pro", thinking_level="high", api_key="x"
        )
        client.get_llm()
        call_kwargs = mock_chat.call_args[1]
        self.assertEqual(call_kwargs["thinking_level"], "high")

    @patch("tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI")
    def test_thinking_level_gemini25_high_to_budget(self, mock_chat):
        """Gemini 2.5 retired: 'high' passes through as thinking_level."""
        from tradingagents.llm_clients.google_client import GoogleClient

        client = GoogleClient(
            "gemini-2.5-flash", thinking_level="high", api_key="x"
        )
        client.get_llm()
        call_kwargs = mock_chat.call_args[1]
        self.assertEqual(call_kwargs["thinking_level"], "high")

    @patch("tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI")
    def test_thinking_level_gemini25_low_to_budget_zero(self, mock_chat):
        """Gemini 2.5 retired: 'low' passes through as thinking_level."""
        from tradingagents.llm_clients.google_client import GoogleClient

        client = GoogleClient(
            "gemini-2.5-flash", thinking_level="low", api_key="x"
        )
        client.get_llm()
        call_kwargs = mock_chat.call_args[1]
        self.assertEqual(call_kwargs["thinking_level"], "low")

    @patch("tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI")
    def test_thinking_level_none_skips(self, mock_chat):
        """Line 49: no thinking_level means no key added."""
        from tradingagents.llm_clients.google_client import GoogleClient

        client = GoogleClient("gemini-2.5-flash", api_key="x")
        client.get_llm()
        call_kwargs = mock_chat.call_args[1]
        self.assertNotIn("thinking_level", call_kwargs)
        self.assertNotIn("thinking_budget", call_kwargs)


class GoogleRetryTests(unittest.TestCase):
    """Retry/backoff behavior on NormalizedChatGoogleGenerativeAI and GoogleClient."""

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_invoke_retries_on_rate_limit(self, mock_sleep):
        from tradingagents.llm_clients.google_client import NormalizedChatGoogleGenerativeAI

        client = NormalizedChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key="test")
        client._retry_config = RetryConfig(max_retries=1, base_delay=0.05)
        raw_response = MagicMock(content="ok")
        side_effect = [
            Exception("rate limit exceeded"),
            raw_response,
        ]
        with patch("langchain_google_genai.ChatGoogleGenerativeAI.invoke", side_effect=side_effect):
            result = client.invoke("hello")
        self.assertEqual(result.content, "ok")
        mock_sleep.assert_called_once_with(0.05)

    @patch("tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI")
    def test_get_llm_binds_retry_config(self, mock_chat):
        retry_config = RetryConfig(max_retries=4, base_delay=0.5)
        client = GoogleClient("gemini-2.5-flash", api_key="x", retry_config=retry_config)
        llm = client.get_llm()
        self.assertEqual(llm._retry_config, retry_config)


class GoogleClientGetLlmTests(unittest.TestCase):
    """Cover remaining branches in GoogleClient.get_llm()."""

    @patch("tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI")
    def test_get_llm_forwards_common_kwargs(self, mock_chat):
        """Line 38-39: timeout, temperature etc. are forwarded to the LLM."""
        from tradingagents.llm_clients.google_client import GoogleClient

        client = GoogleClient(
            "gemini-3-pro",
            timeout=30,
            temperature=0.5,
            max_retries=3,
            api_key="test",
        )
        client.get_llm()
        call_kwargs = mock_chat.call_args[1]
        self.assertEqual(call_kwargs["timeout"], 30)
        self.assertEqual(call_kwargs["temperature"], 0.5)
        self.assertEqual(call_kwargs["max_retries"], 3)

    @patch("tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI")
    def test_get_llm_omits_kwargs_not_in_forwarding_list(self, mock_chat):
        """Line 38-39: unknown kwargs are NOT forwarded."""
        from tradingagents.llm_clients.google_client import GoogleClient

        client = GoogleClient(
            "gemini-3-pro",
            unknown_param="should-not-appear",
            api_key="test",
        )
        client.get_llm()
        call_kwargs = mock_chat.call_args[1]
        self.assertNotIn("unknown_param", call_kwargs)

    @patch("tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI")
    def test_get_llm_without_api_key(self, mock_chat):
        """Branch 43->50: when no api_key is provided, google_api_key is not
        added to llm_kwargs and the code falls through to thinking_level."""
        from tradingagents.llm_clients.google_client import GoogleClient

        client = GoogleClient("gemini-3-pro")  # no api_key
        client.get_llm()
        call_kwargs = mock_chat.call_args[1]
        self.assertNotIn("google_api_key", call_kwargs)

    @patch("tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI")
    def test_get_llm_with_http_client(self, mock_chat):
        """Line 38-39: http_client and http_async_client are forwarded."""
        from tradingagents.llm_clients.google_client import GoogleClient

        http_client = MagicMock()
        http_async_client = MagicMock()
        client = GoogleClient(
            "gemini-3-pro",
            http_client=http_client,
            http_async_client=http_async_client,
            api_key="test",
        )
        client.get_llm()
        call_kwargs = mock_chat.call_args[1]
        self.assertIs(call_kwargs["http_client"], http_client)
        self.assertIs(call_kwargs["http_async_client"], http_async_client)


class NormalizedInvokeTests(unittest.TestCase):
    """Cover line 19: normalize_content is called on super().invoke()."""

    @patch("tradingagents.llm_clients.google_client.normalize_content")
    def test_invoke_calls_normalize_on_super_result(self, mock_normalize):
        from langchain_google_genai import ChatGoogleGenerativeAI

        from tradingagents.llm_clients.google_client import (
            NormalizedChatGoogleGenerativeAI,
        )

        mock_normalize.return_value = "normalized"

        # Patch __init__ to avoid httpx SOCKS proxy setup which requires
        # the optional ``socksio`` package (#1024).
        with patch.object(ChatGoogleGenerativeAI, "__init__", return_value=None):
            client = NormalizedChatGoogleGenerativeAI(
                model="gemini-3-pro", google_api_key="test",
            )

        with patch(
            "langchain_google_genai.ChatGoogleGenerativeAI.invoke",
            return_value="raw_response",
        ) as mock_super:
            result = client.invoke("hello")

        mock_super.assert_called_once()
        mock_normalize.assert_called_once_with("raw_response")
        self.assertEqual(result, "normalized")


class WarnUnknownModelTests(unittest.TestCase):
    """Test warn_if_unknown_model is called from get_llm()."""

    @patch("tradingagents.llm_clients.google_client.NormalizedChatGoogleGenerativeAI")
    def test_unknown_model_triggers_warning(self, mock_chat):
        from tradingagents.llm_clients.google_client import GoogleClient

        client = GoogleClient("totally-fake-model", api_key="test")
        with self.assertWarns(RuntimeWarning) as ctx:
            client.get_llm()
        self.assertIn("not in the known model list", str(ctx.warning).lower())


if __name__ == "__main__":
    unittest.main()

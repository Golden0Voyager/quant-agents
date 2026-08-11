import os
import unittest
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from tradingagents.llm_clients.openai_client import (
    DeepSeekChatOpenAI,
    MinimaxChatOpenAI,
    NormalizedChatOpenAI,
    OpenAIClient,
    _input_to_messages,
    _is_native_openai_base_url,
    _resolve_provider_base_url,
)
from tradingagents.llm_clients.retry_utils import RetryConfig


@pytest.mark.unit
class ResolveProviderBaseUrlTests(unittest.TestCase):
    @patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://remote:11434/v1"}, clear=True)
    def test_ollama_uses_env_var(self):
        url = _resolve_provider_base_url("ollama")
        self.assertEqual(url, "http://remote:11434/v1")

    def test_ollama_falls_back_to_default(self):
        url = _resolve_provider_base_url("ollama")
        self.assertEqual(url, "http://localhost:11434/v1")

    def test_ollama_empty_env_var_falls_back(self):
        """OLLAMA_BASE_URL set to empty string -> fallback to default."""
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": ""}, clear=True):
            url = _resolve_provider_base_url("ollama")
        self.assertEqual(url, "http://localhost:11434/v1")

    def test_returns_none_for_unknown_provider(self):
        url = _resolve_provider_base_url("nonexistent")
        self.assertIsNone(url)


@pytest.mark.unit
class OpenAIClientGetLlmTests(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_creates_chat_openai_with_base_url(self, mock_chat):
        client = OpenAIClient("gpt-4", provider="openai")
        client.get_llm()
        mock_chat.assert_called_once()
        args, kwargs = mock_chat.call_args
        self.assertEqual(kwargs["model"], "gpt-4")
        self.assertTrue(kwargs.get("use_responses_api"))

    @patch.dict(os.environ, {"DEEPSEEK_API_KEY": "ds-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.DeepSeekChatOpenAI")
    def test_deepseek_uses_deepseek_chat(self, mock_chat):
        client = OpenAIClient("deepseek-v4-flash", provider="deepseek")
        client.get_llm()
        mock_chat.assert_called_once()

    @patch.dict(os.environ, {"MINIMAX_API_KEY": "mm-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.MinimaxChatOpenAI")
    def test_minimax_uses_minimax_chat(self, mock_chat):
        client = OpenAIClient("MiniMax-M2.7", provider="minimax")
        client.get_llm()
        mock_chat.assert_called_once()

    @patch.dict(os.environ, {"SENSENOVA_API_KEY": "ss-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.DeepSeekChatOpenAI")
    def test_sensenova_reasoning_model(self, mock_chat):
        client = OpenAIClient("deepseek-v4-flash", provider="sensenova")
        client.get_llm()
        mock_chat.assert_called_once()

    @patch.dict(os.environ, {"SENSENOVA_API_KEY": "ss-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_sensenova_non_reasoning_model(self, mock_chat):
        client = OpenAIClient("sensenova-6.8-flash-lite", provider="sensenova")
        client.get_llm()
        mock_chat.assert_called_once()

    @patch.dict(os.environ, {"KIMI_CODING_API_KEY": "kimi-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_kimi_sets_user_agent(self, mock_chat):
        client = OpenAIClient("kimi-k2.6", provider="kimi")
        client.get_llm()
        _, kwargs = mock_chat.call_args
        self.assertEqual(
            kwargs["default_headers"]["user-agent"], "KimiCLI/1.8.0"
        )

    def test_raises_on_empty_model(self):
        client = OpenAIClient("", provider="openai")
        with self.assertRaises(ValueError):
            client.get_llm()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_passes_through_user_kwargs(self, mock_chat):
        """Line 307: passthrough kwargs are forwarded to ChatOpenAI."""
        client = OpenAIClient("gpt-4", provider="openai", timeout=30)
        client.get_llm()
        _, kwargs = mock_chat.call_args
        self.assertEqual(kwargs.get("timeout"), 30)

    @patch.dict(os.environ, {"SENSENOVA_API_KEY": "ss-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.DeepSeekChatOpenAI")
    def test_attaches_shared_rate_limiter(self, mock_chat):
        """requests_per_minute attaches the process-wide shared limiter."""
        from tradingagents.llm_clients.rate_limit import (
            get_shared_rate_limiter,
            reset_rate_limiters,
        )

        reset_rate_limiters()
        self.addCleanup(reset_rate_limiters)
        client = OpenAIClient(
            "deepseek-v4-flash", provider="sensenova", requests_per_minute=15
        )
        client.get_llm()
        _, kwargs = mock_chat.call_args
        limiter = kwargs.get("rate_limiter")
        self.assertIsNotNone(limiter)
        # The registry hands back the same shared instance for this provider.
        self.assertIs(limiter, get_shared_rate_limiter("sensenova", 15))
        self.assertAlmostEqual(limiter.requests_per_second, 15 / 60)
        # The raw rpm scalar must not leak through to the ChatOpenAI kwargs.
        self.assertNotIn("requests_per_minute", kwargs)

    @patch.dict(os.environ, {"SENSENOVA_API_KEY": "ss-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_no_rate_limiter_without_rpm(self, mock_chat):
        """Without requests_per_minute, no rate_limiter is attached."""
        client = OpenAIClient("sensenova-6.8-flash-lite", provider="sensenova")
        client.get_llm()
        _, kwargs = mock_chat.call_args
        self.assertNotIn("rate_limiter", kwargs)

    @patch.dict(os.environ, {"SENSENOVA_API_KEY": "ss-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_invalid_requests_per_minute_falls_back_to_zero(self, mock_chat):
        """Lines 322-329: invalid requests_per_minute triggers the exception
        handler, logs a warning, and sets rpm=0 (no rate limiter attached)."""
        import logging
        with self.assertLogs("tradingagents.llm_clients.openai_client", level=logging.WARNING) as cm:
            client = OpenAIClient(
                "sensenova-6.8-flash-lite",
                provider="sensenova",
                requests_per_minute="not-a-number",
            )
            client.get_llm()
        self.assertTrue(any("Ignoring invalid" in msg for msg in cm.output))
        self.assertTrue(any("sensenova" in msg for msg in cm.output))
        _, kwargs = mock_chat.call_args
        self.assertNotIn("rate_limiter", kwargs)


@pytest.mark.unit
class NormalizedChatOpenAITests(unittest.TestCase):
    def test_with_structured_output_raises_for_none_method(self):
        with patch(
            "tradingagents.llm_clients.openai_client.get_capabilities"
        ) as mock_caps:
            caps = MagicMock()
            caps.preferred_structured_method = "none"
            mock_caps.return_value = caps
            client = NormalizedChatOpenAI(model="test-model")
            with self.assertRaises(NotImplementedError):
                client.with_structured_output(dict)

    def test_with_structured_output_suppresses_tool_choice(self):
        """Cover line 53: tool_choice is set to None for models that
        don't support it."""
        with patch(
            "tradingagents.llm_clients.openai_client.get_capabilities"
        ) as mock_caps, patch(
            "langchain_openai.ChatOpenAI.with_structured_output",
            return_value="mock_result",
        ) as mock_super:
            caps = MagicMock()
            caps.preferred_structured_method = "function_calling"
            caps.supports_tool_choice = False
            mock_caps.return_value = caps
            client = NormalizedChatOpenAI(model="test-model")
            result = client.with_structured_output(dict)
            self.assertEqual(result, "mock_result")
            # Verify super().with_structured_output was called with
            # tool_choice=None in kwargs.
            _, so_kwargs = mock_super.call_args
            self.assertIn("tool_choice", so_kwargs)
            self.assertIsNone(so_kwargs["tool_choice"])


# =========================================================================
# Edge-case tests merged from test_remaining_coverage.py
# =========================================================================


@pytest.mark.unit
class NormalizedChatOpenAIInvokeTests(unittest.TestCase):
    """Line 33: invoke normalizes content."""

    def test_invoke_normalizes_content(self):
        client = NormalizedChatOpenAI(model="gpt-5.4")
        raw_msg = MagicMock(content="raw")
        with (
            patch.object(NormalizedChatOpenAI, "invoke", wraps=client.invoke),
            patch("langchain_openai.ChatOpenAI.invoke", return_value=raw_msg),
            patch("tradingagents.llm_clients.openai_client.normalize_content",
                  return_value="normalized"),
        ):
            result = client.invoke("input")
        self.assertEqual(result, "normalized")


@pytest.mark.unit
class DeepSeekPayloadEdgeCases(unittest.TestCase):
    """DeepSeekChatOpenAI._get_request_payload: lines 106, 110-111, 119-120."""

    def setUp(self):
        self.client = DeepSeekChatOpenAI(model="deepseek-v4-flash")

    def test_skips_when_already_has_reasoning_content(self):
        """Line 106: message already has reasoning_content -> skip."""
        mock_payload = {
            "messages": [{"role": "assistant", "content": "ok", "reasoning_content": "existing"}]
        }
        mock_msg = AIMessage(content="ok")
        mock_msg.id = "msg-1"
        with patch.object(NormalizedChatOpenAI, "_get_request_payload", return_value=mock_payload), \
             patch("tradingagents.llm_clients.openai_client._input_to_messages", return_value=[mock_msg]):
            result = self.client._get_request_payload("test")
        self.assertEqual(result["messages"][0]["reasoning_content"], "existing")

    def test_uses_sidecar_cache(self):
        """Lines 110-111: cache hit via message id."""
        self.client._reasoning_cache["msg-42"] = "cached thinking"
        mock_payload = {"messages": [{"role": "assistant", "content": "ok"}]}
        mock_msg = AIMessage(content="ok")
        mock_msg.id = "msg-42"
        with patch.object(NormalizedChatOpenAI, "_get_request_payload", return_value=mock_payload), \
             patch("tradingagents.llm_clients.openai_client._input_to_messages", return_value=[mock_msg]):
            result = self.client._get_request_payload("test")
        self.assertEqual(result["messages"][0]["reasoning_content"], "cached thinking")

    def test_tool_calls_fallback(self):
        """Lines 119-120: assistant with tool_calls gets placeholder."""
        self.client._reasoning_cache.clear()
        mock_payload = {
            "messages": [{"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]}]
        }
        mock_msg = AIMessage(content="", tool_calls=[{"id": "call_1", "name": "test", "args": {}}])
        mock_msg.id = None
        with patch.object(NormalizedChatOpenAI, "_get_request_payload", return_value=mock_payload), \
             patch("tradingagents.llm_clients.openai_client._input_to_messages", return_value=[mock_msg]):
            result = self.client._get_request_payload("test")
        self.assertEqual(result["messages"][0]["reasoning_content"], "...")

    def test_non_aimessage_skipped(self):
        """Non-AIMessage in the input is skipped (continue)."""
        mock_payload = {"messages": [{"role": "user", "content": "hello"}]}
        mock_msg = MagicMock()  # not an AIMessage
        with patch.object(NormalizedChatOpenAI, "_get_request_payload", return_value=mock_payload), \
             patch("tradingagents.llm_clients.openai_client._input_to_messages", return_value=[mock_msg]):
            result = self.client._get_request_payload("test")
        # The message dict should have no reasoning_content added.
        self.assertNotIn("reasoning_content", result["messages"][0])

    def test_unknown_message_no_match_any_branch(self):
        """AIMessage with no id, no additional_kwargs reasoning, and no
        tool_calls — none of the three branches match, payload unchanged."""
        self.client._reasoning_cache.clear()
        mock_payload = {"messages": [{"role": "assistant", "content": "thinking..."}]}
        mock_msg = AIMessage(content="thinking...")
        mock_msg.id = None  # no id -> cache miss
        mock_msg.additional_kwargs = {}  # no reasoning_content
        with patch.object(NormalizedChatOpenAI, "_get_request_payload", return_value=mock_payload), \
             patch("tradingagents.llm_clients.openai_client._input_to_messages", return_value=[mock_msg]):
            result = self.client._get_request_payload("test")
        # No reasoning_content added since none of the branches fired.
        self.assertNotIn("reasoning_content", result["messages"][0])

    def test_additional_kwargs_reasoning_content(self):
        """Lines 122-123: when the cache misses but AIMessage has
        reasoning_content in additional_kwargs, it is forwarded to the
        outgoing message dict."""
        self.client._reasoning_cache.clear()
        mock_payload = {"messages": [{"role": "assistant", "content": "thinking..."}]}
        mock_msg = AIMessage(content="thinking...")
        mock_msg.id = None  # no id -> cache miss
        mock_msg.additional_kwargs = {"reasoning_content": "deep thinking"}
        with patch.object(NormalizedChatOpenAI, "_get_request_payload", return_value=mock_payload), \
             patch("tradingagents.llm_clients.openai_client._input_to_messages", return_value=[mock_msg]):
            result = self.client._get_request_payload("test")
        self.assertEqual(result["messages"][0]["reasoning_content"], "deep thinking")


@pytest.mark.unit
class DeepSeekCreateChatResultTests(unittest.TestCase):
    """Lines 140-144: cache eviction and edge cases when exceeding max size."""

    def setUp(self):
        self.client = DeepSeekChatOpenAI(model="deepseek-v4-flash")

    def test_cache_eviction(self):
        """Cache eviction when exceeding max size."""
        self.client._REASONING_CACHE_MAX = 2
        self.client._reasoning_cache = {"old-1": "old", "old-2": "old"}

        mock_chat_result = MagicMock(spec=["generations"])
        mock_gen = MagicMock()
        mock_gen.message.additional_kwargs = {}
        mock_gen.message.id = "new-id"
        mock_chat_result.generations = [mock_gen]

        response = {"choices": [{"message": {"reasoning_content": "new thinking"}}]}

        with patch.object(NormalizedChatOpenAI, "_create_chat_result", return_value=mock_chat_result):
            self.client._create_chat_result(response)

        self.assertIn("new-id", self.client._reasoning_cache)
        self.assertEqual(self.client._reasoning_cache["new-id"], "new thinking")
        self.assertEqual(len(self.client._reasoning_cache), 2)

    def test_no_reasoning_content_in_response(self):
        """Response without reasoning_content does not crash and sets nothing."""
        self.client._reasoning_cache.clear()
        mock_chat_result = MagicMock(spec=["generations"])
        mock_gen = MagicMock()
        mock_gen.message.additional_kwargs = {}
        mock_gen.message.id = "msg-1"
        mock_chat_result.generations = [mock_gen]

        response = {"choices": [{"message": {}}]}  # no reasoning_content

        with patch.object(NormalizedChatOpenAI, "_create_chat_result", return_value=mock_chat_result):
            self.client._create_chat_result(response)

        # additional_kwargs should still be empty (no reasoning injected)
        self.assertEqual(mock_gen.message.additional_kwargs, {})
        # cache should be empty (no reasoning to store)
        self.assertEqual(len(self.client._reasoning_cache), 0)

    def test_message_without_id_still_sets_additional_kwargs(self):
        """Message without an id attribute sets additional_kwargs but not cache."""
        self.client._reasoning_cache.clear()
        mock_chat_result = MagicMock(spec=["generations"])
        mock_gen = MagicMock()
        mock_gen.message.additional_kwargs = {}
        mock_gen.message.id = None  # no id attribute
        mock_chat_result.generations = [mock_gen]

        response = {"choices": [{"message": {"reasoning_content": "thinking..."}}]}

        with patch.object(NormalizedChatOpenAI, "_create_chat_result", return_value=mock_chat_result):
            self.client._create_chat_result(response)

        # additional_kwargs should be set even without a message id.
        self.assertIn("reasoning_content", mock_gen.message.additional_kwargs)
        self.assertEqual(mock_gen.message.additional_kwargs["reasoning_content"], "thinking...")
        # cache should NOT contain an entry for None id.
        self.assertNotIn(None, self.client._reasoning_cache)

    def test_empty_choices_no_crash(self):
        """Response with empty choices list does not crash."""
        self.client._reasoning_cache.clear()
        mock_chat_result = MagicMock(spec=["generations"])
        mock_chat_result.generations = []

        response = {"choices": []}

        with patch.object(NormalizedChatOpenAI, "_create_chat_result", return_value=mock_chat_result):
            # Should not raise any exception.
            self.client._create_chat_result(response)

    def test_response_as_non_dict_object_with_choices(self):
        """Response that is not a dict but has model_dump with choices."""
        self.client._reasoning_cache.clear()
        mock_chat_result = MagicMock(spec=["generations"])
        mock_gen = MagicMock()
        mock_gen.message.additional_kwargs = {}
        mock_gen.message.id = "msg-1"
        mock_chat_result.generations = [mock_gen]

        # Simulate a response object that has model_dump (not a raw dict).
        class FakeResponse:
            def model_dump(self, **kwargs):
                return {"choices": [{"message": {"reasoning_content": "via model_dump"}}]}

        with patch.object(NormalizedChatOpenAI, "_create_chat_result", return_value=mock_chat_result):
            self.client._create_chat_result(FakeResponse())

        self.assertIn("reasoning_content", mock_gen.message.additional_kwargs)
        self.assertEqual(
            mock_gen.message.additional_kwargs["reasoning_content"], "via model_dump",
        )
        self.assertIn("msg-1", self.client._reasoning_cache)


@pytest.mark.unit
class OpenAIClientGetLLMEdgeCases(unittest.TestCase):
    """Lines 276, 278, 282-283: ollama api_key, base_url fallback, kimi user-agent."""

    @patch.dict(os.environ, {}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_ollama_uses_literal_api_key(self, mock_chat):
        """Line 276: ollama gets api_key='ollama' when env is empty."""
        client = OpenAIClient("qwen3:latest", provider="ollama")
        client.get_llm()
        _, kwargs = mock_chat.call_args
        self.assertEqual(kwargs["api_key"], "ollama")

    @patch.dict(os.environ, {}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_custom_base_url_for_unknown_provider(self, mock_chat):
        """Line 278: unknown provider with explicit base_url."""
        client = OpenAIClient("custom-model", provider="unknown_provider",
                              base_url="https://proxy.example.com/v1")
        client.get_llm()
        _, kwargs = mock_chat.call_args
        self.assertEqual(kwargs["base_url"], "https://proxy.example.com/v1")

    @patch.dict(os.environ, {"KIMI_CODING_API_KEY": "kk-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_kimi_sets_user_agent(self, mock_chat):
        """Lines 282-283: kimi provider sets custom user-agent."""
        client = OpenAIClient("kimi-k2.6", provider="kimi")
        client.get_llm()
        _, kwargs = mock_chat.call_args
        self.assertEqual(kwargs["default_headers"]["user-agent"], "KimiCLI/1.8.0")

    def test_raises_on_empty_model(self):
        client = OpenAIClient("", provider="openai")
        with self.assertRaises(ValueError):
            client.get_llm()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_openai_with_non_native_base_url(self, mock_chat):
        """openai provider with custom proxy base_url -> no use_responses_api."""
        client = OpenAIClient(
            "gpt-4", provider="openai",
            base_url="https://myproxy.example.com/v1",
        )
        client.get_llm()
        _, kwargs = mock_chat.call_args
        self.assertEqual(kwargs["base_url"], "https://myproxy.example.com/v1")
        # use_responses_api must NOT be set for non-native URLs.
        self.assertNotIn("use_responses_api", kwargs)

    @patch.dict(os.environ, {}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_unknown_provider_without_base_url(self, mock_chat):
        """Unknown provider without explicit base_url -> no base_url set."""
        client = OpenAIClient("custom-model", provider="nonexistent")
        client.get_llm()
        _, kwargs = mock_chat.call_args
        self.assertEqual(kwargs["model"], "custom-model")
        # base_url should not be in kwargs for unknown providers.
        self.assertNotIn("base_url", kwargs)

    @patch.dict(os.environ, {"MIMO_API_KEY": "mimo-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.DeepSeekChatOpenAI")
    def test_mimo_provider_with_reasoning_model(self, mock_chat):
        """mimo provider with reasoning model uses DeepSeekChatOpenAI."""
        client = OpenAIClient("mimo-v2.5", provider="mimo")
        client.get_llm()
        mock_chat.assert_called_once()

    @patch.dict(os.environ, {"MIMO_API_KEY": "mimo-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.DeepSeekChatOpenAI")
    def test_mimo_provider_always_uses_deepseek_chat(self, mock_chat):
        """mimo provider always uses DeepSeekChatOpenAI (regardless of model)."""
        client = OpenAIClient("mimo-mini", provider="mimo")
        client.get_llm()
        mock_chat.assert_called_once()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_no_retry_config_in_kwargs(self, mock_chat):
        """get_llm without retry_config in kwargs does not add it to llm_kwargs."""
        client = OpenAIClient("gpt-4", provider="openai")
        client.get_llm()
        _, kwargs = mock_chat.call_args
        # retry_config is consumed by OpenAIClient and bound to the llm
        # as _retry_config; it is NOT forwarded to ChatOpenAI.
        self.assertNotIn("retry_config", kwargs)


@pytest.mark.unit
class MinimaxPayloadEdgeCases(unittest.TestCase):
    """MinimaxChatOpenAI._get_request_payload edge cases."""

    def test_non_reasoning_model_does_not_add_reasoning_split(self):
        """Non-reasoning MiniMax model: extra_body should not have reasoning_split."""
        client = MinimaxChatOpenAI(model="MiniMax-Text-01")
        mock_payload = {"messages": [{"role": "user", "content": "hi"}]}
        with patch.object(NormalizedChatOpenAI, "_get_request_payload", return_value=mock_payload), \
             patch("tradingagents.llm_clients.openai_client.get_capabilities") as mock_caps:
            caps = MagicMock()
            caps.requires_reasoning_split = False
            mock_caps.return_value = caps
            result = client._get_request_payload("test")
        # No extra_body should be added for non-reasoning models.
        self.assertNotIn("extra_body", result)

    def test_reasoning_model_adds_reasoning_split(self):
        """Reasoning MiniMax model: extra_body should have reasoning_split=True."""
        client = MinimaxChatOpenAI(model="MiniMax-M2.7")
        mock_payload = {"messages": [{"role": "user", "content": "hi"}]}
        with patch.object(NormalizedChatOpenAI, "_get_request_payload", return_value=mock_payload), \
             patch("tradingagents.llm_clients.openai_client.get_capabilities") as mock_caps:
            caps = MagicMock()
            caps.requires_reasoning_split = True
            mock_caps.return_value = caps
            result = client._get_request_payload("test")
        self.assertIn("extra_body", result)
        self.assertTrue(result["extra_body"]["reasoning_split"])

    def test_existing_reasoning_split_not_overridden(self):
        """If extra_body.reasoning_split is already set, it's not overridden."""
        client = MinimaxChatOpenAI(model="MiniMax-M2.7")
        mock_payload = {
            "messages": [{"role": "user", "content": "hi"}],
            "extra_body": {"reasoning_split": False, "other_param": 42},
        }
        with patch.object(NormalizedChatOpenAI, "_get_request_payload", return_value=mock_payload), \
             patch("tradingagents.llm_clients.openai_client.get_capabilities") as mock_caps:
            caps = MagicMock()
            caps.requires_reasoning_split = True
            mock_caps.return_value = caps
            result = client._get_request_payload("test")
        # reasoning_split should remain False (setdefault does not override).
        self.assertFalse(result["extra_body"]["reasoning_split"])
        # Other params in extra_body must be preserved.
        self.assertEqual(result["extra_body"]["other_param"], 42)


@pytest.mark.unit
class OpenAIClientRetryTests(unittest.TestCase):
    """Retry/backoff behavior on NormalizedChatOpenAI and OpenAIClient."""

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_invoke_retries_on_rate_limit_then_succeeds(self, mock_sleep):
        client = NormalizedChatOpenAI(model="gpt-test")
        client._retry_config = RetryConfig(max_retries=2, base_delay=0.05)
        raw_msg = MagicMock(content="ok")

        side_effect = [
            pytest.importorskip("openai").RateLimitError(
                "rate limit",
                response=MagicMock(status_code=429),
                body={"error": {"message": "rate limit"}},
            ),
            raw_msg,
        ]

        with patch("langchain_openai.ChatOpenAI.invoke", side_effect=side_effect):
            result = client.invoke("input")

        self.assertEqual(result.content, "ok")
        mock_sleep.assert_called_once_with(0.05)

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_invoke_does_not_retry_non_transient_errors(self, mock_sleep):
        client = NormalizedChatOpenAI(model="gpt-test")
        client._retry_config = RetryConfig(max_retries=2, base_delay=0.05)

        with (
            patch("langchain_openai.ChatOpenAI.invoke", side_effect=ValueError("bad")),
            self.assertRaises(ValueError),
        ):
            client.invoke("input")

        mock_sleep.assert_not_called()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_get_llm_binds_retry_config(self, mock_chat):
        retry_config = RetryConfig(max_retries=5, base_delay=1.0)
        client = OpenAIClient("gpt-4", provider="openai", retry_config=retry_config)
        llm = client.get_llm()
        self.assertEqual(llm._retry_config, retry_config)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True)
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_get_llm_binds_retry_config_from_dict(self, mock_chat):
        client = OpenAIClient(
            "gpt-4", provider="openai", retry_config={"max_retries": 4, "base_delay": 0.5}
        )
        llm = client.get_llm()
        self.assertEqual(llm._retry_config.max_retries, 4)
        self.assertEqual(llm._retry_config.base_delay, 0.5)

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_deepseek_inherits_retry(self, mock_sleep):
        client = DeepSeekChatOpenAI(model="deepseek-v4-flash")
        client._retry_config = RetryConfig(max_retries=1, base_delay=0.05)
        raw_msg = MagicMock(content="ok")

        openai = pytest.importorskip("openai")
        side_effect = [
            openai.RateLimitError(
                "rate limit",
                response=MagicMock(status_code=429),
                body={"error": {"message": "rate limit"}},
            ),
            raw_msg,
        ]

        with patch("langchain_openai.ChatOpenAI.invoke", side_effect=side_effect):
            result = client.invoke("input")

        self.assertEqual(result.content, "ok")
        mock_sleep.assert_called_once_with(0.05)

    @patch("tradingagents.llm_clients.retry_utils.time.sleep")
    def test_structured_output_path_retries(self, mock_sleep):
        """with_structured_output binding calls the wrapped invoke, so retries apply."""
        client = NormalizedChatOpenAI(model="gpt-test")
        client._retry_config = RetryConfig(max_retries=1, base_delay=0.05)

        openai = pytest.importorskip("openai")
        raw_msg = AIMessage(content='{"answer": 42}')
        side_effect = [
            openai.RateLimitError(
                "rate limit",
                response=MagicMock(status_code=429),
                body={"error": {"message": "rate limit"}},
            ),
            raw_msg,
        ]

        with patch(
            "tradingagents.llm_clients.openai_client.get_capabilities"
        ) as mock_caps:
            caps = MagicMock()
            caps.preferred_structured_method = "json_mode"
            caps.supports_tool_choice = True
            mock_caps.return_value = caps

            with patch("langchain_openai.ChatOpenAI.invoke", side_effect=side_effect):
                structured = client.with_structured_output(dict)
                result = structured.invoke("input")

        self.assertEqual(result, {"answer": 42})
        mock_sleep.assert_called_once_with(0.05)


@pytest.mark.unit
class InputToMessagesTests(unittest.TestCase):
    """Lines 65-68: _input_to_messages - not patched, tests real function."""

    def test_list_passthrough(self):
        """List input returns as-is."""
        result = _input_to_messages(["hello", "world"])
        self.assertEqual(result, ["hello", "world"])

    def test_to_messages_called(self):
        """Object with to_messages() method is called."""
        obj = MagicMock()
        obj.to_messages.return_value = ["msg"]
        result = _input_to_messages(obj)
        self.assertEqual(result, ["msg"])
        obj.to_messages.assert_called_once()

    def test_fallback_empty_list(self):
        """Unknown input returns empty list."""
        result = _input_to_messages(123)
        self.assertEqual(result, [])


@pytest.mark.unit
class IsNativeOpenaiBaseUrlTests(unittest.TestCase):
    """Lines 260-263: _is_native_openai_base_url for non-None base_urls."""

    def test_native_without_scheme(self):
        """base_url without :// that points to api.openai.com."""
        self.assertTrue(_is_native_openai_base_url("api.openai.com"))

    def test_native_with_scheme(self):
        """base_url with https:// pointing to api.openai.com."""
        self.assertTrue(_is_native_openai_base_url("https://api.openai.com/v1"))

    def test_non_native_proxy(self):
        """Custom proxy URL returns False."""
        self.assertFalse(_is_native_openai_base_url("https://myproxy.example.com/v1"))

    def test_non_native_no_scheme(self):
        """Custom host without :// returns False."""
        self.assertFalse(_is_native_openai_base_url("myproxy.local"))

    def test_none_returns_true(self):
        """None base_url returns True (native default)."""
        self.assertTrue(_is_native_openai_base_url(None))

    def test_empty_returns_true(self):
        """Empty base_url returns True."""
        self.assertTrue(_is_native_openai_base_url(""))

    def test_subdomain_of_openai_dot_com(self):
        """URLs under *.openai.com are considered native."""
        self.assertTrue(_is_native_openai_base_url("https://custom.openai.com"))
        self.assertTrue(_is_native_openai_base_url("api.openai.com"))

    def test_non_openai_domain_with_openai_in_name(self):
        """Domain containing 'openai' in path but not as host is not native."""
        self.assertFalse(_is_native_openai_base_url("https://myproxy.com/openai"))


@pytest.mark.unit
class OpenAIClientMissingApiKeyTests(unittest.TestCase):
    """Line 307: get_llm raises ValueError when API key env var is unset."""

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_api_key_raises_value_error(self):
        """Provider in _PROVIDER_BASE_URL but key env var is not set."""
        client = OpenAIClient("grok-4", provider="xai")
        with self.assertRaises(ValueError) as ctx:
            client.get_llm()
        self.assertIn("API key", str(ctx.exception))
        self.assertIn("XAI_API_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from unittest.mock import patch

import pytest

from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.model_catalog import get_model_options
from tradingagents.llm_clients.openai_client import _PROVIDER_BASE_URL, _resolve_provider_base_url


@pytest.mark.unit
class OpenaiCompatibleListTests(unittest.TestCase):
    def test_includes_expected_providers(self):
        from tradingagents.llm_clients.factory import _OPENAI_COMPATIBLE

        self.assertIn("openai", _OPENAI_COMPATIBLE)
        self.assertIn("deepseek", _OPENAI_COMPATIBLE)

    def test_anthropic_not_in_openai_compatible(self):
        from tradingagents.llm_clients.factory import _OPENAI_COMPATIBLE

        self.assertNotIn("anthropic", _OPENAI_COMPATIBLE)

    def test_google_not_in_openai_compatible(self):
        from tradingagents.llm_clients.factory import _OPENAI_COMPATIBLE

        self.assertNotIn("google", _OPENAI_COMPATIBLE)

    def test_azure_not_in_openai_compatible(self):
        from tradingagents.llm_clients.factory import _OPENAI_COMPATIBLE

        self.assertNotIn("azure", _OPENAI_COMPATIBLE)


@pytest.mark.unit
class CreateLLMClientTests(unittest.TestCase):
    def test_unsupported_provider_raises(self):
        from tradingagents.llm_clients.factory import create_llm_client

        with self.assertRaises(ValueError):
            create_llm_client("nonexistent", "test")


@pytest.mark.unit
class CreateLLMClientIntegrationTests(unittest.TestCase):
    def test_openai_compatible_returns_base_llm_client(self):
        from tradingagents.llm_clients.base_client import BaseLLMClient
        from tradingagents.llm_clients.factory import create_llm_client

        for provider in ("openai", "deepseek", "qwen", "sensenova"):
            with self.subTest(provider=provider):
                client = create_llm_client(provider, "test-model")
                self.assertIsInstance(client, BaseLLMClient)

    def test_anthropic_returns_base_llm_client(self):
        from tradingagents.llm_clients.base_client import BaseLLMClient
        from tradingagents.llm_clients.factory import create_llm_client

        client = create_llm_client("anthropic", "claude-haiku-4-5")
        self.assertIsInstance(client, BaseLLMClient)

    def test_google_returns_base_llm_client(self):
        from tradingagents.llm_clients.base_client import BaseLLMClient
        from tradingagents.llm_clients.factory import create_llm_client

        client = create_llm_client("google", "gemini-2.5-flash")
        self.assertIsInstance(client, BaseLLMClient)

    def test_azure_returns_base_llm_client(self):
        from tradingagents.llm_clients.base_client import BaseLLMClient
        from tradingagents.llm_clients.factory import create_llm_client

        client = create_llm_client("azure", "gpt-4")
        self.assertIsInstance(client, BaseLLMClient)


# =========================================================================
# Provider registration invariants -- merged from test_llm_provider_registration.py
# =========================================================================


@pytest.mark.unit
class ProviderMembershipTests(unittest.TestCase):
    def test_factory_recognises_agnes(self):
        from tradingagents.llm_clients.factory import _OPENAI_COMPATIBLE
        self.assertIn("agnes", _OPENAI_COMPATIBLE)

    def test_factory_recognises_modelscope(self):
        from tradingagents.llm_clients.factory import _OPENAI_COMPATIBLE
        self.assertIn("modelscope", _OPENAI_COMPATIBLE)

    def test_factory_recognises_nvidia(self):
        from tradingagents.llm_clients.factory import _OPENAI_COMPATIBLE
        self.assertIn("nvidia", _OPENAI_COMPATIBLE)


@pytest.mark.unit
class ProviderBaseUrlPinningTests(unittest.TestCase):
    EXPECTED = {
        "agnes": "https://apihub.agnes-ai.com/v1",
        "modelscope": "https://api-inference.modelscope.cn/v1",
        "nvidia": "https://integrate.api.nvidia.com/v1",
    }

    def test_base_urls_pinned_in_provider_dict(self):
        for provider, expected in self.EXPECTED.items():
            with self.subTest(provider=provider):
                self.assertEqual(_PROVIDER_BASE_URL[provider], expected)

    def test_resolver_returns_dict_value(self):
        for provider, expected in self.EXPECTED.items():
            with self.subTest(provider=provider):
                self.assertEqual(_resolve_provider_base_url(provider), expected)

    def test_resolver_unknown_provider_returns_none(self):
        self.assertIsNone(_resolve_provider_base_url("not-a-real-provider"))


@pytest.mark.unit
class ProviderApiKeyEnvMappingTests(unittest.TestCase):
    EXPECTED = {
        "agnes": "AGNES_API_KEY",
        "modelscope": "MODELSCOPE_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
    }

    def test_env_var_for_each_provider(self):
        for provider, expected in self.EXPECTED.items():
            with self.subTest(provider=provider):
                self.assertEqual(get_api_key_env(provider), expected)

    def test_env_var_case_insensitive(self):
        self.assertEqual(get_api_key_env("Agnes"), "AGNES_API_KEY")
        self.assertEqual(get_api_key_env("MODELSCOPE"), "MODELSCOPE_API_KEY")

    def test_env_var_nvidia_case_insensitive(self):
        self.assertEqual(get_api_key_env("Nvidia"), "NVIDIA_API_KEY")


@pytest.mark.unit
class ProviderModelCatalogTests(unittest.TestCase):
    def test_agnes_options_listed(self):
        options = get_model_options("agnes", "quick")
        values = [v for _, v in options]
        self.assertIn("agnes-2.0-flash", values)
        self.assertIn("custom", values)

    def test_agnes_deep_options_listed(self):
        options = get_model_options("agnes", "deep")
        values = [v for _, v in options]
        self.assertIn("agnes-2.0-flash", values)

    def test_modelscope_options_listed(self):
        for mode in ("quick", "deep"):
            options = get_model_options("modelscope", mode)
            values = [v for _, v in options]
            self.assertIn("deepseek-ai/DeepSeek-V4-Flash-0731", values)
            self.assertIn("custom", values)

    def test_nvidia_options_listed(self):
        for mode in ("quick", "deep"):
            options = get_model_options("nvidia", mode)
            values = [v for _, v in options]
            self.assertIn("deepseek-ai/deepseek-v4-pro", values)
            self.assertIn("custom", values)

    def test_agnes_label_mentions_context_window(self):
        options = get_model_options("agnes", "quick")
        labels = [label for label, _ in options]
        self.assertTrue(any("256K" in label for label in labels), labels)


@pytest.mark.unit
class ProviderEndToEndTests(unittest.TestCase):
    """``create_llm_client(...).get_llm()`` must wire up the right base_url
    and api_key for each non-default provider.
    """

    def _build(self, provider, model, env_var, key, base_url=None):
        from tradingagents.llm_clients import openai_client as oc_mod
        from tradingagents.llm_clients.factory import create_llm_client
        from tradingagents.llm_clients.openai_client import OpenAIClient

        kwargs = {"provider": provider, "model": model}
        if base_url:
            kwargs["base_url"] = base_url
        # ``create_llm_client`` and ``get_llm()`` must both run inside the
        # patched-env block so OpenAIClient picks up the test's key, not
        # whatever the dev's shell exported (#990-style isolation).
        with (
            patch.dict(os.environ, {env_var: key}),
            patch.object(oc_mod, "NormalizedChatOpenAI", autospec=True) as mock_cls,
        ):
            client = create_llm_client(**kwargs)
            assert isinstance(client, OpenAIClient)
            assert client.provider == provider
            mock_cls.return_value = object()
            client.get_llm()
        return mock_cls.call_args.kwargs

    def test_create_agnes_client(self):
        kwargs = self._build("agnes", "agnes-2.0-flash", "AGNES_API_KEY", "sk-agnes-test")
        self.assertEqual(kwargs["base_url"], "https://apihub.agnes-ai.com/v1")
        self.assertEqual(kwargs["api_key"], "sk-agnes-test")

    def test_create_modelscope_client(self):
        kwargs = self._build("modelscope", "deepseek-ai/DeepSeek-V4-Flash",
                             "MODELSCOPE_API_KEY", "sk-modelscope-test")
        self.assertEqual(kwargs["base_url"], "https://api-inference.modelscope.cn/v1")
        self.assertEqual(kwargs["api_key"], "sk-modelscope-test")

    def test_create_nvidia_client(self):
        kwargs = self._build("nvidia", "deepseek-ai/deepseek-v4-pro",
                             "NVIDIA_API_KEY", "sk-nvidia-test")
        self.assertEqual(kwargs["base_url"], "https://integrate.api.nvidia.com/v1")
        self.assertEqual(kwargs["api_key"], "sk-nvidia-test")

    def test_explicit_base_url_wins_over_provider_default(self):
        proxy = "http://corp-proxy.internal:8080/v1"
        kwargs = self._build("agnes", "agnes-2.0-flash", "AGNES_API_KEY",
                             "sk-agnes-test", base_url=proxy)
        self.assertEqual(kwargs["base_url"], proxy)


@pytest.mark.unit
class ProviderMissingApiKeyTests(unittest.TestCase):
    def test_agnes_missing_api_key_raises(self):
        from tradingagents.llm_clients.factory import create_llm_client

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGNES_API_KEY", None)
            client = create_llm_client(provider="agnes", model="agnes-2.0-flash")
            with pytest.raises(ValueError, match="AGNES_API_KEY"):
                client.get_llm()


if __name__ == "__main__":
    unittest.main()

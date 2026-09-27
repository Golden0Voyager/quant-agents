"""Tests for env-driven CLI behavior (#897, #873) and the provider / key
mapping contract.

The config-layer override (TRADINGAGENTS_* -> DEFAULT_CONFIG) lives in
test_default_config.py. These tests cover the CLI layer: an env-configured
provider/model/language must skip its interactive prompt and use the value,
plus the canonical provider -> API-key-env-var mapping and the
``ensure_api_key`` CLI helper.
"""

import os
import unittest
from unittest import mock
from unittest.mock import patch

import pytest

from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV, get_api_key_env


@pytest.mark.unit
class TestProviderDefaultUrl(unittest.TestCase):
    def test_known_providers_resolve(self):
        from cli.utils import provider_default_url
        self.assertEqual(provider_default_url("openai"), "https://api.openai.com/v1")
        self.assertEqual(provider_default_url("DeepSeek"), "https://api.deepseek.com")
        self.assertIsNone(provider_default_url("google"))  # uses SDK default

    def test_unknown_provider_returns_none(self):
        from cli.utils import provider_default_url
        self.assertIsNone(provider_default_url("not-a-provider"))

    def test_ollama_honors_base_url_env(self):
        from cli.utils import provider_default_url
        with mock.patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://host:1234/v1"}):
            self.assertEqual(provider_default_url("ollama"), "http://host:1234/v1")


@pytest.mark.unit
class TestCliSkipsPromptsFromEnv(unittest.TestCase):
    def test_env_config_skips_llm_prompts(self):
        import cli.main as m

        env = {
            "TRADINGAGENTS_LLM_PROVIDER": "openai",
            "TRADINGAGENTS_DEEP_THINK_LLM": "kimi-k2.5",
            "TRADINGAGENTS_QUICK_THINK_LLM": "deepseek-v4-pro",
            "TRADINGAGENTS_LLM_BACKEND_URL": "https://opencode.ai/zen/go/v1",
            "TRADINGAGENTS_OUTPUT_LANGUAGE": "Japanese",
        }
        fake_cfg = dict(m.DEFAULT_CONFIG)
        fake_cfg.update({
            "llm_provider": "openai",
            "backend_url": "https://opencode.ai/zen/go/v1",
            "quick_think_llm": "deepseek-v4-pro",
            "deep_think_llm": "kimi-k2.5",
            "output_language": "Japanese",
        })

        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(m, "DEFAULT_CONFIG", fake_cfg), \
             mock.patch.object(m, "get_ticker", return_value="AAPL"), \
             mock.patch.object(m, "get_analysis_date", return_value="2026-05-29"), \
             mock.patch.object(m, "select_analysts", return_value=[]), \
             mock.patch.object(m, "select_research_depth", return_value=1), \
             mock.patch("questionary.confirm") as mock_confirm, \
             mock.patch.object(m, "ensure_api_key") as ensure_key, \
             mock.patch.object(m, "select_llm_provider") as prompt_provider, \
             mock.patch.object(m, "ask_output_language") as prompt_lang, \
             mock.patch.object(m, "select_shallow_thinking_agent") as prompt_quick, \
             mock.patch.object(m, "select_deep_thinking_agent") as prompt_deep:
            mock_confirm.return_value.ask.return_value = True
            sel = m.get_user_selections()

        # None of the LLM selection prompts should have been shown.
        prompt_provider.assert_not_called()
        prompt_lang.assert_not_called()
        prompt_quick.assert_not_called()
        prompt_deep.assert_not_called()
        # API key is still verified for the env-configured provider.
        ensure_key.assert_called_once()

        # The env values flow into the returned selections.
        self.assertEqual(sel["llm_provider"], "openai")
        self.assertEqual(sel["backend_url"], "https://opencode.ai/zen/go/v1")
        self.assertEqual(sel["shallow_thinker"], "deepseek-v4-pro")
        self.assertEqual(sel["deep_thinker"], "kimi-k2.5")
        self.assertEqual(sel["output_language"], "Japanese")


# ===========================================================================
# Provider -> env-var mapping contract.
# Merged from tests/test_api_key_env.py — mapping coverage plus the
# ``ensure_api_key`` CLI helper (read-existing / prompt-and-write / no-op
# for ollama / unknown-provider).
# ===========================================================================


@pytest.mark.unit
def test_every_select_llm_provider_choice_has_an_entry():
    expected = {
        "openai", "google", "anthropic", "xai", "deepseek",
        "qwen", "qwen-cn",
        "glm", "glm-cn",
        "minimax", "minimax-cn",
        "openrouter", "azure", "ollama",
    }
    assert expected.issubset(PROVIDER_API_KEY_ENV.keys())


@pytest.mark.unit
@pytest.mark.parametrize(
    "provider,env_var",
    [
        ("openai",     "OPENAI_API_KEY"),
        ("anthropic",  "ANTHROPIC_API_KEY"),
        ("google",     "GOOGLE_API_KEY"),
        ("azure",      "AZURE_OPENAI_API_KEY"),
        ("xai",        "XAI_API_KEY"),
        ("deepseek",   "DEEPSEEK_API_KEY"),
        ("qwen",       "DASHSCOPE_API_KEY"),
        ("qwen-cn",    "DASHSCOPE_CN_API_KEY"),
        ("glm",        "ZHIPU_API_KEY"),
        ("glm-cn",     "ZHIPU_CN_API_KEY"),
        ("minimax",    "MINIMAX_API_KEY"),
        ("minimax-cn", "MINIMAX_CN_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
    ],
)
def test_known_providers_resolve(provider, env_var):
    assert get_api_key_env(provider) == env_var


@pytest.mark.unit
def test_ollama_has_no_key():
    assert get_api_key_env("ollama") is None


@pytest.mark.unit
def test_unknown_provider_returns_none():
    assert get_api_key_env("not-a-real-provider") is None


@pytest.mark.unit
def test_case_insensitive_lookup():
    assert get_api_key_env("OpenAI") == "OPENAI_API_KEY"
    assert get_api_key_env("QWEN-CN") == "DASHSCOPE_CN_API_KEY"


# ---- ensure_api_key behavior ----------------------------------------------


@pytest.fixture
def cli_utils(monkeypatch):
    """Import cli.utils with a fresh environment so module-level state is consistent."""
    import importlib

    import cli.utils as cli_utils_module
    return importlib.reload(cli_utils_module)


@pytest.mark.unit
def test_ensure_api_key_returns_existing(monkeypatch, cli_utils):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-already-set")
    result = cli_utils.ensure_api_key("openai")
    assert result == "sk-already-set"


@pytest.mark.unit
def test_ensure_api_key_no_op_for_ollama(monkeypatch, cli_utils):
    # Even with no env var set, ollama should not prompt and should return None.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch.object(cli_utils, "questionary") as mock_q:
        result = cli_utils.ensure_api_key("ollama")
    assert result is None
    mock_q.password.assert_not_called()


@pytest.mark.unit
def test_ensure_api_key_unknown_provider_no_prompt(monkeypatch, cli_utils):
    with patch.object(cli_utils, "questionary") as mock_q:
        result = cli_utils.ensure_api_key("totally-fake-provider")
    assert result is None
    mock_q.password.assert_not_called()


@pytest.mark.unit
def test_ensure_api_key_prompts_and_writes_to_env(monkeypatch, tmp_path, cli_utils):
    """When key is missing, user-pasted value must be written to .env AND os.environ."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    fake_prompt = type("P", (), {"ask": staticmethod(lambda: "sk-deepseek-test")})()
    with patch.object(cli_utils.questionary, "password", return_value=fake_prompt):
        result = cli_utils.ensure_api_key("deepseek")

    assert result == "sk-deepseek-test"
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-deepseek-test"
    env_file = tmp_path / ".env"
    assert env_file.exists()
    assert "DEEPSEEK_API_KEY" in env_file.read_text()
    assert "sk-deepseek-test" in env_file.read_text()


@pytest.mark.unit
def test_ensure_api_key_user_cancels_returns_none(monkeypatch, tmp_path, cli_utils):
    """Empty prompt response (user cancelled) must not write to .env."""
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    fake_prompt = type("P", (), {"ask": staticmethod(lambda: None)})()
    with patch.object(cli_utils.questionary, "password", return_value=fake_prompt):
        result = cli_utils.ensure_api_key("xai")

    assert result is None
    assert "XAI_API_KEY" not in os.environ
    env_file = tmp_path / ".env"
    if env_file.exists():
        assert "XAI_API_KEY" not in env_file.read_text()


@pytest.mark.unit
def test_ensure_api_key_updates_existing_env_file(monkeypatch, tmp_path, cli_utils):
    """An existing .env with other keys must be preserved on writeback."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-existing\nOTHER=value\n")

    fake_prompt = type("P", (), {"ask": staticmethod(lambda: "sk-openrouter-new")})()
    with patch.object(cli_utils.questionary, "password", return_value=fake_prompt):
        cli_utils.ensure_api_key("openrouter")

    content = env_file.read_text()
    assert "OPENAI_API_KEY" in content and "sk-existing" in content
    assert "OTHER=value" in content
    assert "OPENROUTER_API_KEY" in content and "sk-openrouter-new" in content


if __name__ == "__main__":
    unittest.main()

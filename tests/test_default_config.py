import importlib
import os
import unittest
from unittest.mock import patch

import pytest

import tradingagents.default_config as default_config_module
from tradingagents.default_config import (
    DEFAULT_CONFIG,
    _apply_env_overrides,
    _coerce,
)


@pytest.mark.unit
class CoerceTests(unittest.TestCase):
    def test_bool_true_variants(self):
        for v in ("true", "True", "1", "yes", "on"):
            self.assertTrue(_coerce(v, True))

    def test_bool_false_variants(self):
        for v in ("false", "0", "no", "off"):
            self.assertFalse(_coerce(v, True))

    def test_int_coercion(self):
        self.assertEqual(_coerce("42", 1), 42)

    def test_float_coercion(self):
        self.assertEqual(_coerce("3.14", 1.0), 3.14)

    def test_string_fallback(self):
        self.assertEqual(_coerce("hello", "world"), "hello")


@pytest.mark.unit
class ApplyEnvOverridesTests(unittest.TestCase):
    @patch.dict(os.environ, {"TRADINGAGENTS_LLM_PROVIDER": "openai"})
    def test_overrides_provider(self):
        config = {"llm_provider": "sensenova"}
        _apply_env_overrides(config)
        self.assertEqual(config["llm_provider"], "openai")

    @patch.dict(os.environ, {"TRADINGAGENTS_CHECKPOINT_ENABLED": "true"})
    def test_overrides_bool(self):
        config = {"checkpoint_enabled": False}
        _apply_env_overrides(config)
        self.assertTrue(config["checkpoint_enabled"])

    @patch.dict(os.environ, {"TRADINGAGENTS_TEMPERATURE": "0.7"})
    def test_overrides_float(self):
        config = {"temperature": 0.0}
        _apply_env_overrides(config)
        self.assertEqual(config["temperature"], 0.7)

    @patch.dict(os.environ, {"UNRELATED_VAR": "foo"})
    def test_ignores_unrelated_vars(self):
        config = {"llm_provider": "sensenova"}
        _apply_env_overrides(config)
        self.assertEqual(config["llm_provider"], "sensenova")


@pytest.mark.unit
class DefaultConfigTests(unittest.TestCase):
    def test_has_expected_keys(self):
        expected = {
            "llm_provider", "deep_think_llm", "quick_think_llm",
            "max_debate_rounds", "max_risk_discuss_rounds",
            "checkpoint_enabled", "output_language", "benchmark_map",
        }
        for key in expected:
            self.assertIn(key, DEFAULT_CONFIG)

    def test_defaults_are_sane(self):
        self.assertEqual(DEFAULT_CONFIG["llm_provider"], "sensenova")
        self.assertEqual(DEFAULT_CONFIG["max_debate_rounds"], 1)
        self.assertEqual(DEFAULT_CONFIG["checkpoint_enabled"], False)
        self.assertEqual(DEFAULT_CONFIG["output_language"], "Chinese")

    def test_benchmark_map_has_default(self):
        self.assertIn("", DEFAULT_CONFIG["benchmark_map"])
        self.assertEqual(DEFAULT_CONFIG["benchmark_map"][""], "SPY")


# ===========================================================================
# TRADINGAGENTS_* env-var overlay onto DEFAULT_CONFIG.
# Merged from tests/test_env_overrides.py. These exercise the higher-level
# module-reload surface; CoerceTests / ApplyEnvOverridesTests above poke
# _apply_env_overrides directly with a fake config dict.
# ===========================================================================


def _dc_reload_with_env(monkeypatch, **overrides):
    """Set/clear TRADINGAGENTS_* env vars then reload DEFAULT_CONFIG."""
    from tradingagents.default_config import _ENV_OVERRIDES

    for key in list(_ENV_OVERRIDES):
        monkeypatch.delenv(key, raising=False)
    for key, val in overrides.items():
        monkeypatch.setenv(key, val)
    return importlib.reload(default_config_module)


@pytest.mark.unit
def test_no_env_uses_built_in_defaults(monkeypatch):
    dc = _dc_reload_with_env(monkeypatch)
    assert dc.DEFAULT_CONFIG["llm_provider"] == "sensenova"
    assert dc.DEFAULT_CONFIG["deep_think_llm"] == "deepseek-v4-flash"
    assert dc.DEFAULT_CONFIG["quick_think_llm"] == "sensenova-6.7-flash-lite"
    assert dc.DEFAULT_CONFIG["backend_url"] == "https://token.sensenova.cn/v1"
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 1
    assert dc.DEFAULT_CONFIG["checkpoint_enabled"] is False


@pytest.mark.unit
def test_string_env_overrides(monkeypatch):
    dc = _dc_reload_with_env(
        monkeypatch,
        TRADINGAGENTS_LLM_PROVIDER="google",
        TRADINGAGENTS_DEEP_THINK_LLM="gemini-3-pro-preview",
        TRADINGAGENTS_QUICK_THINK_LLM="gemini-3-flash-preview",
        TRADINGAGENTS_LLM_BACKEND_URL="https://example.invalid/v1",
        TRADINGAGENTS_OUTPUT_LANGUAGE="Chinese",
    )
    assert dc.DEFAULT_CONFIG["llm_provider"] == "google"
    assert dc.DEFAULT_CONFIG["deep_think_llm"] == "gemini-3-pro-preview"
    assert dc.DEFAULT_CONFIG["quick_think_llm"] == "gemini-3-flash-preview"
    assert dc.DEFAULT_CONFIG["backend_url"] == "https://example.invalid/v1"
    assert dc.DEFAULT_CONFIG["output_language"] == "Chinese"


@pytest.mark.unit
def test_int_env_coercion(monkeypatch):
    dc = _dc_reload_with_env(
        monkeypatch,
        TRADINGAGENTS_MAX_DEBATE_ROUNDS="3",
        TRADINGAGENTS_MAX_RISK_ROUNDS="2",
    )
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 3
    assert isinstance(dc.DEFAULT_CONFIG["max_debate_rounds"], int)
    assert dc.DEFAULT_CONFIG["max_risk_discuss_rounds"] == 2
    assert isinstance(dc.DEFAULT_CONFIG["max_risk_discuss_rounds"], int)


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
        ("false", False), ("False", False), ("0", False), ("no", False), ("off", False),
    ],
)
def test_bool_env_coercion(monkeypatch, raw, expected):
    dc = _dc_reload_with_env(monkeypatch, TRADINGAGENTS_CHECKPOINT_ENABLED=raw)
    assert dc.DEFAULT_CONFIG["checkpoint_enabled"] is expected


@pytest.mark.unit
def test_empty_env_value_is_passthrough(monkeypatch):
    """Empty TRADINGAGENTS_* values must not clobber the built-in default."""
    dc = _dc_reload_with_env(
        monkeypatch,
        TRADINGAGENTS_LLM_PROVIDER="",
        TRADINGAGENTS_MAX_DEBATE_ROUNDS="",
    )
    assert dc.DEFAULT_CONFIG["llm_provider"] == "sensenova"
    assert dc.DEFAULT_CONFIG["max_debate_rounds"] == 1


@pytest.mark.unit
def test_invalid_int_env_raises(monkeypatch):
    """Garbage int values should surface a ValueError at import, not silently misconfigure."""
    monkeypatch.setenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "not-a-number")
    with pytest.raises(ValueError):
        importlib.reload(default_config_module)
    # Restore module state for subsequent tests in this process.
    monkeypatch.delenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", raising=False)
    importlib.reload(default_config_module)


@pytest.mark.unit
def test_unknown_env_var_is_ignored(monkeypatch):
    """Env vars outside _ENV_OVERRIDES must not bleed into DEFAULT_CONFIG."""
    dc = _dc_reload_with_env(
        monkeypatch,
        TRADINGAGENTS_NONEXISTENT_KEY="oops",
    )
    assert "nonexistent_key" not in dc.DEFAULT_CONFIG


if __name__ == "__main__":
    unittest.main()

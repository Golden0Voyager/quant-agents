import os
import unittest
from unittest.mock import patch

import pytest

from tradingagents.default_config import (
    _ENV_OVERRIDES,
    DEFAULT_CONFIG,
    _apply_env_overrides,
    _coerce,
    default_config,
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
    def test_default_config_returns_dict(self):
        cfg = default_config()
        self.assertIsInstance(cfg, dict)

    def test_default_config_returns_deep_copy(self):
        cfg1 = default_config()
        cfg2 = default_config()
        self.assertIsNot(cfg1, cfg2)
        self.assertIsNot(cfg1["data_vendors"], cfg2["data_vendors"])

        cfg1["data_vendors"]["core_stock_apis"] = "changed"
        self.assertEqual(cfg2["data_vendors"]["core_stock_apis"], "smartmoney_db,quant_db_global,akshare,yfinance,akshare_hk")

    def test_default_config_is_independent_of_import_reference(self):
        """Mutating a returned config must not affect the next call."""
        cfg = default_config()
        cfg["quick_think_fallback"].append({"provider": "x", "model": "y"})
        cfg["benchmark_map"][""] = "QQQ"

        fresh = default_config()
        self.assertNotIn({"provider": "x", "model": "y"}, fresh["quick_think_fallback"])
        self.assertEqual(fresh["benchmark_map"][""], "SPY")

    def test_has_expected_keys(self):
        expected = {
            "llm_provider", "deep_think_llm", "quick_think_llm",
            "max_debate_rounds", "max_risk_discuss_rounds",
            "checkpoint_enabled", "output_language", "benchmark_map",
            "disable_yfinance_fallback",
        }
        cfg = default_config()
        for key in expected:
            self.assertIn(key, cfg)

    def test_defaults_are_sane(self):
        cfg = default_config()
        self.assertEqual(cfg["llm_provider"], "sensenova")
        self.assertEqual(cfg["max_debate_rounds"], 1)
        self.assertEqual(cfg["checkpoint_enabled"], False)
        self.assertEqual(cfg["output_language"], "Chinese")
        self.assertEqual(cfg["disable_yfinance_fallback"], False)

    def test_benchmark_map_has_default(self):
        cfg = default_config()
        self.assertIn("", cfg["benchmark_map"])
        self.assertEqual(cfg["benchmark_map"][""], "SPY")

    def test_data_vendors_has_research_opinion(self):
        """data_vendors must include a research_opinion category for
        analyst-opinion / research-report tool routing."""
        cfg = default_config()
        self.assertIn("data_vendors", cfg)
        self.assertIn("research_opinion", cfg["data_vendors"])
        vendors = cfg["data_vendors"]["research_opinion"]
        self.assertIsInstance(vendors, str)
        self.assertIn("akshare", vendors)

    def test_data_vendors_no_prediction_markets(self):
        """prediction_markets category was removed from default config."""
        cfg = default_config()
        self.assertNotIn("prediction_markets", cfg["data_vendors"])

    def test_default_config_backward_compat_reference(self):
        """DEFAULT_CONFIG remains a dict populated at import time."""
        self.assertIsInstance(DEFAULT_CONFIG, dict)
        self.assertIn("llm_provider", DEFAULT_CONFIG)


# ===========================================================================
# TRADINGAGENTS_* env-var overlay onto default_config().
# These exercise the public factory without module reload hacks.
# ===========================================================================


def _dc_with_env(monkeypatch, **overrides):
    """Clear TRADINGAGENTS_* env vars, set overrides, return default_config()."""
    for key in list(_ENV_OVERRIDES):
        monkeypatch.delenv(key, raising=False)
    for key, val in overrides.items():
        monkeypatch.setenv(key, val)
    return default_config()


@pytest.mark.unit
def test_no_env_uses_built_in_defaults(monkeypatch):
    cfg = _dc_with_env(monkeypatch)
    assert cfg["llm_provider"] == "sensenova"
    assert cfg["deep_think_llm"] == "deepseek-flash"
    assert cfg["quick_think_llm"] == "sensenova-6.8-flash-lite"
    assert cfg["backend_url"] == "https://token.sensenova.cn/v1"
    assert cfg["max_debate_rounds"] == 1
    assert cfg["checkpoint_enabled"] is False


@pytest.mark.unit
def test_string_env_overrides(monkeypatch):
    cfg = _dc_with_env(
        monkeypatch,
        TRADINGAGENTS_LLM_PROVIDER="google",
        TRADINGAGENTS_DEEP_THINK_LLM="gemini-3-pro-preview",
        TRADINGAGENTS_QUICK_THINK_LLM="gemini-3-flash-preview",
        TRADINGAGENTS_LLM_BACKEND_URL="https://example.invalid/v1",
        TRADINGAGENTS_OUTPUT_LANGUAGE="Chinese",
    )
    assert cfg["llm_provider"] == "google"
    assert cfg["deep_think_llm"] == "gemini-3-pro-preview"
    assert cfg["quick_think_llm"] == "gemini-3-flash-preview"
    assert cfg["backend_url"] == "https://example.invalid/v1"
    assert cfg["output_language"] == "Chinese"


@pytest.mark.unit
def test_int_env_coercion(monkeypatch):
    cfg = _dc_with_env(
        monkeypatch,
        TRADINGAGENTS_MAX_DEBATE_ROUNDS="3",
        TRADINGAGENTS_MAX_RISK_ROUNDS="2",
    )
    assert cfg["max_debate_rounds"] == 3
    assert isinstance(cfg["max_debate_rounds"], int)
    assert cfg["max_risk_discuss_rounds"] == 2
    assert isinstance(cfg["max_risk_discuss_rounds"], int)


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
        ("false", False), ("False", False), ("0", False), ("no", False), ("off", False),
    ],
)
def test_bool_env_coercion(monkeypatch, raw, expected):
    cfg = _dc_with_env(monkeypatch, TRADINGAGENTS_CHECKPOINT_ENABLED=raw)
    assert cfg["checkpoint_enabled"] is expected


@pytest.mark.unit
def test_empty_env_value_is_passthrough(monkeypatch):
    """Empty TRADINGAGENTS_* values must not clobber the built-in default."""
    cfg = _dc_with_env(
        monkeypatch,
        TRADINGAGENTS_LLM_PROVIDER="",
        TRADINGAGENTS_MAX_DEBATE_ROUNDS="",
    )
    assert cfg["llm_provider"] == "sensenova"
    assert cfg["max_debate_rounds"] == 1


@pytest.mark.unit
def test_invalid_int_env_raises(monkeypatch):
    """Garbage int values should surface a ValueError, not silently misconfigure."""
    monkeypatch.setenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "not-a-number")
    with pytest.raises(ValueError):
        default_config()


@pytest.mark.unit
def test_unknown_env_var_is_ignored(monkeypatch):
    """Env vars outside _ENV_OVERRIDES must not bleed into config."""
    cfg = _dc_with_env(
        monkeypatch,
        TRADINGAGENTS_NONEXISTENT_KEY="oops",
    )
    assert "nonexistent_key" not in cfg


@pytest.mark.unit
def test_results_dir_env_override(monkeypatch, tmp_path):
    cfg = _dc_with_env(monkeypatch, TRADINGAGENTS_RESULTS_DIR=str(tmp_path / "logs"))
    assert cfg["results_dir"] == str(tmp_path / "logs")


@pytest.mark.unit
def test_cache_dir_env_override(monkeypatch, tmp_path):
    cfg = _dc_with_env(monkeypatch, TRADINGAGENTS_CACHE_DIR=str(tmp_path / "cache"))
    assert cfg["data_cache_dir"] == str(tmp_path / "cache")


@pytest.mark.unit
def test_memory_log_path_env_override(monkeypatch, tmp_path):
    cfg = _dc_with_env(monkeypatch, TRADINGAGENTS_MEMORY_LOG_PATH=str(tmp_path / "mem.md"))
    assert cfg["memory_log_path"] == str(tmp_path / "mem.md")


@pytest.mark.unit
def test_disable_yfinance_fallback_env_override(monkeypatch):
    cfg = _dc_with_env(monkeypatch, DISABLE_YFINANCE_FALLBACK="1")
    assert cfg["disable_yfinance_fallback"] is True

    cfg_off = _dc_with_env(monkeypatch, DISABLE_YFINANCE_FALLBACK="0")
    assert cfg_off["disable_yfinance_fallback"] is False


if __name__ == "__main__":
    unittest.main()

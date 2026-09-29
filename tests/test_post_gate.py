"""Unit tests for tradingagents/dataflows/post_gate.py.

Upstream parity: TauricResearch/TradingAgents #1376 screens each social post
with Jev before it reaches the Sentiment Analyst. The local adaptation reuses
the news-gate infrastructure (config family ``jev_post_gate_*``, shadow mode,
``jev_gate_decisions.jsonl`` logging) and the fetchers' ``screen`` parameter.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

from tradingagents.dataflows import post_gate
from tradingagents.dataflows.news_gate import _log_decision
from tradingagents.llm_clients.typesafe_client import TypeSafeNewsGate


def _posts(n, prefix="Post about the company number "):
    return [f"{prefix}{i}" for i in range(n)]


def _gate_config(**overrides):
    cfg = {
        "jev_post_gate_enabled": True,
        "jev_post_gate_shadow": False,
        "jev_api_key_env": "TYPESAFE_API_KEY",
        "jev_model": "jev-latest",
        "jev_post_gate_threshold": 0.3,
        "jev_post_gate_keep_floor": 5,
        "jev_post_gate_max_articles": 30,
        "jev_post_gate_timeout": 10,
        "data_cache_dir": tempfile.mkdtemp(),
    }
    cfg.update(overrides)
    return cfg


def _patch_gate_config(cfg):
    return patch("tradingagents.dataflows.post_gate.get_config", return_value=cfg)


class PostGatePassthroughTests(unittest.TestCase):
    def test_disabled_returns_none_without_http(self):
        cfg = _gate_config(jev_post_gate_enabled=False)
        with _patch_gate_config(cfg), patch(
            "tradingagents.dataflows.post_gate.TypeSafeNewsGate"
        ) as mock_gate:
            result = post_gate.apply_post_gate(_posts(10), "AAPL", "Apple")
        self.assertIsNone(result)
        mock_gate.assert_not_called()

    def test_below_keep_floor_returns_none(self):
        cfg = _gate_config()
        with _patch_gate_config(cfg), patch(
            "tradingagents.dataflows.post_gate.TypeSafeNewsGate"
        ) as mock_gate:
            result = post_gate.apply_post_gate(_posts(5), "AAPL", "Apple")
        self.assertIsNone(result)
        mock_gate.assert_not_called()

    def test_shadow_mode_keeps_all_but_logs(self):
        cfg = _gate_config(jev_post_gate_shadow=True)
        with _patch_gate_config(cfg), patch.object(
            TypeSafeNewsGate, "score_posts", return_value=[0.9, 0.1]
        ) as mock_score:
            result = post_gate.apply_post_gate(_posts(2), "AAPL", "Apple")
        self.assertIsNone(result)
        mock_score.assert_called_once()
        log_path = os.path.join(cfg["data_cache_dir"], "jev_gate_decisions.jsonl")
        with open(log_path, encoding="utf-8") as f:
            record = json.loads(f.readline())
        self.assertEqual(len(record["articles"]), 2)
        self.assertEqual(record["source"], "post")

    def test_enforced_mode_drops_below_threshold(self):
        cfg = _gate_config(jev_post_gate_keep_floor=2)
        scores = [0.9, 0.1, 0.5]
        with _patch_gate_config(cfg), patch.object(
            TypeSafeNewsGate, "score_posts", return_value=scores
        ):
            kept, demoted = post_gate.apply_post_gate(_posts(3), "AAPL", "Apple")
        self.assertEqual(kept, [0, 2])
        self.assertEqual(demoted, [1])

    def test_scoring_failure_returns_none(self):
        cfg = _gate_config()
        with _patch_gate_config(cfg), patch.object(
            TypeSafeNewsGate, "score_posts", return_value=None
        ):
            result = post_gate.apply_post_gate(_posts(8), "AAPL", "Apple")
        self.assertIsNone(result)


class MakePostScreenTests(unittest.TestCase):
    def test_disabled_returns_none(self):
        cfg = _gate_config(jev_post_gate_enabled=False)
        with _patch_gate_config(cfg):
            self.assertIsNone(post_gate.make_post_screen("AAPL", "Apple"))

    def test_missing_company_name_returns_none(self):
        cfg = _gate_config()
        with _patch_gate_config(cfg), patch(
            "tradingagents.ticker_resolver.resolve_ticker",
            side_effect=Exception("no name"),
        ):
            self.assertIsNone(post_gate.make_post_screen("XXXX", ""))

    def test_screen_drops_off_topic_and_notes(self):
        cfg = _gate_config(jev_post_gate_keep_floor=1)
        scores = [0.9, 0.05]  # second post clearly not about the company
        with _patch_gate_config(cfg), patch.object(
            TypeSafeNewsGate, "score_posts", return_value=scores
        ):
            screen = post_gate.make_post_screen("AAPL", "Apple")
            keep, note = screen(["on topic", "totally unrelated spam"])
        self.assertEqual(keep, [True, False])
        self.assertIn("Screened by Jev", note)
        self.assertIn("1 of the 2", note)
        self.assertIn("Apple (AAPL)", note)

    def test_screen_fail_open_keeps_all(self):
        cfg = _gate_config()
        with _patch_gate_config(cfg), patch.object(
            TypeSafeNewsGate, "score_posts", return_value=None
        ):
            screen = post_gate.make_post_screen("AAPL", "Apple")
            keep, note = screen(["a", "b"])
        self.assertEqual(keep, [True, True])
        self.assertEqual(note, "")

    def test_screen_crash_is_contained(self):
        cfg = _gate_config()
        with _patch_gate_config(cfg), patch(
            "tradingagents.dataflows.post_gate.apply_post_gate",
            side_effect=RuntimeError("boom"),
        ):
            screen = post_gate.make_post_screen("AAPL", "Apple")
            keep, note = screen(["a", "b"])
        self.assertEqual(keep, [True, True])


class PostGateSourceTagTests(unittest.TestCase):
    def test_log_decision_records_source(self):
        cfg = _gate_config()
        articles = [{"title": "hello post"}]
        with tempfile.TemporaryDirectory() as tmp:
            cfg["data_cache_dir"] = tmp
            _log_decision(cfg, "AAPL", articles, [0.9], [0], [], False, source="stocktwits")
            log_path = os.path.join(tmp, "jev_gate_decisions.jsonl")
            with open(log_path, encoding="utf-8") as f:
                record = json.loads(f.readline())
        self.assertEqual(record["source"], "stocktwits")

    def test_log_decision_defaults_to_news(self):
        cfg = _gate_config()
        with tempfile.TemporaryDirectory() as tmp:
            cfg["data_cache_dir"] = tmp
            _log_decision(cfg, "AAPL", [{"title": "t"}], [0.9], [0], [], False)
            log_path = os.path.join(tmp, "jev_gate_decisions.jsonl")
            with open(log_path, encoding="utf-8") as f:
                record = json.loads(f.readline())
        self.assertEqual(record["source"], "news")

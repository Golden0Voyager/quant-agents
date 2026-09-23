import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

pytestmark = pytest.mark.unit

from tradingagents.dataflows import news_gate
from tradingagents.llm_clients.typesafe_client import TypeSafeNewsGate


def _articles(n, prefix="标题"):
    return [
        {
            "title": f"{prefix}{i}",
            "body": f"正文内容{i}",
            "publisher": "测试来源",
            "pub_date": "2026-09-20",
            "link": f"http://example.com/{i}",
        }
        for i in range(n)
    ]


def _gate_config(**overrides):
    cfg = {
        "jev_news_gate_enabled": True,
        "jev_news_gate_shadow": False,
        "jev_api_key_env": "TYPESAFE_API_KEY",
        "jev_model": "jev-latest",
        "jev_news_gate_threshold": 0.5,
        "jev_news_gate_keep_floor": 5,
        "jev_news_gate_max_articles": 30,
        "jev_news_gate_timeout": 10,
        "data_cache_dir": tempfile.mkdtemp(),
    }
    cfg.update(overrides)
    return cfg


def _patch_gate_config(cfg):
    return patch("tradingagents.dataflows.news_gate.get_config", return_value=cfg)


class NewsGatePassthroughTests(unittest.TestCase):
    def test_disabled_returns_none_without_http(self):
        cfg = _gate_config(jev_news_gate_enabled=False)
        with _patch_gate_config(cfg), patch(
            "tradingagents.dataflows.news_gate.TypeSafeNewsGate"
        ) as mock_gate:
            result = news_gate.apply_news_gate(_articles(10), "300454.SZ", "测试公司")
        self.assertIsNone(result)
        mock_gate.assert_not_called()

    def test_below_keep_floor_returns_none(self):
        cfg = _gate_config()
        with _patch_gate_config(cfg), patch(
            "tradingagents.dataflows.news_gate.TypeSafeNewsGate"
        ) as mock_gate:
            result = news_gate.apply_news_gate(_articles(5), "300454.SZ", "测试公司")
        self.assertIsNone(result)
        mock_gate.assert_not_called()

    def test_shadow_mode_bypasses_keep_floor(self):
        cfg = _gate_config(jev_news_gate_shadow=True)
        with _patch_gate_config(cfg), patch.object(
            news_gate.TypeSafeNewsGate, "score_articles", return_value=[0.9, 0.1]
        ) as mock_score:
            result = news_gate.apply_news_gate(_articles(2), "300454.SZ", "测试公司")
        self.assertIsNone(result)
        mock_score.assert_called_once()
        log_path = os.path.join(cfg["data_cache_dir"], "jev_gate_decisions.jsonl")
        with open(log_path, encoding="utf-8") as f:
            record = json.loads(f.readline())
        self.assertEqual(len(record["articles"]), 2)

    def test_api_failure_returns_none(self):
        cfg = _gate_config()
        with _patch_gate_config(cfg), patch.object(
            news_gate.TypeSafeNewsGate, "score_articles", return_value=None
        ):
            result = news_gate.apply_news_gate(_articles(10), "300454.SZ", "测试公司")
        self.assertIsNone(result)


class NewsGateSplitTests(unittest.TestCase):
    def test_scores_split_kept_demoted(self):
        cfg = _gate_config()
        with _patch_gate_config(cfg), patch.object(
            news_gate.TypeSafeNewsGate, "score_articles", return_value=[0.9, 0.2, 0.8] + [0.6] * 7
        ):
            result = news_gate.apply_news_gate(_articles(10), "300454.SZ", "测试公司")
        self.assertIsNotNone(result)
        kept, demoted = result
        self.assertIn(0, kept)
        self.assertIn(2, kept)
        self.assertEqual(demoted, [1])

    def test_shadow_mode_returns_none_but_logs(self):
        cfg = _gate_config(jev_news_gate_shadow=True)
        with _patch_gate_config(cfg), patch.object(
            news_gate.TypeSafeNewsGate, "score_articles", return_value=[0.9] + [0.1] * 9
        ):
            result = news_gate.apply_news_gate(_articles(10), "300454.SZ", "测试公司")
        self.assertIsNone(result)
        log_path = os.path.join(cfg["data_cache_dir"], "jev_gate_decisions.jsonl")
        self.assertTrue(os.path.exists(log_path))
        with open(log_path, encoding="utf-8") as f:
            record = json.loads(f.readline())
        self.assertEqual(record["symbol"], "300454.SZ")
        self.assertTrue(record["shadow"])
        self.assertEqual(len(record["articles"]), 10)
        self.assertEqual(record["articles"][0]["score"], 0.9)
        self.assertTrue(record["articles"][0]["kept"])
        self.assertFalse(record["articles"][1]["kept"])


def _akshare_df():
    return pd.DataFrame(
        [
            {
                "新闻标题": f"测试公司重大合同{i}",
                "新闻内容": f"测试公司签订大额合同正文{i}",
                "文章来源": "东财",
                "发布时间": "2026-09-20 10:00:00",
                "新闻链接": f"http://example.com/{i}",
            }
            for i in range(6)
        ]
    )


class AkshareVendorGateTests(unittest.TestCase):
    def test_demoted_in_title_section_not_body(self):
        from tradingagents.dataflows import akshare_vendor

        cfg = _gate_config()
        scores = [0.9, 0.9, 0.1, 0.1, 0.9, 0.9]
        with _patch_gate_config(cfg), patch(
            "tradingagents.dataflows.akshare_vendor.ak"
        ) as mock_ak, patch(
            "tradingagents.ticker_resolver.resolve_ticker",
            return_value={"ticker": "300454.SZ", "company_name": "测试公司"},
        ), patch.object(
            news_gate.TypeSafeNewsGate, "score_articles", return_value=scores
        ):
            mock_ak.stock_news_em.return_value = _akshare_df()
            result = akshare_vendor.get_news("300454.SZ", "2026-09-15", "2026-09-22")

        self.assertIn("🤖", result)
        # demoted titles appear in the demoted title-only section
        demoted_sec = result.split("🤖", 1)[1]
        self.assertIn("测试公司重大合同2", demoted_sec)
        self.assertIn("测试公司重大合同3", demoted_sec)
        # demoted bodies do not appear anywhere
        self.assertNotIn("签订大额合同正文2", result)
        self.assertNotIn("签订大额合同正文3", result)
        # kept bodies still rendered
        self.assertIn("签订大额合同正文0", result)


class GateDecisionLogTests(unittest.TestCase):
    def test_log_format(self):
        cfg = _gate_config()
        with _patch_gate_config(cfg), patch.object(
            news_gate.TypeSafeNewsGate, "score_articles", return_value=[0.9, 0.1] + [0.7] * 6
        ):
            news_gate.apply_news_gate(_articles(8), "000001.SZ", "测试银行")
        log_path = os.path.join(cfg["data_cache_dir"], "jev_gate_decisions.jsonl")
        with open(log_path, encoding="utf-8") as f:
            record = json.loads(f.readline())
        self.assertIn("ts", record)
        self.assertEqual(record["symbol"], "000001.SZ")
        self.assertIn("shadow", record)
        for entry in record["articles"]:
            self.assertIn("title", entry)
            self.assertIn("score", entry)
            self.assertIn("kept", entry)


class AkshareAllDemoteTests(unittest.TestCase):
    def test_all_demoted_renders_title_section_without_error(self):
        from tradingagents.dataflows import akshare_vendor

        cfg = _gate_config()
        with _patch_gate_config(cfg), patch(
            "tradingagents.dataflows.akshare_vendor.ak"
        ) as mock_ak, patch(
            "tradingagents.ticker_resolver.resolve_ticker",
            return_value={"ticker": "300454.SZ", "company_name": "测试公司"},
        ), patch.object(
            news_gate.TypeSafeNewsGate, "score_articles", return_value=[0.1] * 6
        ):
            mock_ak.stock_news_em.return_value = _akshare_df()
            result = akshare_vendor.get_news("300454.SZ", "2026-09-15", "2026-09-22")

        self.assertIn("🤖", result)
        self.assertIn("测试公司重大合同0", result)
        for i in range(6):
            self.assertNotIn(f"签订大额合同正文{i}", result)


def _yahoo_article(title, summary, pub_date="2025-06-15T10:00:00Z"):
    return {
        "content": {
            "title": title,
            "summary": summary,
            "provider": {"displayName": "Yahoo"},
            "canonicalUrl": {"url": "https://example.com/x"},
            "pubDate": pub_date,
        }
    }


class YFinanceGateTests(unittest.TestCase):
    def test_demoted_titles_only_in_yfinance_path(self):
        from tradingagents.dataflows.yfinance_news import get_news_yfinance

        mock_news = [
            _yahoo_article(f"Yahoo Story {i}", f"Yahoo summary body {i}")
            for i in range(6)
        ]
        scores = [0.9, 0.9, 0.1, 0.9, 0.1, 0.9]
        yahoo_cfg = {
            "news_article_limit": 20,
            "jev_news_gate_enabled": True,
            "jev_news_gate_shadow": False,
            "jev_news_gate_threshold": 0.5,
            "jev_news_gate_keep_floor": 5,
            "jev_news_gate_max_articles": 30,
            "jev_news_gate_timeout": 10,
            "jev_api_key_env": "TYPESAFE_API_KEY",
            "jev_model": "jev-latest",
            "data_cache_dir": tempfile.mkdtemp(),
        }
        with patch(
            "tradingagents.dataflows.yfinance_news.get_config", return_value=yahoo_cfg
        ), patch(
            "tradingagents.dataflows.news_gate.get_config", return_value=yahoo_cfg
        ), patch.object(
            news_gate.TypeSafeNewsGate, "score_articles", return_value=scores
        ):
            mock_ticker = MagicMock()
            mock_ticker.get_news.return_value = mock_news
            with patch(
                "tradingagents.dataflows.yfinance_news.yf.Ticker",
                return_value=mock_ticker,
            ), patch(
                "tradingagents.dataflows.yfinance_news.yf_retry",
                side_effect=lambda f, **kwargs: f(),
            ):
                result = get_news_yfinance("AAPL", "2025-06-01", "2025-06-30")

        self.assertIn("🤖", result)
        demoted_sec = result.split("🤖", 1)[1]
        self.assertIn("Yahoo Story 2", demoted_sec)
        self.assertIn("Yahoo Story 4", demoted_sec)
        self.assertNotIn("Yahoo summary body 2", result)
        self.assertNotIn("Yahoo summary body 4", result)
        self.assertIn("Yahoo summary body 0", result)


def _ok_response(answers: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"answers": answers}
    return resp


class TypeSafeClientTests(unittest.TestCase):
    def _gate(self, **overrides):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}, clear=False):
            return TypeSafeNewsGate(_gate_config(**overrides))

    def test_unavailable_without_api_key(self):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}, clear=False):
            gate = TypeSafeNewsGate(_gate_config())
        self.assertFalse(gate.available)
        self.assertIsNone(gate.score_articles(_articles(3), {}))

    def test_request_body_carries_each_article(self):
        gate = self._gate()
        answers = {f"article_{i}": {"type": "noul", "noul": 0.9 - i * 0.1} for i in range(3)}
        with patch(
            "tradingagents.llm_clients.typesafe_client.requests.post",
            return_value=_ok_response(answers),
        ) as mock_post:
            scores = gate.score_articles(
                _articles(3),
                {"ticker": "002594.SZ", "company_name": "比亚迪", "date_range": "x"},
            )
        self.assertEqual(scores, [0.9, 0.8, 0.7])
        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body["state"]["company_name"], "比亚迪")
        for i in range(3):
            question = body["questions"][f"article_{i}"]
            self.assertEqual(question["type"], "noul")
            self.assertEqual(question["instructions"]["article"]["title"], f"标题{i}")
            self.assertEqual(question["instructions"]["article"]["body"], f"正文内容{i}")
            self.assertIn("criteria", question)

    def test_truncates_body(self):
        gate = self._gate()
        articles = _articles(1)
        articles[0]["body"] = "x" * 900
        with patch(
            "tradingagents.llm_clients.typesafe_client.requests.post",
            return_value=_ok_response({"article_0": {"noul": 0.5}}),
        ) as mock_post:
            gate.score_articles(articles, {})
        posted = mock_post.call_args.kwargs["json"]["questions"]["article_0"]
        self.assertEqual(len(posted["instructions"]["article"]["body"]), 500)

    def test_articles_beyond_max_kept_at_full_score(self):
        gate = self._gate(jev_news_gate_max_articles=2)
        with patch(
            "tradingagents.llm_clients.typesafe_client.requests.post",
            return_value=_ok_response(
                {"article_0": {"noul": 0.2}, "article_1": {"noul": 0.3}}
            ),
        ) as mock_post:
            scores = gate.score_articles(_articles(4), {})
        self.assertEqual(scores, [0.2, 0.3, 1.0, 1.0])
        self.assertEqual(len(mock_post.call_args.kwargs["json"]["questions"]), 2)

    def test_transport_error_returns_none(self):
        gate = self._gate()
        with patch(
            "tradingagents.llm_clients.typesafe_client.requests.post",
            side_effect=OSError("connection refused"),
        ):
            self.assertIsNone(gate.score_articles(_articles(2), {}))

    def test_http_error_status_returns_none(self):
        gate = self._gate()
        resp = MagicMock()
        resp.raise_for_status.side_effect = OSError("500")
        with patch(
            "tradingagents.llm_clients.typesafe_client.requests.post", return_value=resp
        ):
            self.assertIsNone(gate.score_articles(_articles(2), {}))

    def test_missing_answer_key_returns_none(self):
        gate = self._gate()
        with patch(
            "tradingagents.llm_clients.typesafe_client.requests.post",
            return_value=_ok_response({}),
        ):
            self.assertIsNone(gate.score_articles(_articles(2), {}))


class DecisionLogErrorTests(unittest.TestCase):
    def test_unwritable_log_path_does_not_raise(self):
        cfg = _gate_config(
            jev_news_gate_shadow=True, data_cache_dir="/nonexistent-root/nope"
        )
        with _patch_gate_config(cfg), patch.object(
            news_gate.TypeSafeNewsGate, "score_articles", return_value=[0.9, 0.1, 0.9]
        ):
            result = news_gate.apply_news_gate(_articles(3), "300454.SZ", "测试公司")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

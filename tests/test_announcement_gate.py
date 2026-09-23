"""Tests for the Jev announcement gate (spec docs/jev_announcement_gate_design.md §5.6).

Covers the 12 scenarios from §5.6 plus the news-default-prefix guard (MAJOR-4).
All network access is mocked: ``requests.get`` (body fetch),
``requests.post`` (Jev scoring) and ``ak.stock_individual_notice_report``.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest
import requests

pytestmark = pytest.mark.unit

from tradingagents.dataflows import akshare_vendor, announcement_bodies, announcement_dedup, news_gate

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _ann_df(n=4, prefix_dates=True):
    """A-share announcement DataFrame shaped like stock_individual_notice_report rows.

    ``网址`` carries a distinct eastmoney art-code fragment so the vendor's
    ``_art_code`` extraction yields ``AN202609200{i}001``.
    """
    return pd.DataFrame(
        [
            {
                "公告标题": f"标题{i}",
                "公告类型": "其他",
                "公告日期": f"2026-09-{i:02d}",
                "网址": f"https://data.eastmoney.com/notices/detail/002594/AN202609200{i}001.html",
            }
            for i in range(1, n + 1)
        ]
    )


def _ann_cfg(**overrides):
    cfg = {
        "jev_ann_gate_enabled": True,
        "jev_ann_gate_shadow": False,
        "jev_ann_gate_threshold": 0.5,
        "jev_ann_gate_keep_floor": 5,
        "jev_ann_gate_max_per_type": 3,
        "jev_ann_gate_fetch_bodies": True,
        "jev_ann_gate_body_trunc": 800,
        "jev_ann_gate_fetch_timeout": 15,
        "jev_ann_gate_max_workers": 5,
        "jev_api_key_env": "TYPESAFE_API_KEY",
        "data_cache_dir": tempfile.mkdtemp(),
    }
    cfg.update(overrides)
    return cfg


def _render(df, cfg):
    with (
        patch("tradingagents.dataflows.config.get_config", return_value=cfg),
        patch(
            "tradingagents.dataflows.akshare_vendor.ak.stock_individual_notice_report",
            return_value=df,
        ),
    ):
        return akshare_vendor.get_company_announcements("002594.SZ", "2026-09-01", "2026-09-30")


def _ok_response(answers: dict):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"answers": answers}
    return resp


def _body_get_response(art_code: str):
    """Mock ``requests.get`` response carrying one announcement body."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"data": {"notice_content": f"<p>正文BODY{art_code}</p>"}}
    return resp


# ---------------------------------------------------------------------------
# 1  extract_art_code — valid & malformed URLs
# ---------------------------------------------------------------------------

class ExtractArtCodeTests(unittest.TestCase):
    def test_extract_art_code_valid_and_malformed(self):
        url = "https://data.eastmoney.com/notices/detail/002594/AN202609201829667819.html"
        self.assertEqual(
            announcement_bodies.extract_art_code(url), "AN202609201829667819"
        )
        self.assertIsNone(announcement_bodies.extract_art_code("no-code"))
        self.assertIsNone(announcement_bodies.extract_art_code(""))
        self.assertIsNone(announcement_bodies.extract_art_code(None))


# ---------------------------------------------------------------------------
# 2/3  fetch_bodies — cache hit / total failure
# ---------------------------------------------------------------------------

class FetchBodiesTests(unittest.TestCase):
    def test_cache_hit_no_http(self):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "AN1.txt"), "w", encoding="utf-8") as f:
            f.write("cached body text")
        with patch(
            "tradingagents.dataflows.announcement_bodies.requests.get"
        ) as mock_get:
            bodies = announcement_bodies.fetch_bodies(["AN1"], tmp)
        self.assertEqual(bodies, {"AN1": "cached body text"})
        mock_get.assert_not_called()

    def test_fetch_failure_returns_empty(self):
        tmp = tempfile.mkdtemp()
        with patch(
            "tradingagents.dataflows.announcement_bodies.requests.get",
            side_effect=Exception("boom"),
        ):
            bodies = announcement_bodies.fetch_bodies(["AN1"], tmp, retries=0)
        self.assertEqual(bodies, {})


# ---------------------------------------------------------------------------
# 4/5  dedup_announcements — identity dedup & series cap
# ---------------------------------------------------------------------------

class DedupAnnouncementsTests(unittest.TestCase):
    def test_identity_dedup_same_art_code_keeps_newest_date(self):
        rows = [
            {"_art_code": "AN1", "公告类型": "其他", "公告日期": "2026-09-20", "公告标题": "旧"},
            {"_art_code": "AN1", "公告类型": "其他", "公告日期": "2026-09-22", "公告标题": "新"},
        ]
        kept, dropped = announcement_dedup.dedup_announcements(
            rows, max_per_type=3, series_types=("调研活动",)
        )
        self.assertEqual([r["公告标题"] for r in kept], ["新"])
        self.assertEqual([r["公告标题"] for r in dropped], ["旧"])

    def test_series_cap_keeps_newest_n(self):
        rows = [
            {"_art_code": f"AN{i}", "公告类型": "调研活动", "公告日期": f"2026-09-{i:02d}", "公告标题": f"调研{i}"}
            for i in range(1, 9)
        ]
        kept, dropped = announcement_dedup.dedup_announcements(
            rows, max_per_type=3, series_types=("调研活动",)
        )
        self.assertEqual([r["公告标题"] for r in kept], ["调研6", "调研7", "调研8"])
        self.assertEqual(len(dropped), 5)
        self.assertNotIn("调研1", {r["公告标题"] for r in kept})


# ---------------------------------------------------------------------------
# 6  gate disabled → byte-identical to status quo, no fetch, no dedup
# ---------------------------------------------------------------------------

class GateDisabledTests(unittest.TestCase):
    def test_gate_disabled_noop_byte_identical(self):
        df = _ann_df()
        baseline = _render(df, {})  # jev_ann_gate_enabled defaults to False

        # fetch + dedup must be provably untouched when the gate is off.
        with (
            patch("tradingagents.dataflows.config.get_config", return_value={}),
            patch(
                "tradingagents.dataflows.akshare_vendor.ak.stock_individual_notice_report",
                return_value=df,
            ),
            patch(
                "tradingagents.dataflows.announcement_bodies.fetch_bodies",
                side_effect=AssertionError("fetch_bodies must not run when gate disabled"),
            ),
            patch(
                "tradingagents.dataflows.announcement_dedup.dedup_announcements",
                side_effect=AssertionError("dedup must not run when gate disabled"),
            ),
        ):
            output = akshare_vendor.get_company_announcements(
                "002594.SZ", "2026-09-01", "2026-09-30"
            )

        self.assertEqual(output, baseline)
        self.assertIn("Total notices: 4", output)
        self.assertNotIn("正文BODY", output)


# ---------------------------------------------------------------------------
# 7  shadow mode → fetch + score + JSONL, rendering byte-identical
# ---------------------------------------------------------------------------

class ShadowModeTests(unittest.TestCase):
    def test_shadow_fetches_but_render_unchanged(self):
        tmp = tempfile.mkdtemp()
        df = _ann_df()
        baseline = _render(df, {})

        def _get_side_effect(*args, **kwargs):
            params = kwargs.get("params", {})
            return _body_get_response(params.get("art_code", ""))

        answers = {f"article_{i}": {"type": "noul", "noul": 0.9} for i in range(4)}
        cfg = _ann_cfg(
            jev_ann_gate_enabled=True,
            jev_ann_gate_shadow=True,
            jev_ann_gate_keep_floor=0,
            data_cache_dir=tmp,
        )
        with (
            patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}),
            patch("tradingagents.dataflows.config.get_config", return_value=cfg),
            patch("tradingagents.dataflows.news_gate.get_config", return_value=cfg),
            patch(
                "tradingagents.dataflows.akshare_vendor.ak.stock_individual_notice_report",
                return_value=df,
            ),
            patch(
                "tradingagents.dataflows.announcement_bodies.requests.get",
                side_effect=_get_side_effect,
            ) as mock_get,
            patch(
                "tradingagents.llm_clients.typesafe_client.requests.post",
                return_value=_ok_response(answers),
            ) as mock_post,
        ):
            output = akshare_vendor.get_company_announcements(
                "002594.SZ", "2026-09-01", "2026-09-30"
            )

        # Rendering must be byte-identical to the disabled baseline: no body,
        # no dedup, no demotion.
        self.assertEqual(output, baseline)
        self.assertNotIn("正文BODY", output)
        self.assertIn("Total notices: 4", output)
        # But the shadow side-effects must have happened.
        mock_get.assert_called()
        mock_post.assert_called()
        jsonl = os.path.join(tmp, "jev_gate_decisions.jsonl")
        self.assertTrue(os.path.exists(jsonl))
        with open(jsonl, encoding="utf-8") as f:
            self.assertIn("002594", f.read())


# ---------------------------------------------------------------------------
# 8  normal mode → kept rows carry body, demoted rows are title-only
# ---------------------------------------------------------------------------

class FormalModeTests(unittest.TestCase):
    def test_normal_mode_kept_with_body_demoted_title_only(self):
        tmp = tempfile.mkdtemp()
        df = _ann_df()
        scores = [0.9, 0.1, 0.9, 0.9]  # article_1 (标题2) falls below threshold

        def _get_side_effect(*args, **kwargs):
            params = kwargs.get("params", {})
            return _body_get_response(params.get("art_code", ""))

        answers = {f"article_{i}": {"type": "noul", "noul": s} for i, s in enumerate(scores)}
        cfg = _ann_cfg(
            jev_ann_gate_enabled=True,
            jev_ann_gate_shadow=False,
            jev_ann_gate_keep_floor=0,
            data_cache_dir=tmp,
        )
        with (
            patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}),
            patch("tradingagents.dataflows.config.get_config", return_value=cfg),
            patch("tradingagents.dataflows.news_gate.get_config", return_value=cfg),
            patch(
                "tradingagents.dataflows.akshare_vendor.ak.stock_individual_notice_report",
                return_value=df,
            ),
            patch(
                "tradingagents.dataflows.announcement_bodies.requests.get",
                side_effect=_get_side_effect,
            ),
            patch(
                "tradingagents.llm_clients.typesafe_client.requests.post",
                return_value=_ok_response(answers),
            ),
        ):
            output = akshare_vendor.get_company_announcements(
                "002594.SZ", "2026-09-01", "2026-09-30"
            )

        # High-score rows keep their bodies…
        self.assertIn("正文BODYAN2026092001001", output)  # 标题1 (0.9)
        self.assertIn("正文BODYAN2026092003001", output)  # 标题3 (0.9)
        # …the demoted row is title-only, body absent.
        self.assertNotIn("正文BODYAN2026092002001", output)  # 标题2 (0.1)
        self.assertIn("🤖", output)
        self.assertIn("- 标题2 (2026-09-02)", output)
        # Total notices reflects the post-dedup kept rows (demotion does not
        # change the announcement count reported by the vendor).
        self.assertIn("Total notices: 4", output)


# ---------------------------------------------------------------------------
# 9  all body fetches fail → title-only, no exception / NoMarketDataError
# ---------------------------------------------------------------------------

class AllFetchFailTests(unittest.TestCase):
    def test_all_fetch_fail_title_only_no_exception(self):
        tmp = tempfile.mkdtemp()
        df = _ann_df(3)
        cfg = _ann_cfg(jev_ann_gate_enabled=True, jev_ann_gate_shadow=False, data_cache_dir=tmp)
        with (
            patch("tradingagents.dataflows.config.get_config", return_value=cfg),
            patch("tradingagents.dataflows.news_gate.get_config", return_value=cfg),
            patch(
                "tradingagents.dataflows.akshare_vendor.ak.stock_individual_notice_report",
                return_value=df,
            ),
            patch(
                "tradingagents.dataflows.announcement_bodies.fetch_bodies", return_value={}
            ),
        ):
            output = akshare_vendor.get_company_announcements(
                "002594.SZ", "2026-09-01", "2026-09-30"
            )
        self.assertIsInstance(output, str)
        self.assertNotIn("正文BODY", output)
        self.assertIn("标题1", output)
        self.assertIn("Total notices: 3", output)


# ---------------------------------------------------------------------------
# 10  no API key → bodies still rendered, no demotion, warning logged
# ---------------------------------------------------------------------------

class NoApiKeyTests(unittest.TestCase):
    def test_no_api_key_still_renders_bodies(self):
        tmp = tempfile.mkdtemp()
        df = _ann_df(6)
        codes = [f"AN202609200{i}001" for i in range(1, 7)]
        bodies = {c: f"正文BODY{c}" for c in codes}
        cfg = _ann_cfg(
            jev_ann_gate_enabled=True,
            jev_ann_gate_shadow=False,
            jev_ann_gate_keep_floor=5,
            data_cache_dir=tmp,
        )
        with (
            patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}),
            patch("tradingagents.dataflows.config.get_config", return_value=cfg),
            patch("tradingagents.dataflows.news_gate.get_config", return_value=cfg),
            patch(
                "tradingagents.dataflows.akshare_vendor.ak.stock_individual_notice_report",
                return_value=df,
            ),
            patch(
                "tradingagents.dataflows.announcement_bodies.fetch_bodies",
                return_value=bodies,
            ),
        ):
            output = akshare_vendor.get_company_announcements(
                "002594.SZ", "2026-09-01", "2026-09-30"
            )
        # Bodies still fetched and rendered — Jev key is not a gate on fetching.
        self.assertIn("正文BODYAN2026092001001", output)
        self.assertIn("正文BODYAN2026092006001", output)
        # No demotion happened.
        self.assertNotIn("🤖", output)
        self.assertIn("Total notices: 6", output)


# ---------------------------------------------------------------------------
# 11  body strips to empty → title-only for that row
# ---------------------------------------------------------------------------

class StripEmptyTests(unittest.TestCase):
    def test_strip_empty_falls_back_title_only(self):
        tmp = tempfile.mkdtemp()
        df = _ann_df(3)
        missing = "AN2026092002001"  # 标题2 has no body
        bodies = {"AN2026092001001": "正文BODYAN2026092001001", "AN2026092003001": "正文BODYAN2026092003001"}
        cfg = _ann_cfg(jev_ann_gate_enabled=True, jev_ann_gate_shadow=False, data_cache_dir=tmp)
        with (
            patch("tradingagents.dataflows.config.get_config", return_value=cfg),
            patch("tradingagents.dataflows.news_gate.get_config", return_value=cfg),
            patch(
                "tradingagents.dataflows.akshare_vendor.ak.stock_individual_notice_report",
                return_value=df,
            ),
            patch(
                "tradingagents.dataflows.announcement_bodies.fetch_bodies",
                return_value=bodies,
            ),
        ):
            output = akshare_vendor.get_company_announcements(
                "002594.SZ", "2026-09-01", "2026-09-30"
            )
        # The empty-body row keeps its title but renders no empty placeholder.
        self.assertIn("标题2", output)
        self.assertNotIn("正文BODY" + missing, output)
        self.assertNotIn("【正文】", output)
        self.assertNotIn("正文BODY" + missing, output)
        # Other rows keep their bodies.
        self.assertIn("正文BODYAN2026092001001", output)


# ---------------------------------------------------------------------------
# 12  config_prefix isolation (BLOCKER-1)
# ---------------------------------------------------------------------------

class ConfigPrefixIsolationTests(unittest.TestCase):
    def test_config_prefix_isolation(self):
        tmp = tempfile.mkdtemp()
        articles = news_gate._articles(3) if hasattr(news_gate, "_articles") else None
        if articles is None:
            articles = [
                {"title": f"标题{i}", "body": f"正文{i}", "publisher": "公告",
                 "pub_date": "2026-09-01", "link": f"http://x/{i}"}
                for i in range(3)
            ]

        # A) only jev_ann_gate_* keys → ann prefix routes into the gate.
        ann_cfg = {
            "jev_ann_gate_enabled": True,
            "jev_ann_gate_shadow": False,
            "jev_ann_gate_keep_floor": 0,
            "jev_ann_gate_threshold": 0.5,
            "jev_api_key_env": "TYPESAFE_API_KEY",
            "data_cache_dir": tmp,
        }
        with patch("tradingagents.dataflows.news_gate.get_config", return_value=ann_cfg), patch(
            "tradingagents.dataflows.news_gate.TypeSafeNewsGate"
        ) as mock_gate:
            mock_gate.return_value.score_articles.return_value = [0.9, 0.9, 0.9]
            result = news_gate.apply_news_gate(
                articles, "002594.SZ", "测试公司", config_prefix="jev_ann_gate"
            )
        self.assertIsNotNone(result)
        mock_gate.assert_called_once()

        # B) only jev_news_gate_* keys → ann prefix must NOT run the gate.
        news_cfg = {
            "jev_news_gate_enabled": True,
            "jev_news_gate_shadow": False,
            "jev_news_gate_keep_floor": 0,
            "jev_news_gate_threshold": 0.5,
            "jev_api_key_env": "TYPESAFE_API_KEY",
            "data_cache_dir": tmp,
        }
        with patch("tradingagents.dataflows.news_gate.get_config", return_value=news_cfg), patch(
            "tradingagents.dataflows.news_gate.TypeSafeNewsGate"
        ) as mock_gate:
            result = news_gate.apply_news_gate(
                articles, "002594.SZ", "测试公司", config_prefix="jev_ann_gate"
            )
        self.assertIsNone(result)
        mock_gate.assert_not_called()

        # C) news prefix + news keys → gate runs.
        with patch("tradingagents.dataflows.news_gate.get_config", return_value=news_cfg), patch(
            "tradingagents.dataflows.news_gate.TypeSafeNewsGate"
        ) as mock_gate:
            mock_gate.return_value.score_articles.return_value = [0.9, 0.9, 0.9]
            result = news_gate.apply_news_gate(articles, "002594.SZ", "测试公司")
        self.assertIsNotNone(result)
        mock_gate.assert_called_once()


# ---------------------------------------------------------------------------
# MAJOR-4  news default path unchanged: default prefix honors its own threshold
# ---------------------------------------------------------------------------

class NewsDefaultPathTests(unittest.TestCase):
    def test_news_default_prefix_threshold_honored(self):
        tmp = tempfile.mkdtemp()
        articles = [
            {"title": f"标题{i}", "body": f"正文{i}", "publisher": "来源",
             "pub_date": "2026-09-01", "link": f"http://x/{i}"}
            for i in range(2)
        ]
        cfg = {
            "jev_news_gate_enabled": True,
            "jev_news_gate_shadow": False,
            "jev_news_gate_keep_floor": 0,
            "jev_news_gate_threshold": 0.5,
            "jev_api_key_env": "TYPESAFE_API_KEY",
            "data_cache_dir": tmp,
        }
        with patch("tradingagents.dataflows.news_gate.get_config", return_value=cfg), patch(
            "tradingagents.dataflows.news_gate.TypeSafeNewsGate"
        ) as mock_gate:
            mock_gate.return_value.score_articles.return_value = [0.1, 0.9]
            kept, demoted = news_gate.apply_news_gate(articles, "002594.SZ", "测试公司")
        self.assertEqual(kept, [1])
        self.assertEqual(demoted, [0])


# ---------------------------------------------------------------------------
# Edge cases — fetch_bodies fail-open paths (empty input / makedirs failure /
# missing content / strip-to-empty / retry backoff / cache write failure)
# ---------------------------------------------------------------------------

class FetchBodiesEdgeCaseTests(unittest.TestCase):
    def test_empty_art_codes_returns_empty(self):
        bodies = announcement_bodies.fetch_bodies([], str(tempfile.mkdtemp()))
        self.assertEqual(bodies, {})

    def test_cache_dir_create_failure_is_nonfatal(self):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "AN1.txt"), "w", encoding="utf-8") as f:
            f.write("cached body text")
        with patch(
            "tradingagents.dataflows.announcement_bodies.os.makedirs",
            side_effect=OSError("denied"),
        ):
            bodies = announcement_bodies.fetch_bodies(["AN1"], tmp)
        self.assertEqual(bodies, {"AN1": "cached body text"})

    def test_api_missing_notice_content_returns_empty(self):
        tmp = tempfile.mkdtemp()
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"data": {}}
        with patch(
            "tradingagents.dataflows.announcement_bodies.requests.get",
            return_value=resp,
        ):
            bodies = announcement_bodies.fetch_bodies(["AN2"], tmp, retries=0)
        self.assertEqual(bodies, {})

    def test_strip_to_empty_omits_and_skips_cache(self):
        tmp = tempfile.mkdtemp()

        def _resp(content):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {"data": {"notice_content": content}}
            return resp

        with patch(
            "tradingagents.dataflows.announcement_bodies.requests.get",
            side_effect=[_resp("   "), _resp("<p></p>")],
        ):
            bodies = announcement_bodies.fetch_bodies(["AN3", "AN4"], tmp, retries=0)
        self.assertEqual(bodies, {})
        self.assertFalse(os.path.exists(os.path.join(tmp, "AN3.txt")))
        self.assertFalse(os.path.exists(os.path.join(tmp, "AN4.txt")))

    def test_retry_backoff_then_give_up(self):
        tmp = tempfile.mkdtemp()
        with (
            patch(
                "tradingagents.dataflows.announcement_bodies.requests.get",
                side_effect=requests.ConnectionError("boom"),
            ),
            patch(
                "tradingagents.dataflows.announcement_bodies.time.sleep"
            ) as mock_sleep,
        ):
            bodies = announcement_bodies.fetch_bodies(["ANR1"], tmp, retries=2, timeout=5)
        self.assertEqual(bodies, {})
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertEqual(mock_sleep.call_args_list, [call(1.0), call(2.0)])

    def test_cache_write_failure_still_returns_body(self):
        tmp = tempfile.mkdtemp()
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"data": {"notice_content": "<p>正文BODY</p>"}}
        with (
            patch(
                "tradingagents.dataflows.announcement_bodies.requests.get",
                return_value=resp,
            ),
            patch("builtins.open", side_effect=OSError("denied")),
        ):
            bodies = announcement_bodies.fetch_bodies(["AN5"], tmp, retries=0)
        self.assertEqual(bodies, {"AN5": "正文BODY"})


# ---------------------------------------------------------------------------
# Edge cases — dedup_announcements identity fallbacks (empty input / no-key
# rows / older-dup / url fallback / title+date fallback)
# ---------------------------------------------------------------------------

class DedupAnnouncementsEdgeCaseTests(unittest.TestCase):
    def test_empty_rows_returns_empty_lists(self):
        kept, dropped = announcement_dedup.dedup_announcements(
            [], max_per_type=3, series_types=("调研活动",)
        )
        self.assertEqual(kept, [])
        self.assertEqual(dropped, [])

    def test_rows_without_identity_never_deduped(self):
        rows = [
            {"公告标题": "", "公告日期": "2026-09-20"},
            {"公告标题": "有标题无日期", "公告日期": ""},
        ]
        kept, dropped = announcement_dedup.dedup_announcements(
            rows, max_per_type=3, series_types=("调研活动",)
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_older_duplicate_after_newer_keeps_first(self):
        rows = [
            {"_art_code": "AN1", "公告类型": "其他", "公告日期": "2026-09-22", "公告标题": "新在前"},
            {"_art_code": "AN1", "公告类型": "其他", "公告日期": "2026-09-20", "公告标题": "旧在后"},
        ]
        kept, dropped = announcement_dedup.dedup_announcements(
            rows, max_per_type=3, series_types=("调研活动",)
        )
        self.assertEqual([r["公告标题"] for r in kept], ["新在前"])
        self.assertEqual([r["公告标题"] for r in dropped], ["旧在后"])

    def test_url_fallback_identity(self):
        url = "https://data.eastmoney.com/notices/detail/002594/AN2026092001001.html"
        rows = [
            {"网址": url, "公告日期": "2026-09-20", "公告标题": "旧"},
            {"网址": url, "公告日期": "2026-09-22", "公告标题": "新"},
        ]
        kept, dropped = announcement_dedup.dedup_announcements(
            rows, max_per_type=3, series_types=("调研活动",)
        )
        self.assertEqual([r["公告标题"] for r in kept], ["新"])
        self.assertEqual([r["公告标题"] for r in dropped], ["旧"])

    def test_title_date_fallback_identity(self):
        rows = [
            {"公告标题": "相同标题", "公告日期": "2026-09-22"},
            {"公告标题": "相同标题", "公告日期": "2026-09-22"},
            {"公告标题": "相同标题", "公告日期": "2026-09-23"},
        ]
        kept, dropped = announcement_dedup.dedup_announcements(
            rows, max_per_type=3, series_types=("调研活动",)
        )
        self.assertEqual([r["公告日期"] for r in kept], ["2026-09-22", "2026-09-23"])
        self.assertEqual([r["公告日期"] for r in dropped], ["2026-09-22"])

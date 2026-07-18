"""Tests for akshare_common.py — retry, timeout, and exception handling."""

from __future__ import annotations

import json
import os
import time
from unittest import TestCase

import pytest


@pytest.mark.unit
class TestAkshareRetryTimeout(TestCase):
    """P1-9: akshare calls must time out and json decode errors must retry."""

    def test_short_timeout_raises_timeout_error(self):
        from tradingagents.dataflows.akshare_common import _akshare_retry

        os.environ["AKSHARE_TIMEOUT"] = "0.05"
        try:
            with pytest.raises(TimeoutError):
                _akshare_retry(
                    lambda: time.sleep(1.0),
                    max_retries=0,
                    base_delay=0.0,
                )
        finally:
            os.environ.pop("AKSHARE_TIMEOUT", None)

    def test_json_decode_error_retries_then_succeeds(self):
        from tradingagents.dataflows.akshare_common import _akshare_retry

        calls = []

        def func():
            calls.append(1)
            if len(calls) < 2:
                raise json.JSONDecodeError("Expecting value", "", 0)
            return "ok"

        result = _akshare_retry(func, max_retries=3, base_delay=0.0)
        assert result == "ok"
        assert len(calls) == 2

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

    def test_call_with_timeout_runs_in_worker_thread(self):
        # P0 regression: batch concurrent mode runs akshare calls in worker
        # threads, where arming a SIGALRM handler raises "signal only works in
        # main thread of the main interpreter". _call_with_timeout must fall
        # back to the thread-safe path instead of crashing there.
        import threading

        from tradingagents.dataflows.akshare_common import _call_with_timeout

        box: dict[str, object] = {}

        def worker() -> None:
            try:
                box["result"] = _call_with_timeout(lambda: "ok", timeout_seconds=5.0)
            except Exception as exc:
                box["error"] = exc

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert "error" not in box, f"worker thread errored: {box.get('error')!r}"
        assert box["result"] == "ok"

    def test_timeout_still_fires_in_worker_thread(self):
        # The worker-thread fallback must still enforce the timeout, so long
        # concurrent akshare calls stay bounded.
        import concurrent.futures
        import threading

        from tradingagents.dataflows.akshare_common import _call_with_timeout

        box: dict[str, object] = {}

        def worker() -> None:
            try:
                _call_with_timeout(lambda: time.sleep(1.0), timeout_seconds=0.05)
            except Exception as exc:
                box["error"] = exc

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert isinstance(box.get("error"), concurrent.futures.TimeoutError)

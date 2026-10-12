#!/usr/bin/env python
"""Update the Xueqiu xq_a_token used by the realtime snapshot / report_auditor.

Usage:
    uv run python scripts/update_xq_token.py "0e4ab0..."            # validate + write .env
    uv run python scripts/update_xq_token.py "xq_a_token=0e4ab0..." # cookie form also accepted
    uv run python scripts/update_xq_token.py "0e4ab0..." --no-persist  # this process only

Where to find the token: log in at xueqiu.com in a browser, open DevTools →
Application → Cookies → https://xueqiu.com, and copy the value of xq_a_token.

The token is validated live against Xueqiu's quote endpoint BEFORE being
written; an invalid or already-expired paste aborts with exit code 1 so a
bad token never reaches .env.
"""

from __future__ import annotations

import argparse
import sys

from tradingagents.dataflows.xq_token import update_xq_token


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "token",
        help="xq_a_token 值（裸值或 Cookie 形式 'xq_a_token=...;' 均可）",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="只设置本进程环境变量，不写入 .env",
    )
    args = parser.parse_args()

    try:
        token = update_xq_token(args.token, persist=not args.no_persist)
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print(f"✓ xq_a_token 已通过雪球实测探针并更新：{token[:6]}…{token[-4:]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

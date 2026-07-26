#!/usr/bin/env python
"""Environment doctor: read-only self-check for TradingAgents.

Usage:
    uv run python scripts/doctor.py            # offline checks
    uv run python scripts/doctor.py --network  # also probe akshare connectivity

Checks are read-only and never mutate state. Exit code 0 = all required
checks passed (warnings allowed), 1 = at least one required check failed.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# At least one of these must be set for the LLM pipeline to run.
LLM_KEY_ENV_VARS = [
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_CN_API_KEY",
    "ZHIPU_API_KEY",
    "ZHIPU_CN_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "OPENROUTER_API_KEY",
    "SENSENOVA_API_KEY",
    "MIMO_API_KEY",
    "KIMI_API_KEY",
    "AGNES_API_KEY",
    "MODELSCOPE_API_KEY",
    "NVIDIA_API_KEY",
]

CORE_IMPORTS = ["langchain_core", "langgraph", "pandas", "akshare", "yfinance", "rich", "typer"]

_failures = 0


def _report(status: str, name: str, detail: str = "") -> None:
    symbol = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}[status]
    line = f"{symbol} {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if status == "fail":
        global _failures
        _failures += 1


def check_python() -> None:
    if sys.version_info >= (3, 10):  # noqa: UP036 - doctor must still report on unsupported interpreters
        _report("ok", "Python 版本", f"{sys.version.split()[0]}")
    else:
        _report("fail", "Python 版本", f"{sys.version.split()[0]}，需要 >= 3.10")


def check_env_file() -> None:
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        _report("ok", ".env 文件", str(env_path))
    else:
        _report("fail", ".env 文件", "不存在，请从 .env.example 复制并填写密钥")
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except ImportError:
        _report("warn", "python-dotenv", "未安装，跳过 .env 加载（uv sync 可修复）")


def check_llm_keys() -> None:
    present = [k for k in LLM_KEY_ENV_VARS if os.getenv(k)]
    if present:
        _report("ok", "LLM API 密钥", f"已配置 {len(present)} 个（如 {present[0]}）")
    else:
        _report("fail", "LLM API 密钥", "未发现任何已配置的提供商密钥")


def check_imports() -> None:
    missing = []
    for mod in CORE_IMPORTS:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        _report("fail", "核心依赖导入", f"缺失：{', '.join(missing)}（运行 uv sync）")
    else:
        _report("ok", "核心依赖导入", f"{len(CORE_IMPORTS)} 个模块全部可用")


def check_quant_db() -> None:
    """quant_core.db 缺失只降级（akshare 回退仍可用），不算失败。"""
    db_path = os.getenv("QUANT_DB_PATH", os.path.expanduser("~/Code/quant_data/quant_core.db"))
    if not os.path.exists(db_path):
        _report("warn", "quant_core.db", f"未找到 {db_path}，将回退 akshare 实时抓取")
        return
    try:
        with sqlite3.connect(db_path) as conn:
            tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        _report("ok", "quant_core.db", f"{db_path}（{tables} 张表）")
    except sqlite3.Error as exc:
        _report("warn", "quant_core.db", f"存在但无法读取：{exc}")


def check_pricing_yaml() -> None:
    path = REPO_ROOT / "pricing.yaml"
    if path.exists():
        _report("ok", "pricing.yaml", "存在")
    else:
        _report("warn", "pricing.yaml", "缺失，成本统计不可用")


def check_akshare_network() -> None:
    try:
        import akshare as ak

        from tradingagents.dataflows.akshare_common import no_proxy

        with no_proxy():
            df = ak.tool_trade_date_hist_sina()
        _report("ok", "akshare 网络连通", f"取得交易日历 {len(df)} 行")
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        _report("fail", "akshare 网络连通", f"{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", action="store_true", help="额外探测 akshare 网络连通性")
    args = parser.parse_args()

    print(f"TradingAgents doctor（仓库：{REPO_ROOT}）\n")
    check_python()
    check_env_file()
    check_llm_keys()
    check_imports()
    check_quant_db()
    check_pricing_yaml()
    if args.network:
        check_akshare_network()

    print()
    if _failures:
        print(f"结论：{_failures} 项必需检查未通过。")
        return 1
    print("结论：环境就绪。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

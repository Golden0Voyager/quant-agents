#!/usr/bin/env python3
"""
MiMo API 性能基准测试

测试不同 worker 并发数下的吞吐量和延迟，找出性能瓶颈。

用法:
    uv run python scripts/benchmark_workers.py
    uv run python scripts/benchmark_workers.py --tickers AAPL,600519,NVDA --workers 1,2,3
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from cli.stats_handler import StatsCallbackHandler
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph


def run_single_benchmark(
    ticker: str,
    config: dict,
    worker_id: int = 0,
) -> dict[str, Any]:
    """Run a single ticker analysis and collect timing data.

    Returns dict with ticker, total_time, node_timings, stats, error.
    """
    result: dict[str, Any] = {
        "ticker": ticker,
        "worker_id": worker_id,
        "error": None,
    }

    stats = StatsCallbackHandler()
    callbacks = [stats]

    # Initialise before the try so the error path always has a meaningful
    # start time (previously a failure before `t0` binding reported None).
    t0 = time.perf_counter()

    try:
        graph = TradingAgentsGraph(
            debug=False, config=config, callbacks=callbacks
        )
        final_state, decision = graph.propagate(ticker, datetime.now().strftime("%Y-%m-%d"))
        total_time = time.perf_counter() - t0

        result["total_time"] = round(total_time, 2)
        result["node_timings"] = graph.node_timings
        result["total_stream_time"] = getattr(graph, "total_stream_time", None)
        result["llm_calls"] = stats.llm_calls
        result["tool_calls"] = stats.tool_calls
        result["tokens_in"] = stats.tokens_in
        result["tokens_out"] = stats.tokens_out
        result["decision"] = str(decision)[:200] if decision else None

    except Exception as e:
        result["error"] = str(e)
        result["total_time"] = round(time.perf_counter() - t0, 2)

    return result


def benchmark_sequential(
    tickers: list[str], config: dict
) -> list[dict[str, Any]]:
    """Run tickers one by one."""
    results = []
    for i, ticker in enumerate(tickers):
        print(f"  [{i+1}/{len(tickers)}] 分析 {ticker}...")
        r = run_single_benchmark(ticker, config, worker_id=0)
        status = f"✓ {r['total_time']}s" if not r["error"] else f"✗ {r['error']}"
        print(f"         {status} | LLM调用: {r.get('llm_calls', '?')} | Tokens: {r.get('tokens_in', 0)}+{r.get('tokens_out', 0)}")
        results.append(r)
    return results


def benchmark_parallel(
    tickers: list[str], config: dict, workers: int
) -> list[dict[str, Any]]:
    """Run tickers concurrently with ThreadPoolExecutor."""
    results = [None] * len(tickers)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_idx = {}
        for i, ticker in enumerate(tickers):
            future = executor.submit(run_single_benchmark, ticker, config, worker_id=i % workers)
            future_to_idx[future] = i

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                r = future.result()
            except Exception as e:
                r = {"ticker": tickers[idx], "error": str(e)}
            results[idx] = r
            ticker = r.get("ticker", tickers[idx])
            status = f"✓ {r.get('total_time', '?')}s" if not r.get("error") else f"✗ {r.get('error')}"
            print(f"  完成 {ticker}: {status}")

    return results


def analyze_results(
    all_results: dict[int, list[dict[str, Any]]],
    wall_times: dict[int, float] | None = None,
) -> None:
    """Print comparison table and bottleneck analysis.

    ``wall_times`` maps worker count -> measured wall-clock seconds for that
    phase. Per-ticker ``max(total_time)`` systematically understates batch
    duration (it ignores inter-ticker gaps and the slowest path), so real
    wall-clock is preferred whenever available.
    """
    wall_times = wall_times or {}
    print("\n" + "=" * 80)
    print("📊 基准测试结果")
    print("=" * 80)

    # Summary table
    print(f"\n{'Workers':>8} | {'总耗时':>8} | {'平均/只':>8} | {'吞吐量':>10} | {'成功':>4} | {'失败':>4}")
    print("-" * 60)

    for workers, results in sorted(all_results.items()):
        total_time = wall_times.get(workers) or max(
            (r["total_time"] for r in results if r.get("total_time")), default=0
        )
        avg_time = total_time / len(results) if results else 0
        success = sum(1 for r in results if not r.get("error"))
        failed = len(results) - success
        throughput = len(results) / total_time if total_time > 0 else 0

        print(
            f"{workers:>8} | {total_time:>7.1f}s | {avg_time:>7.1f}s | "
            f"{throughput:>8.2f}/s | {success:>4} | {failed:>4}"
        )

    # Per-node timing breakdown (from workers=1 run)
    if 1 in all_results:
        print("\n" + "=" * 80)
        print("⏱  逐节点耗时分布 (workers=1)")
        print("=" * 80)

        node_stats: dict[str, list[float]] = {}
        for r in all_results[1]:
            if r.get("error") or not r.get("node_timings"):
                continue
            for entry in r["node_timings"]:
                name = entry["node"]
                dur = entry.get("duration_s")
                if dur is not None:
                    node_stats.setdefault(name, []).append(dur)

        if node_stats:
            print(f"\n{'节点':<30} | {'平均':>7} | {'最大':>7} | {'调用次数':>8}")
            print("-" * 60)
            sorted_nodes = sorted(
                node_stats.items(), key=lambda x: max(x[1]), reverse=True
            )
            for name, durations in sorted_nodes:
                avg_d = sum(durations) / len(durations)
                max_d = max(durations)
                print(f"{name:<30} | {avg_d:>6.2f}s | {max_d:>6.2f}s | {len(durations):>8}")

            # Identify top 3 bottlenecks
            top3 = sorted_nodes[:3]
            print("\n🔥 瓶颈 TOP 3:")
            for i, (name, durations) in enumerate(top3, 1):
                avg_d = sum(durations) / len(durations)
                print(f"  {i}. {name} — 平均 {avg_d:.2f}s, 最大 {max(durations):.2f}s")

    # Scaling efficiency
    if 1 in all_results and len(all_results) > 1:
        print("\n" + "=" * 80)
        print("📈 并发扩展效率")
        print("=" * 80)
        baseline_time = wall_times.get(1) or max(
            (r["total_time"] for r in all_results[1] if r.get("total_time")),
            default=None,
        )
        if baseline_time is None:
            print("  ⚠ 无有效 baseline 数据，跳过扩展效率分析")
        else:
            for workers, results in sorted(all_results.items()):
                if workers == 1:
                    continue
                batch_time = wall_times.get(workers) or max(
                    (r["total_time"] for r in results if r.get("total_time")),
                    default=None,
                )
                if batch_time is None:
                    print(f"  workers={workers}: 无有效数据，跳过")
                    continue
                ideal = baseline_time / workers
                efficiency = (ideal / batch_time * 100) if batch_time > 0 else 0
                print(
                    f"  workers={workers}: "
                    f"实际 {batch_time:.1f}s vs 理想 {ideal:.1f}s "
                    f"→ 效率 {efficiency:.0f}%"
                )


def main():
    parser = argparse.ArgumentParser(description="MiMo API 性能基准测试")
    parser.add_argument(
        "--tickers",
        default="AAPL,600519,NVDA",
        help="逗号分隔的 ticker 列表 (默认: AAPL,600519,NVDA)",
    )
    parser.add_argument(
        "--workers",
        default="1,2,3",
        help="逗号分隔的 worker 数量列表 (默认: 1,2,3)",
    )
    parser.add_argument(
        "--skip-data-check",
        action="store_true",
        help="跳过数据源可用性检查",
    )
    args = parser.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        print("❌ --tickers 为空")
        sys.exit(1)
    try:
        worker_counts = [int(w.strip()) for w in args.workers.split(",") if w.strip()]
    except ValueError:
        print(f"❌ --workers 含非数字项: {args.workers!r}（应为逗号分隔的正整数）")
        sys.exit(1)
    if not worker_counts or any(w < 1 for w in worker_counts):
        print(f"❌ --workers 必须是正整数列表，当前: {worker_counts}")
        sys.exit(1)

    # Build config
    config = DEFAULT_CONFIG.copy()

    # Check API key
    if not os.environ.get("MIMO_API_KEY"):
        print("❌ MIMO_API_KEY 未设置。请在 .env 中配置。")
        sys.exit(1)

    print("=" * 80)
    print("🚀 MiMo API 性能基准测试")
    print("=" * 80)
    print(f"  Provider: {config['llm_provider']}")
    print(f"  Deep Think: {config['deep_think_llm']}")
    print(f"  Quick Think: {config['quick_think_llm']}")
    print(f"  Tickers: {', '.join(tickers)}")
    print(f"  Worker 测试: {worker_counts}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Phase 1: Single-ticker baseline with per-node timing
    print("─" * 80)
    print("📊 Phase 1: 单股票逐节点计时 (baseline)")
    print("─" * 80)
    single_config = config.copy()
    single_config["selected_analysts"] = single_config.get(
        "selected_analysts", ["market", "news", "fundamentals", "social"]
    )
    single_config["max_debate_rounds"] = single_config.get("max_debate_rounds", 1)
    single_config["max_risk_discuss_rounds"] = single_config.get("max_risk_discuss_rounds", 1)

    # Run with workers=1 for baseline
    print(f"\n  使用 {len(tickers)} 只股票 × workers=1:")
    wall_start = time.perf_counter()
    results_1 = benchmark_sequential(tickers, single_config)
    wall_times = {1: time.perf_counter() - wall_start}

    all_results = {1: results_1}

    # Phase 2: Multi-worker scaling test
    for workers in worker_counts:
        if workers == 1:
            continue
        print(f"\n  使用 {len(tickers)} 只股票 × workers={workers}:")
        wall_start = time.perf_counter()
        results_n = benchmark_parallel(tickers, single_config, workers)
        wall_times[workers] = time.perf_counter() - wall_start
        all_results[workers] = results_n

    # Phase 3: Analysis
    analyze_results(all_results, wall_times)

    # Save raw results
    output_path = Path("reports") / f"benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Make JSON serializable
    serializable = {}
    for w, results in all_results.items():
        serializable[str(w)] = results

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 原始数据已保存: {output_path}")


if __name__ == "__main__":
    main()

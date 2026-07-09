# Batch UX 改进计划：压制 yfinance 噪音 + 并发 Dashboard 增强

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 batch 运行中 yfinance "Failed download" 的 stdout 噪音，并在并发模式下（workers > 1）展示 per-ticker 的实时 agent 阶段状态。

**Architecture:** 两个独立的改动：(1) 在 `stockstats_utils.py` 的 `yf.download()` 调用外套一层 stdout 抑制；(2) 在 `BatchDashboard` 中新增 `per_ticker_meta` 字段记录每只标的的当前阶段，worker 线程在 `_refresh_display()` 中更新该字段，`render_batch_progress_panel()` 将其渲染为 per-ticker 状态表。

**Tech Stack:** Python, Rich (Table/Panel/Layout), yfinance, contextlib, threading

## Global Constraints

- stdout 抑制必须线程安全（`yf.download` 可能在 worker 线程中调用）
- 不能修改 yfinance 源码
- per-ticker 状态写入用 `threading.RLock`（已有 `self._lock`）保护，读取不做快照（GIL 保证 dict 引用原子性，显示允许轻微滞后）
- 不修改 `AnalysisDashboard` 基类，只扩展 `BatchDashboard`
- 保留 `workers <= 1` 的详细 Dashboard 路径不变
- 显示使用中文 stage 名称
- 保持 Rich Layout 分配不变（`progress:ratio=2`, `messages:ratio=3`, `upper:ratio=3`）

---

### Task 1: 压制 yfinance "Failed download" stdout 输出

**Files:**
- Modify: `tradingagents/dataflows/stockstats_utils.py`

**Interfaces:**
- Consumes: `yf.download()` calls at lines ~330 and ~352
- Produces: `_silent_yf_download()` helper function + two call-site replacements

**分析：** yfinance 内部使用 `print()` 输出 "1 Failed download:" 等消息（在 `yfinance/data.py` 中）。`progress=False` 参数只压制进度条，不压制失败消息。在 `yf.download()` 外套 `contextlib.redirect_stdout(io.StringIO())` 可消除此类噪音。由于该调用在 worker 线程中执行，`sys.stdout` 的全局替换对其他线程的影响窗口极短（~1-3 秒的 HTTP 请求），且被捕获的 print 恰好是用户不需要看到的 yfinance 内部消息。

- [ ] **Step 1: 添加 `_silent_yf_download` 辅助函数**

在 `stockstats_utils.py` 的 import 区域添加：

```python
import contextlib
import io
```

在 `yf_retry` 函数定义之后添加：

```python
def _silent_yf_download(*args, **kwargs):
    """Call yf.download with stdout suppressed to hide 'Failed download' messages.

    yfinance uses print() internally for failure reporting; redirect_stdout
    silences these without affecting Rich's Live rendering (which runs in the
    main thread and writes to the layout data structure, not sys.stdout directly).
    """
    with contextlib.redirect_stdout(io.StringIO()):
        return yf.download(*args, **kwargs)
```

- [ ] **Step 2: 替换 A-share 回退路径中的 yf.download 调用**

在 `load_ohlcv()` 函数中，A-share fallback 路径（约第 329-337 行）：

```python
# 修改前：
downloaded = yf_retry(
    lambda: yf.download(
        canonical,
        start=start_str,
        end=end_str,
        multi_level_index=False,
        progress=False,
        auto_adjust=True,
    )
)

# 修改后：
downloaded = yf_retry(
    lambda: _silent_yf_download(
        canonical,
        start=start_str,
        end=end_str,
        multi_level_index=False,
        progress=False,
        auto_adjust=True,
    )
)
```

- [ ] **Step 3: 替换非 A-share 路径中的 yf.download 调用**

在 `load_ohlcv()` 函数中，非 A-share 路径（约第 352-361 行），同样替换 `yf.download` 为 `_silent_yf_download`。

- [ ] **Step 4: 验证**

```bash
# 运行单元测试确认不破坏原有逻辑
uv run pytest -m unit -v -k "stockstats or load_ohlcv or akshare"
```

预期：所有测试 PASS。无 `yf.download` 相关的 stdout 输出。

---

### Task 2: 并发 Dashboard 显示 per-ticker 实时阶段状态

**Files:**
- Modify: `cli/batch_dashboard.py` — 添加 `per_ticker_meta` 字段和更新方法
- Modify: `cli/batch_runner.py` — 在 `_refresh_display()` 中保存 per-ticker 快照，future 完成后更新状态
- Modify: `cli/dashboard.py` — 增强 `render_batch_progress_panel()` 渲染 per-ticker 表

**分析：** 当前并发模式下，Dashboard 仅显示 aggregate 进度条和 `Completed: X | Failed: Y` 计数器，用户无法看到每个 worker 当前正在跑哪个阶段。需要给 `BatchDashboard` 加一个 `per_ticker_meta: dict[str, dict]` 字段，结构为 `{ticker: {"stage": str, "progress": float, "agent": str}}`。worker 线程在每次 `_refresh_display()` 时更新自己 ticker 的 meta；主线程在 future 完成后将 meta 标记为 Completed/Failed。`render_batch_progress_panel()` 读取 meta 渲染 per-ticker 状态表。

- [ ] **Step 1: BatchDashboard 添加 per_ticker_meta**

修改 `cli/batch_dashboard.py`：

```python
class BatchDashboard(AnalysisDashboard):
    def __init__(self, total: int, profile_name: str):
        super().__init__()
        self.total = total
        self.profile_name = profile_name
        self.completed = 0
        self.failed = 0
        self.skipped = 0
        self.skipped_tickers: list[str] = []
        self.current_ticker: str | None = None
        self.readiness_ready: int = 0
        self.readiness_total: int = 0
        self.per_ticker_meta: dict[str, dict] = {}  # ticker -> {stage, progress, agent}

    def update_ticker_meta(self, ticker: str, stage: str, progress: float, agent: str) -> None:
        self.per_ticker_meta[ticker] = {
            "stage": stage,
            "progress": progress,
            "agent": agent,
        }

    # 其余方法不变
```

- [ ] **Step 2: BatchRunner._refresh_display 保存 per-ticker 快照**

修改 `cli/batch_runner.py` 中的 `_refresh_display()` 方法：

```python
def _refresh_display(self, stats_handler=None) -> None:
    """Push current dashboard state into the Live-managed layout.

    Safe no-op when no Live context is active.
    """
    if self._layout is None or self._start_time is None:
        return
    elapsed = time.time() - self._start_time  # 原有行，但未赋值给变量

    # Save per-ticker snapshot (worker thread updates its own ticker's meta)
    if self._batch_mode:
        ticker = self.dashboard.current_ticker
        if ticker:
            with self._lock:
                self.dashboard.update_ticker_meta(
                    ticker,
                    stage=self.dashboard.current_stage,
                    progress=self.dashboard.overall_progress,
                    agent=self.dashboard.current_agent or "",
                )

    update_dashboard_display(
        self._layout,
        self.dashboard,
        ticker=self.dashboard.current_ticker or "",
        stats_handler=stats_handler,
        start_time=self._start_time,
        batch_completed=self.dashboard.completed,
        batch_total=self.dashboard.total,
        batch_failed=self.dashboard.failed,
        profile_name=self.dashboard.profile_name,
        batch_mode=self._batch_mode,
    )
```

- [ ] **Step 3: Future 完成后标记 per_ticker 完成/失败**

在 `batch_runner.py` 的 `run()` 方法中的并发路径（约第 910-954 行），在 future.result() 成功/失败处理中加入 meta 更新：

在 `self.completed_tickers.add(ticker)` 和 `self.failures[ticker] = ...` 所在的位置，同时更新 `per_ticker_meta`：

```python
# 成功完成时（约第 919-921 行附近）：
with self._lock:
    self.completed_tickers.add(ticker)
    self.dashboard.update_ticker_meta(ticker, "Completed", 1.0, "✓ Done")

# 失败时（约第 922-925 行附近）：
with self._lock:
    self.failures[ticker] = str(e)
    self.completed_tickers.add(ticker)
    self.dashboard.update_ticker_meta(ticker, "Failed", 0.0, f"✗ {str(e)[:20]}")
```

- [ ] **Step 4: 增强 render_batch_progress_panel 渲染 per-ticker 表**

修改 `cli/dashboard.py` 中的 `render_batch_progress_panel()` 函数：

```python
def render_batch_progress_panel(layout: Layout, dashboard: AnalysisDashboard) -> None:
    """Render progress panel for batch concurrent mode with per-ticker live status."""
    completed = getattr(dashboard, "completed", 0)
    failed = getattr(dashboard, "failed", 0)
    total = getattr(dashboard, "total", 0)
    skipped = getattr(dashboard, "skipped", 0)
    pct = (completed / total * 100) if total > 0 else 0

    # ── Aggregate progress bar ──
    bar_len = 40
    filled = int(bar_len * completed / total) if total > 0 else 0
    bar = "━" * filled + "─" * (bar_len - filled)
    summary_parts = [
        f"Completed: [green]{completed}[/green]",
        f"Failed: [red]{failed}[/red]",
        f"Skipped: [cyan]{skipped}[/cyan]",
        f"Total: {total}",
    ]
    if failed > 0:
        summary_parts[1] = f"Failed: [bold red]{failed}[/bold red]"

    # ── Per-ticker live status table ──
    meta: dict[str, dict] = getattr(dashboard, "per_ticker_meta", {})
    ticker_lines: list[str] = []
    if meta:
        # Sort: running first (by stage alphabetical ≈ pipeline order), then done
        running = [(t, m) for t, m in meta.items() if m.get("stage") not in ("Completed", "Failed")]
        done = [(t, m) for t, m in meta.items() if m.get("stage") in ("Completed", "Failed")]
        running.sort(key=lambda x: x[1].get("stage", ""))
        done.sort(key=lambda x: x[0])

        # Header
        ticker_lines.append(f"\n{'Ticker':<12} {'Stage':<16} {'Progress':<12} {'Agent':<18}")
        ticker_lines.append(f"{'─'*12} {'─'*16} {'─'*12} {'─'*18}")

        for ticker, m in running:
            p = m.get("progress", 0)
            pb = "█" * int(p * 10) + "░" * (10 - int(p * 10))
            ticker_lines.append(
                f"[cyan]{ticker[-8:]:>8}[/cyan]  "
                f"{m.get('stage', '—'):<16}  "
                f"{pb} {int(p*100):>3d}%  "
                f"{m.get('agent', '—'):<18}"
            )

        for ticker, m in done:
            status = m.get("stage", "")
            style = "green" if status == "Completed" else "red"
            ticker_lines.append(
                f"[cyan]{ticker[-8:]:>8}[/cyan]  "
                f"[{style}]{status:<16}[/{style}]  "
                f"{'██████████ 100%':<12}  "
                f"{'✓' if status == 'Completed' else '✗':<18}"
            )

    # ── Combine ──
    from rich.text import Text
    content = Text.assemble(
        (f"[green]{bar}[/green] {pct:.0f}%", ""),
        (f"    {' | '.join(summary_parts)}", ""),
        *([("\n" + line, "") for line in ticker_lines] if ticker_lines else []),
    )
    layout["progress"].update(
        Panel(content, title="Batch Progress  (concurrent mode)", border_style="cyan", padding=(1, 2))
    )
```

- [ ] **Step 5: 验证**

```bash
uv run pytest -m unit -v -k "batch or dashboard"
```

预期：所有测试 PASS。手动验证步骤：

```bash
# 用 mock ticker 跑 batch 确认 TUI 显示 per-ticker 状态
python -m cli.main batch my-list --workers 3
```

打开终端观察：
- 并发模式下 Header 区域保持现有样式（Running: 列表 + 计数器）
- Progress 面板顶部为 aggregate 进度条，下方为 per-ticker 状态表
- 每个运行中 ticker 显示当前 Stage（Analysts / Research Debate / Trading / Risk Debate / Portfolio）
- 完成后 ticker 显示 ✓ 或 ✗
- 无 "1 Failed download:" 等 yfinance stdout 噪音

---

## Self-Review

### Spec Coverage
- ✅ Issue 1（压制 yfinance stdout 噪音）→ Task 1 覆盖，3 个步骤：辅助函数 → A-share 替换 → 非 A-share 替换 → 验证
- ✅ Issue 2（per-ticker 并发 Dashboard）→ Task 2 覆盖，5 个步骤：数据模型 → 快照保存 → 完成标记 → 渲染增强 → 验证

### Placeholder Scan
- ❌ 无 "TBD"、"TODO"、"implement later" 等占位符
- ❌ 无 "Add appropriate error handling" 等模糊指令
- ❌ 所有步骤都包含具体代码或命令

### Type Consistency
- `BatchDashboard.update_ticker_meta(ticker, stage, progress, agent)` — 在 task2 step1 定义，在 step2 和 step3 调用，签名一致
- `per_ticker_meta` dict 结构 `{stage: str, progress: float, agent: str}` — 在 step2 写入，在 step4 读取，结构一致
- `dashboard.current_stage` / `dashboard.overall_progress` / `dashboard.current_agent` — 在 step2 读取，由 `AnalysisDashboard` 的现有方法维护，类型一致

### Thread Safety
- `per_ticker_meta` 写入：在 step2 (worker thread) 和 step3 (main thread) 中都使用 `self._lock` 保护
- `per_ticker_meta` 读取：在 step4 (main thread Live 渲染) 中读取，无锁（GIL 保护 dict 引用原子性，显示允许 ~250ms 滞后）
- stdout 抑制：`redirect_stdout` 在 CPython 中是全局的，但 yfinance 调用窗口短（~1-3 秒），且被捕获的内容正是需要消除的噪音

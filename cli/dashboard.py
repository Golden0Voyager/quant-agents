"""通用 Dashboard 状态层与渲染函数。

为单股分析和批量分析提供统一的状态容器 (AnalysisDashboard) 和渲染管线，
消除 cli/main.py 与 cli/batch_dashboard.py 之间的重复代码。
"""

from __future__ import annotations

import datetime
from collections import deque

from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

console = Console()

# ── Analyst mappings ──────────────────────────────────────────────

ANALYST_ORDER = ["market", "social", "news", "fundamentals", "governance", "industry"]

ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Social Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
    "governance": "Governance Analyst",
    "industry": "Industry Analyst",
}

ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
    "governance": "governance_report",
    "industry": "industry_report",
}

# ── Stage definitions ─────────────────────────────────────────────

STAGES = [
    ("Analysts", 0.0),
    ("Research Debate", 0.30),
    ("Trading", 0.50),
    ("Risk Debate", 0.60),
    ("Portfolio", 0.80),
]

SECTION_TITLES = {
    "market_report": "Market Analysis",
    "sentiment_report": "Social Sentiment",
    "news_report": "News Analysis",
    "fundamentals_report": "Fundamentals Analysis",
    "governance_report": "Governance Analysis",
    "industry_report": "Industry Analysis",
    "investment_plan": "Research Team Decision",
    "trader_investment_plan": "Trading Team Plan",
    "final_trade_decision": "Portfolio Management Decision",
}

# ── AnalysisDashboard ─────────────────────────────────────────────

class AnalysisDashboard:
    """统一的状态容器，替代 MessageBuffer 与 BatchDashboard 的共有部分。"""

    FIXED_AGENTS = {
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    REPORT_SECTIONS = {
        "market_report": ("market", "Market Analyst"),
        "sentiment_report": ("social", "Social Analyst"),
        "news_report": ("news", "News Analyst"),
        "fundamentals_report": ("fundamentals", "Fundamentals Analyst"),
        "governance_report": ("governance", "Governance Analyst"),
        "industry_report": ("industry", "Industry Analyst"),
        "investment_plan": (None, "Research Manager"),
        "trader_investment_plan": (None, "Trader"),
        "final_trade_decision": (None, "Portfolio Manager"),
    }

    def __init__(self, max_length: int = 100):
        self.messages: deque = deque(maxlen=max_length)
        self.tool_calls: deque = deque(maxlen=max_length)
        self.agent_status: dict[str, str] = {}
        self.report_sections: dict[str, str | None] = {}
        self.selected_analysts: list[str] = []
        self.current_report: str | None = None
        self.final_report: str | None = None
        self.current_agent: str | None = None
        self.current_stage: str = "Analysts"
        self.stage_progress: float = 0.0
        self.overall_progress: float = 0.0
        self._tool_call_active: dict[str, bool] = {}

    # ── State mutations ───────────────────────────────────────────

    def init_for_analysis(self, selected_analysts: list[str], clear_messages: bool = True) -> None:
        self.selected_analysts = [a.lower() for a in selected_analysts]
        self.agent_status = {}
        for key in self.selected_analysts:
            if key in ANALYST_AGENT_NAMES:
                self.agent_status[ANALYST_AGENT_NAMES[key]] = "pending"
        for team_agents in self.FIXED_AGENTS.values():
            for agent in team_agents:
                self.agent_status[agent] = "pending"

        self.report_sections = {}
        for section, (analyst_key, _) in self.REPORT_SECTIONS.items():
            if analyst_key is None or analyst_key in self.selected_analysts:
                self.report_sections[section] = None

        self.current_report = None
        self.final_report = None
        self.current_agent = None
        self.current_stage = "Analysts"
        self.stage_progress = 0.0
        self.overall_progress = 0.0
        self._tool_call_active.clear()
        if clear_messages:
            self.messages.clear()
            self.tool_calls.clear()

    def reset_per_stock(self, clear_messages: bool = True) -> None:
        """清空单只股票的运行时状态（batch 切股时使用）。

        Args:
            clear_messages: 是否清空消息和工具调用历史。
                在 batch 模式下设为 False 以保留跨标的的初始化消息。
        """
        self.agent_status.clear()
        self.report_sections.clear()
        self.current_report = None
        self.final_report = None
        self.current_agent = None
        self.current_stage = "Analysts"
        self.stage_progress = 0.0
        self.overall_progress = 0.0
        self._tool_call_active.clear()
        if clear_messages:
            self.messages.clear()
            self.tool_calls.clear()
        self.selected_analysts.clear()

    def add_message(self, msg_type: str, content: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.messages.append((ts, msg_type, content))

    def add_tool_call(self, tool_name: str, args: dict) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.tool_calls.append((ts, tool_name, args))
        if self.current_agent:
            self._tool_call_active[self.current_agent] = True

    def finish_tool_call(self, agent: str | None = None) -> None:
        target = agent or self.current_agent
        if target:
            self._tool_call_active.pop(target, None)

    def update_agent_status(self, agent: str, status: str) -> None:
        self.agent_status[agent] = status
        self.current_agent = agent
        if status != "in_progress":
            self._tool_call_active.pop(agent, None)

    def update_report_section(self, section_name: str, content: str) -> None:
        if section_name in self.report_sections:
            self.report_sections[section_name] = content
            self._update_current_report()
            self._update_final_report()

    def _update_current_report(self) -> None:
        latest_section = None
        latest_content = None
        for section, content in self.report_sections.items():
            if content is not None:
                latest_section = section
                latest_content = content
        if latest_section and latest_content:
            title = SECTION_TITLES.get(latest_section, latest_section)
            self.current_report = f"### {title}\n{latest_content}"
        self._update_final_report()

    def _update_final_report(self) -> None:
        parts = []
        analyst_sections = [
            "market_report",
            "sentiment_report",
            "news_report",
            "fundamentals_report",
            "governance_report",
            "industry_report",
        ]
        if any(self.report_sections.get(s) for s in analyst_sections):
            parts.append("## Analyst Team Reports")
            for s in analyst_sections:
                if self.report_sections.get(s):
                    parts.append(f"### {SECTION_TITLES.get(s, s)}\n{self.report_sections[s]}")

        if investment_plan := self.report_sections.get("investment_plan"):
            parts.append("## Research Team Decision")
            parts.append(investment_plan)

        if trader_plan := self.report_sections.get("trader_investment_plan"):
            parts.append("## Trading Team Plan")
            parts.append(trader_plan)

        if final_decision := self.report_sections.get("final_trade_decision"):
            parts.append("## Portfolio Management Decision")
            parts.append(final_decision)

        self.final_report = "\n\n".join(parts) if parts else None

    def get_completed_reports_count(self) -> int:
        count = 0
        for section in self.report_sections:
            if section not in self.REPORT_SECTIONS:
                continue
            _, finalizing_agent = self.REPORT_SECTIONS[section]
            has_content = self.report_sections.get(section) is not None
            agent_done = self.agent_status.get(finalizing_agent) == "completed"
            if has_content and agent_done:
                count += 1
        return count

    def get_overall_progress_pct(self) -> int:
        return int(self.overall_progress * 100)

    # ── Stage tracking ────────────────────────────────────────────

    def update_stage_from_chunk(
        self,
        chunk: dict,
        max_debate_rounds: int = 1,
        max_risk_rounds: int = 1,
    ) -> None:
        """根据 agent 状态推断当前 pipeline 阶段与进度。

        不直接检查 chunk key 是否存在，因为 LangGraph 的 values 模式
        下每个 chunk 都包含完整状态（包括初始为空的 debate_state），
        会导致一启动就误判到后面阶段。
        """
        # Stage 5: Portfolio
        if self.agent_status.get("Portfolio Manager") == "completed":
            self.current_stage = "Portfolio"
            self.stage_progress = 1.0
            self.overall_progress = 1.0
            return

        # Stage 4: Risk Debate
        if any(
            self.agent_status.get(a) in ("in_progress", "completed")
            for a in ("Aggressive Analyst", "Neutral Analyst", "Conservative Analyst")
        ):
            risk = chunk.get("risk_debate_state") or {}
            self.current_stage = "Risk Debate"
            if risk.get("judge_decision", "").strip():
                self.stage_progress = 0.9
            else:
                roles = ["aggressive", "conservative", "neutral"]
                filled = sum(1 for r in roles if risk.get(f"{r}_history", "").strip())
                self.stage_progress = min(filled / max(1, len(roles)), 0.8)
            self.overall_progress = 0.60 + self.stage_progress * 0.20
            return

        # Stage 3: Trading
        if self.agent_status.get("Trader") in ("in_progress", "completed"):
            self.current_stage = "Trading"
            self.stage_progress = 0.5
            self.overall_progress = 0.55
            return

        # Stage 2: Research Debate
        if any(
            self.agent_status.get(a) in ("in_progress", "completed")
            for a in ("Bull Researcher", "Bear Researcher", "Research Manager")
        ):
            debate = chunk.get("investment_debate_state") or {}
            self.current_stage = "Research Debate"
            if debate.get("judge_decision", "").strip():
                self.stage_progress = 0.9
            else:
                filled = 0
                if debate.get("bull_history", "").strip():
                    filled += 1
                if debate.get("bear_history", "").strip():
                    filled += 1
                self.stage_progress = min(filled / max(1, max_debate_rounds * 2), 0.8)
            self.overall_progress = 0.30 + self.stage_progress * 0.20
            return

        # Stage 1: Analysts
        self.current_stage = "Analysts"
        total = len([a for a in ANALYST_ORDER if a in self.selected_analysts])
        if total == 0:
            self.stage_progress = 0.0
            self.overall_progress = 0.0
            return
        completed = sum(
            1
            for key in ANALYST_ORDER
            if key in self.selected_analysts
            and self.agent_status.get(ANALYST_AGENT_NAMES[key]) == "completed"
        )
        self.stage_progress = completed / total
        self.overall_progress = self.stage_progress * 0.30


# ── Layout ────────────────────────────────────────────────────────

def create_dashboard_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_column(
        Layout(name="upper", ratio=3),
        Layout(name="analysis", ratio=5),
    )
    layout["upper"].split_row(
        Layout(name="progress", ratio=2),
        Layout(name="messages", ratio=3),
    )
    return layout


# ── Rendering helpers ─────────────────────────────────────────────

def _stage_bar(progress: float, width: int = 20) -> str:
    filled = int(progress * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {int(progress * 100)}%"


def _status_cell(status: str, tool_active: bool = False) -> str | Spinner:
    if tool_active:
        return Spinner("dots", text="[cyan]fetching[/cyan]", style="bold cyan")
    if status == "in_progress":
        return Spinner("dots", text="[blue]in_progress[/blue]", style="bold cyan")
    color = {"pending": "yellow", "completed": "green", "error": "red"}.get(status, "white")
    return f"[{color}]{status}[/{color}]"


# ── Render functions ──────────────────────────────────────────────

def render_header(
    layout: Layout,
    dashboard: AnalysisDashboard,
    ticker: str,
    batch_completed: int = 0,
    batch_total: int = 0,
    batch_failed: int = 0,
    profile_name: str | None = None,
    batch_mode: bool = False,
    running_tickers: set[str] | None = None,
) -> None:
    parts = []
    if profile_name:
        parts.append(f"[bold]Batch:[/bold] {profile_name}")
        # Show skipped count if available (BatchDashboard has .skipped)
        skipped = getattr(dashboard, "skipped", 0)
        progress_str = (
            f"{batch_completed}/{batch_total} "
            f"[green]✓[/green] {batch_failed}[red]✗[/red]"
        )
        if skipped:
            progress_str += f" {skipped}[cyan]⏭[/cyan]"
        # Show data readiness pre-check result if available
        readiness_total = getattr(dashboard, "readiness_total", 0)
        if readiness_total > 0:
            r_ready = getattr(dashboard, "readiness_ready", 0)
            color = "green" if r_ready == readiness_total else "yellow"
            progress_str += f" | 预检: [{color}]{r_ready}/{readiness_total}[/{color}]"
        parts.append(progress_str)

        if batch_mode:
            # Batch summary mode: show running tickers instead of a single current ticker
            if running_tickers:
                running_str = ", ".join(sorted(running_tickers)[:10])
                if len(running_tickers) > 10:
                    running_str += f" +{len(running_tickers) - 10} more"
                parts.append(f"Running: [cyan]{running_str}[/cyan]")
            parts.append("Stage: [yellow]Batch Processing[/yellow]")
        else:
            parts.append(f"Current: [cyan]{ticker}[/cyan]")
            parts.append(f"Stage: [yellow]{dashboard.current_stage}[/yellow]")
            parts.append(_stage_bar(dashboard.overall_progress))
    else:
        parts.append(f"[bold]Ticker:[/bold] [cyan]{ticker}[/cyan]")
        parts.append(f"Stage: [yellow]{dashboard.current_stage}[/yellow]")
        parts.append(_stage_bar(dashboard.overall_progress))

    header_text = "  |  ".join(parts)
    layout["header"].update(
        Panel(header_text, border_style="green", padding=(1, 2), expand=True)
    )


def render_batch_progress_panel(layout: Layout, dashboard: AnalysisDashboard) -> None:
    """Render simplified progress panel for batch summary mode."""
    completed = getattr(dashboard, "completed", 0)
    failed = getattr(dashboard, "failed", 0)
    total = getattr(dashboard, "total", 0)
    skipped = getattr(dashboard, "skipped", 0)
    pct = (completed / total * 100) if total > 0 else 0

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

    # Use plain markup strings so Rich parses [green]...[/green] tags
    progress_markup = f"[green]{bar}[/green] {pct:.0f}%    {' | '.join(summary_parts)}"

    meta: dict[str, dict] = getattr(dashboard, "per_ticker_meta", {})
    ticker_lines: list[str] = []
    if meta:
        running = [(t, m) for t, m in meta.items() if m.get("stage") not in ("Completed", "Failed")]
        done = [(t, m) for t, m in meta.items() if m.get("stage") in ("Completed", "Failed")]
        running.sort(key=lambda x: x[1].get("stage", ""))
        done.sort(key=lambda x: x[0])

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

    ticker_markup = "\n".join(ticker_lines) if ticker_lines else ""

    content = f"{progress_markup}\n{ticker_markup}" if ticker_markup else progress_markup
    layout["progress"].update(
        Panel(content, title="Batch Progress  (concurrent mode)", border_style="cyan", padding=(1, 2))
    )


def render_progress_panel(layout: Layout, dashboard: AnalysisDashboard) -> None:
    """渲染 Progress panel：顶部 stage bar + 下方 agent status table。"""
    # Stage bar
    stage_lines = []
    for stage_name, base_weight in STAGES:
        if dashboard.current_stage == stage_name:
            stage_lines.append(f"[bold cyan]→ {stage_name}[/bold cyan]")
        elif base_weight < STAGES[0][1] + (dashboard.overall_progress * 0.01):
            stage_lines.append(f"[dim]  {stage_name}[/dim]")
        else:
            stage_lines.append(f"[dim]  {stage_name}[/dim]")
    stage_text = " → ".join(stage_lines)

    # Agent status table
    table = Table(
        show_header=True,
        header_style="bold magenta",
        box=box.SIMPLE_HEAD,
        padding=(0, 2),
        expand=True,
    )
    table.add_column("Team", style="cyan", justify="center", width=20)
    table.add_column("Agent", style="green", justify="center", width=20)
    table.add_column("Status", style="yellow", justify="center", width=20)

    all_teams = {
        "Analyst Team": [
            "Market Analyst",
            "Social Analyst",
            "News Analyst",
            "Fundamentals Analyst",
            "Governance Analyst",
            "Industry Analyst",
        ],
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    for team, agents in all_teams.items():
        active = [a for a in agents if a in dashboard.agent_status]
        if not active:
            continue
        for idx, agent in enumerate(active):
            status = dashboard.agent_status.get(agent, "pending")
            tool_active = dashboard._tool_call_active.get(agent, False)
            cell = _status_cell(status, tool_active)
            table.add_row(team if idx == 0 else "", agent, cell)
        table.add_row("─" * 20, "─" * 20, "─" * 20, style="dim")

    # Combine stage bar + table in one panel
    Text.assemble(
        Text(stage_text + "\n\n", style="bold"),
    )
    # Use a group or nested table approach
    layout["progress"].update(
        Panel(table, title=f"Progress  {_stage_bar(dashboard.overall_progress, 12)}", border_style="cyan", padding=(1, 2))
    )


def render_messages_panel(layout: Layout, dashboard: AnalysisDashboard, batch_mode: bool = False) -> None:
    table = Table(
        show_header=True,
        header_style="bold magenta",
        expand=True,
        box=box.MINIMAL,
        show_lines=True,
        padding=(0, 1),
    )
    table.add_column("Time", style="cyan", width=8, justify="center")
    table.add_column("Type", style="green", width=10, justify="center")
    table.add_column("Content", style="white", no_wrap=False, ratio=1)

    all_items = []

    if batch_mode:
        # Show messages from the pipeline + batch runner (Agent/Data/Info/System etc.)
        for ts, msg_type, content in dashboard.messages:
            content_str = str(content)[:200] if content else ""
            all_items.append((ts, msg_type, content_str))
        # Also show the latest tool call per agent
        for ts, tool_name, args in dashboard.tool_calls:
            args_str = str(args)
            if len(args_str) > 80:
                args_str = args_str[:77] + "..."
            all_items.append((ts, "Tool", f"{tool_name}: {args_str}"))
    else:
        for ts, tool_name, args in dashboard.tool_calls:
            args_str = str(args)
            if len(args_str) > 80:
                args_str = args_str[:77] + "..."
            all_items.append((ts, "Tool", f"{tool_name}: {args_str}"))
        for ts, msg_type, content in dashboard.messages:
            content_str = str(content)[:200] if content else ""
            all_items.append((ts, msg_type, content_str))

    all_items.sort(key=lambda x: x[0], reverse=True)

    if not batch_mode:
        # Current Focus: messages from current_agent + latest tool call
        focus_items = []
        if dashboard.current_agent:
            for ts, msg_type, content in dashboard.messages:
                if msg_type in ("Agent", "Data"):
                    focus_items.append((ts, msg_type, content[:150]))
                    if len(focus_items) >= 3:
                        break
        if dashboard.tool_calls:
            ts, tool_name, args = dashboard.tool_calls[-1]
            args_str = str(args)
            if len(args_str) > 60:
                args_str = args_str[:57] + "..."
            focus_items.append((ts, "Tool", f"{tool_name}: {args_str}"))

        # Display: focus first, then general messages
        shown = set()
        for ts, msg_type, content in focus_items:
            key = (ts, msg_type, content)
            if key not in shown:
                table.add_row(ts, f"[bold]{msg_type}[/bold]", Text(content, overflow="fold"))
                shown.add(key)

        if focus_items and all_items:
            table.add_row("─" * 8, "─" * 10, "─" * 40, style="dim")

    if all_items:
        for ts, msg_type, content in all_items[:10]:
            if not batch_mode and (ts, msg_type, content) in shown:
                continue
            table.add_row(ts, msg_type, Text(content, overflow="fold"))
    elif not batch_mode:
        table.add_row("-", "等待", "等待运行时消息…", style="dim")

    layout["messages"].update(
        Panel(table, title="Messages & Tools", border_style="blue", padding=(1, 2))
    )


def render_analysis_panel(layout: Layout, dashboard: AnalysisDashboard, batch_mode: bool = False) -> None:
    if batch_mode:
        completed = getattr(dashboard, "completed", 0)
        failed = getattr(dashboard, "failed", 0)
        total = getattr(dashboard, "total", 0)
        layout["analysis"].update(
            Panel(
                f"[bold]Batch Analysis in Progress[/bold]\n\n"
                f"Completed: [green]{completed}[/green]  "
                f"Failed: [red]{failed}[/red]  "
                f"Total: {total}\n\n"
                f"[italic]Detailed per-ticker reports will be available after completion.[/italic]",
                title="Batch Overview",
                border_style="green",
                padding=(1, 2),
            )
        )
    elif dashboard.final_report:
        layout["analysis"].update(
            Panel(
                Markdown(dashboard.final_report),
                title="Accumulated Report",
                border_style="green",
                padding=(1, 2),
            )
        )
    elif dashboard.current_report:
        layout["analysis"].update(
            Panel(
                Markdown(dashboard.current_report),
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        layout["analysis"].update(
            Panel(
                "[italic]Waiting for analysis report...[/italic]",
                title="Report",
                border_style="green",
                padding=(1, 2),
            )
        )


def render_footer(
    layout: Layout,
    dashboard: AnalysisDashboard,
    stats_handler=None,
    start_time: float | None = None,
) -> None:
    agents_completed = sum(
        1 for s in dashboard.agent_status.values() if s == "completed"
    )
    agents_total = len(dashboard.agent_status)
    reports_completed = dashboard.get_completed_reports_count()
    reports_total = len(dashboard.report_sections)

    stats_parts = [f"Agents: {agents_completed}/{agents_total}"]

    if stats_handler:
        stats = stats_handler.get_stats()
        stats_parts.append(f"LLM: {stats['llm_calls']}")
        stats_parts.append(f"Tools: {stats['tool_calls']}")
        if stats["tokens_in"] > 0 or stats["tokens_out"] > 0:
            tin = stats["tokens_in"]
            tout = stats["tokens_out"]
            tin_str = f"{tin / 1000:.1f}k" if tin >= 1000 else str(tin)
            tout_str = f"{tout / 1000:.1f}k" if tout >= 1000 else str(tout)
            stats_parts.append(f"Tokens: {tin_str}↑ {tout_str}↓")
        else:
            stats_parts.append("Tokens: --")
        if stats.get("cost") is not None:
            stats_parts.append(f"Cost: ${stats['cost']:.4f}")

        # Per-model cost breakdown — when the run mixed providers or
        # quick/deep models with different rates, the rolled-up total
        # above hides which model is driving the spend. The breakdown
        # is bounded to the two largest entries to keep the footer line
        # readable; the full map is still in stats["cost_by_model"] for
        # callers that want the long form.
        cost_by_model = stats.get("cost_by_model") or {}
        if len(cost_by_model) > 1:
            top = sorted(
                cost_by_model.items(), key=lambda kv: kv[1], reverse=True,
            )[:2]
            breakdown = "  ".join(
                f"{model}: ${cost:.3f}" for model, cost in top
            )
            stats_parts.append(f"By model: {breakdown}")

    stats_parts.append(f"Reports: {reports_completed}/{reports_total}")

    if start_time:
        elapsed = datetime.datetime.now().timestamp() - start_time
        stats_parts.append(f"⏱ {int(elapsed // 60):02d}:{int(elapsed % 60):02d}")

    stats_table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    stats_table.add_column("Stats", justify="center")
    stats_table.add_row(" | ".join(stats_parts))

    layout["footer"].update(Panel(stats_table, border_style="grey50"))


def update_dashboard_display(
    layout: Layout,
    dashboard: AnalysisDashboard,
    ticker: str = "",
    stats_handler=None,
    start_time: float | None = None,
    batch_completed: int = 0,
    batch_total: int = 0,
    batch_failed: int = 0,
    profile_name: str | None = None,
    batch_mode: bool = False,
    running_tickers: set[str] | None = None,
) -> None:
    """统一渲染入口，batch 与单股模式共用。"""
    render_header(
        layout,
        dashboard,
        ticker,
        batch_completed=batch_completed,
        batch_total=batch_total,
        batch_failed=batch_failed,
        profile_name=profile_name,
        batch_mode=batch_mode,
        running_tickers=running_tickers,
    )
    if batch_mode:
        render_batch_progress_panel(layout, dashboard)
        render_messages_panel(layout, dashboard, batch_mode=True)
        render_analysis_panel(layout, dashboard, batch_mode=True)
    else:
        render_progress_panel(layout, dashboard)
        render_messages_panel(layout, dashboard)
        render_analysis_panel(layout, dashboard)
    render_footer(layout, dashboard, stats_handler=stats_handler, start_time=start_time)


# ── Stream chunk processing ───────────────────────────────────────

def _extract_content_string(content):
    """Extract string content from various message formats."""
    import ast

    def is_empty(val):
        if val is None or val == "":
            return True
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return True
            try:
                return not bool(ast.literal_eval(s))
            except (ValueError, SyntaxError):
                return False
        return not bool(val)

    if is_empty(content):
        return None
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        text = content.get("text", "")
        return text.strip() if not is_empty(text) else None
    if isinstance(content, list):
        text_parts = [
            item.get("text", "").strip()
            if isinstance(item, dict) and item.get("type") == "text"
            else (item.strip() if isinstance(item, str) else "")
            for item in content
        ]
        result = " ".join(t for t in text_parts if t and not is_empty(t))
        return result if result else None
    return str(content).strip() if not is_empty(content) else None


def _classify_message(message) -> tuple[str, str | None]:
    """Classify LangChain message into display type and extract content."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    content = _extract_content_string(getattr(message, "content", None))

    if isinstance(message, HumanMessage):
        if content and content.strip() == "Continue":
            return ("Control", content)
        return ("User", content)

    if isinstance(message, ToolMessage):
        return ("Data", content)

    if isinstance(message, AIMessage):
        return ("Agent", content)

    return ("System", content)


def _update_analyst_statuses(dashboard: AnalysisDashboard, chunk: dict) -> None:
    """Update analyst statuses based on accumulated report state."""
    selected = dashboard.selected_analysts
    found_active = False

    for analyst_key in ANALYST_ORDER:
        if analyst_key not in selected:
            continue

        agent_name = ANALYST_AGENT_NAMES[analyst_key]
        report_key = ANALYST_REPORT_MAP[analyst_key]

        if chunk.get(report_key):
            dashboard.update_report_section(report_key, chunk[report_key])

        has_report = bool(dashboard.report_sections.get(report_key))

        if has_report:
            dashboard.update_agent_status(agent_name, "completed")
        elif not found_active:
            dashboard.update_agent_status(agent_name, "in_progress")
            found_active = True
        else:
            dashboard.update_agent_status(agent_name, "pending")

    if not found_active and selected and dashboard.agent_status.get("Bull Researcher") == "pending":
        dashboard.update_agent_status("Bull Researcher", "in_progress")


def process_stream_chunk(
    dashboard: AnalysisDashboard,
    chunk: dict,
    max_debate_rounds: int = 1,
    max_risk_rounds: int = 1,
    processed_ids: set | None = None,
) -> set:
    """处理单个 graph stream chunk，更新 dashboard 所有状态。

    返回更新后的 processed_ids 集合（用于消息去重）。
    """
    if processed_ids is None:
        processed_ids = set()

    # 1. Messages & tool calls
    for message in chunk.get("messages", []):
        msg_id = getattr(message, "id", None)
        if msg_id is not None:
            if msg_id in processed_ids:
                continue
            processed_ids.add(msg_id)

        msg_type, content = _classify_message(message)
        if content and content.strip():
            dashboard.add_message(msg_type, content)

        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                if isinstance(tc, dict):
                    dashboard.add_tool_call(tc["name"], tc.get("args", {}))
                else:
                    dashboard.add_tool_call(tc.name, tc.args)
        else:
            # If this is a regular message (not tool call), mark current agent's tool call as finished
            if dashboard.current_agent and dashboard._tool_call_active.get(dashboard.current_agent):
                dashboard.finish_tool_call(dashboard.current_agent)

    # 2. Analyst statuses
    _update_analyst_statuses(dashboard, chunk)

    # 3. Research debate
    if chunk.get("investment_debate_state"):
        debate = chunk["investment_debate_state"]
        if debate.get("bull_history", "").strip() or debate.get("bear_history", "").strip():
            for agent in ["Bull Researcher", "Bear Researcher", "Research Manager"]:
                if dashboard.agent_status.get(agent) == "pending":
                    dashboard.update_agent_status(agent, "in_progress")
        if debate.get("judge_decision", "").strip():
            dashboard.update_agent_status("Research Manager", "completed")
            dashboard.update_agent_status("Trader", "in_progress")
            dashboard.update_report_section("investment_plan", debate["judge_decision"])

    # 4. Trading
    if chunk.get("trader_investment_plan"):
        dashboard.update_report_section("trader_investment_plan", chunk["trader_investment_plan"])
        if dashboard.agent_status.get("Trader") != "completed":
            dashboard.update_agent_status("Trader", "completed")
            dashboard.update_agent_status("Aggressive Analyst", "in_progress")

    # 5. Risk debate
    if chunk.get("risk_debate_state"):
        risk = chunk["risk_debate_state"]
        for role in ["aggressive", "conservative", "neutral"]:
            hist = risk.get(f"{role}_history", "").strip()
            agent_name = f"{role.title()} Analyst"
            if hist:
                if dashboard.agent_status.get(agent_name) != "completed":
                    dashboard.update_agent_status(agent_name, "in_progress")
                dashboard.update_report_section(
                    "final_trade_decision",
                    f"### {agent_name} Analysis\n{hist}",
                )
        judge = risk.get("judge_decision", "").strip()
        if judge and dashboard.agent_status.get("Portfolio Manager") != "completed":
            dashboard.update_agent_status("Portfolio Manager", "in_progress")
            dashboard.update_report_section(
                "final_trade_decision", f"### Portfolio Manager Decision\n{judge}"
            )
            dashboard.update_agent_status("Aggressive Analyst", "completed")
            dashboard.update_agent_status("Conservative Analyst", "completed")
            dashboard.update_agent_status("Neutral Analyst", "completed")
            dashboard.update_agent_status("Portfolio Manager", "completed")

    # 6. Stage tracking
    dashboard.update_stage_from_chunk(chunk, max_debate_rounds, max_risk_rounds)

    return processed_ids

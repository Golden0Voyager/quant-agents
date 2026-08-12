import contextlib
import datetime
import os
import time
from dataclasses import asdict
from functools import wraps
from pathlib import Path

import questionary
import typer
from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule

from cli.announcements import display_announcements, fetch_announcements
from cli.batch_runner import BatchRunner
from cli.dashboard import (
    AnalysisDashboard,
    create_dashboard_layout,
    process_stream_chunk,
    update_dashboard_display,
)
from cli.profiles import list_profiles, load_profile, save_profile
from cli.stats_handler import StatsCallbackHandler
from cli.utils import *
from cli.watchlists import list_watchlists, load_watchlist, save_watchlist
from tradingagents.dataflows.interface import collect_route_diagnostics
from tradingagents.default_config import DEFAULT_CONFIG, default_config
from tradingagents.graph.analyst_execution import (
    AnalystWallTimeTracker,
    build_analyst_execution_plan,
    get_initial_analyst_node,
    sync_analyst_tracker_from_chunk,
)
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients.pricing import get_usd_to_cny_rate
from tradingagents.reporting import write_report_tree

console = Console()

app = typer.Typer(
    name="TradingAgents",
    help="TradingAgents CLI: Multi-Agents LLM Financial Trading Framework",
    add_completion=True,  # Enable shell completion
)





def create_question_box(title, prompt, default=None):
    box_content = f"[bold]{title}[/bold]\n"
    box_content += f"[dim]{prompt}[/dim]"
    if default:
        box_content += f"\n[dim]Default: {default}[/dim]"
    return Panel(box_content, border_style="blue", padding=(1, 2))


def get_user_selections(preselected_tickers: list[str] | None = None) -> dict | None:
    """Get all user selections before starting the analysis display.

    Args:
        preselected_tickers: If provided, skip the ticker input prompt and use these tickers directly.
    """
    # Display ASCII art welcome message
    with open(Path(__file__).parent / "static" / "welcome.txt", encoding="utf-8") as f:
        welcome_ascii = f.read()

    # Create welcome box content
    welcome_content = f"{welcome_ascii}\n"
    welcome_content += "[bold green]TradingAgents: Multi-Agents LLM Financial Trading Framework - CLI[/bold green]\n\n"
    welcome_content += "[bold]Workflow Steps:[/bold]\n"
    welcome_content += (
        "I. Analyst Team → II. Research Team → III. Trader → IV. Risk Management → V. Portfolio Management\n\n"
    )
    welcome_content += "[dim]Built by [Tauric Research](https://github.com/TauricResearch)[/dim]"

    # Create and center the welcome box
    welcome_box = Panel(
        welcome_content,
        border_style="green",
        padding=(1, 2),
        title="Welcome to TradingAgents",
        subtitle="Multi-Agents LLM Financial Trading Framework",
    )
    console.print(Align.center(welcome_box))
    console.print()
    console.print()  # Add vertical space before announcements

    # Fetch and display announcements (silent on failure)
    announcements = fetch_announcements()
    display_announcements(console, announcements)

    # Create a boxed questionnaire for each step
    # (create_question_box is defined at module level)

    # Step 1: Ticker symbol(s)
    from tradingagents.ticker_resolver import resolve_ticker

    # Map resolved ticker -> company_name for downstream naming
    ticker_to_name: dict[str, str] = {}

    if preselected_tickers is not None:
        # Use watchlist or CLI-provided tickers directly
        selected_tickers = []
        ticker_names = []
        for t in preselected_tickers:
            try:
                resolved = resolve_ticker(t)
                selected_tickers.append(resolved["ticker"])
                name = resolved.get("company_name", "")
                ticker_to_name[resolved["ticker"]] = name
                ticker_names.append(f"[cyan]{resolved['ticker']}[/cyan] {name}")
            except Exception as e:
                console.print(f"[yellow]解析提示 {t}: {e}[/yellow]")
                selected_tickers.append(t.upper())
                ticker_names.append(f"[cyan]{t.upper()}[/cyan] (未知)")

        console.print("\n[bold cyan]Step 1: Ticker Symbol[/bold cyan]")
        console.print(f"[dim]Using pre-selected tickers from watchlist/args: {', '.join(preselected_tickers)}[/dim]")
        console.print("\n[bold]已解析股票:[/bold]")
        for line in ticker_names:
            console.print(f"  • {line}")
    else:
        while True:
            console.print("\n[bold cyan]Step 1: Ticker Symbol[/bold cyan]")
            console.print("[dim]Enter ticker symbol(s) to analyze, comma-separated for multiple[/dim]")
            raw_tickers = get_ticker()
            tickers = _parse_tickers_input(raw_tickers)

            # Resolve tickers and show names for confirmation
            selected_tickers = []
            ticker_names = []
            for t in tickers:
                try:
                    resolved = resolve_ticker(t)
                    selected_tickers.append(resolved["ticker"])
                    name = resolved.get("company_name", "")
                    ticker_to_name[resolved["ticker"]] = name
                    ticker_names.append(f"[cyan]{resolved['ticker']}[/cyan] {name}")
                except Exception as e:
                    console.print(f"[yellow]解析提示 {t}: {e}[/yellow]")
                    selected_tickers.append(t.upper())
                    ticker_names.append(f"[cyan]{t.upper()}[/cyan] (未知)")

            console.print("\n[bold]已解析股票:[/bold]")
            for line in ticker_names:
                console.print(f"  • {line}")

            import questionary

            confirmed = questionary.confirm(
                "股票信息是否正确？",
                default=True,
                style=questionary.Style(
                    [
                        ("question", "fg:green bold"),
                    ]
                ),
            ).ask()
            if confirmed:
                break
            console.print("[yellow]请重新输入股票代码...[/yellow]\n")

    selected_ticker = selected_tickers[0] if len(selected_tickers) == 1 else selected_tickers

    asset_type = detect_asset_type(selected_ticker if isinstance(selected_ticker, str) else selected_tickers[0])
    # Only announce when it's not the default stock path, to avoid printing
    # "stock" on every run.
    if asset_type.value != "stock":
        console.print(f"[green]Detected asset type:[/green] {asset_type.value}")

    # Step 2: Analysis date
    default_date = datetime.datetime.now().strftime("%Y-%m-%d")
    console.print("\n[bold cyan]Step 2: Analysis Date[/bold cyan]")
    console.print(f"[dim]Enter the analysis date (YYYY-MM-DD), default: {default_date}[/dim]")
    analysis_date = get_analysis_date()

    # Step 3: Select analysts
    console.print("\n[bold cyan]Step 3: Analysts Team[/bold cyan]")
    console.print("[dim]Select your LLM analyst agents for the analysis[/dim]")
    selected_analysts = select_analysts(
        asset_type,
        ticker=selected_ticker if isinstance(selected_ticker, str) else None,
    )
    console.print(f"[green]Selected analysts:[/green] {', '.join(analyst.value for analyst in selected_analysts)}")

    # Step 3.5: Data Readiness Check
    from tradingagents.agents.utils.data_readiness import (
        check_data_readiness,
        display_readiness_report,
    )

    console.print()
    ticker_for_check = selected_ticker if isinstance(selected_ticker, str) else selected_tickers[0]
    report = check_data_readiness(
        ticker=ticker_for_check,
        trade_date=analysis_date,
        selected_analysts=[a.value for a in selected_analysts],
    )
    display_readiness_report(console, report)
    if (
        report.warning_count > 0
        and not questionary.confirm(
            "部分数据不可用，是否继续分析？",
            default=True,
        ).ask()
    ):
        console.print("[yellow]已取消分析[/yellow]")
        return None

    # Step 4: Output language (skipped when set via TRADINGAGENTS_OUTPUT_LANGUAGE)
    if os.environ.get("TRADINGAGENTS_OUTPUT_LANGUAGE"):
        output_language = DEFAULT_CONFIG["output_language"]
        console.print(f"[green]✓ Output language from environment:[/green] {output_language}")
    else:
        console.print("\n[bold cyan]Step 4: Output Language[/bold cyan]")
        console.print("[dim]Select the language for analyst reports and final decision[/dim]")
        output_language = ask_output_language()

    # Step 5: Research depth
    console.print("\n[bold cyan]Step 5: Research Depth[/bold cyan]")
    console.print("[dim]Select your research depth level[/dim]")
    selected_research_depth = select_research_depth()

    # Step 6: LLM Provider (skipped when set via TRADINGAGENTS_LLM_PROVIDER).
    # The backend URL comes from TRADINGAGENTS_LLM_BACKEND_URL when set,
    # otherwise the provider's default endpoint — the same value the menu
    # would have picked.
    provider_from_env = bool(os.environ.get("TRADINGAGENTS_LLM_PROVIDER"))
    if provider_from_env:
        selected_llm_provider = DEFAULT_CONFIG["llm_provider"].lower()
        backend_url = DEFAULT_CONFIG["backend_url"] or provider_default_url(selected_llm_provider)
        console.print(f"[green]✓ LLM provider from environment:[/green] {selected_llm_provider}")
        console.print(f"[green]✓ Backend URL:[/green] {backend_url}")
        # Still confirm/persist the API key so the run doesn't fail later.
        ensure_api_key(selected_llm_provider)
    else:
        console.print("\n[bold cyan]Step 6: LLM Provider[/bold cyan]")
        console.print("[dim]Select your LLM provider[/dim]")
        selected_llm_provider, backend_url = select_llm_provider()

        # Providers with regional endpoints prompt for the region as a secondary
        # step so the main dropdown stays clean (mainland China and international
        # accounts cannot share API keys).
        if selected_llm_provider == "qwen":
            selected_llm_provider, backend_url = ask_qwen_region()
        elif selected_llm_provider == "minimax":
            selected_llm_provider, backend_url = ask_minimax_region()
        elif selected_llm_provider == "glm":
            selected_llm_provider, backend_url = ask_glm_region()

        # For Ollama, surface the resolved endpoint (OLLAMA_BASE_URL vs default)
        # before model selection so it's obvious where we're connecting.
        if selected_llm_provider == "ollama" and backend_url:
            confirm_ollama_endpoint(backend_url)

        # Confirm the provider's API key is present; prompt the user to paste
        # one and persist it to .env if it's missing, so the analysis run
        # doesn't fail later at the first API call.
        ensure_api_key(selected_llm_provider)

    # Step 7: Thinking agents (skipped when either model is set via environment)
    if os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM") or os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM"):
        selected_shallow_thinker = DEFAULT_CONFIG["quick_think_llm"]
        selected_deep_thinker = DEFAULT_CONFIG["deep_think_llm"]
        console.print(
            f"[green]✓ Thinking agents from environment:[/green] "
            f"quick={selected_shallow_thinker}, deep={selected_deep_thinker}"
        )
    else:
        console.print("\n[bold cyan]Step 7: Thinking Agents[/bold cyan]")
        console.print("[dim]Select your thinking agents for analysis[/dim]")
        selected_shallow_thinker = select_shallow_thinking_agent(selected_llm_provider)
        selected_deep_thinker = select_deep_thinking_agent(selected_llm_provider)

    # Step 8: Provider-specific thinking configuration
    thinking_level = None
    reasoning_effort = None
    anthropic_effort = None

    provider_lower = selected_llm_provider.lower()
    # When the provider is configured via environment we keep the run fully
    # non-interactive and use the config defaults (None = each provider's own
    # default reasoning/thinking behavior) instead of prompting.
    if provider_from_env:
        thinking_level = DEFAULT_CONFIG["google_thinking_level"]
        reasoning_effort = DEFAULT_CONFIG["openai_reasoning_effort"]
        anthropic_effort = DEFAULT_CONFIG["anthropic_effort"]
    elif provider_lower == "google":
        console.print("\n[bold cyan]Step 8: Thinking Mode[/bold cyan]")
        console.print("[dim]Configure Gemini thinking mode[/dim]")
        thinking_level = ask_gemini_thinking_config()
    elif provider_lower == "openai":
        console.print("\n[bold cyan]Step 8: Reasoning Effort[/bold cyan]")
        console.print("[dim]Configure OpenAI reasoning effort level[/dim]")
        reasoning_effort = ask_openai_reasoning_effort()
    elif provider_lower == "anthropic":
        console.print("\n[bold cyan]Step 8: Effort Level[/bold cyan]")
        console.print("[dim]Configure Claude effort level[/dim]")
        anthropic_effort = ask_anthropic_effort()

    first_ticker = selected_ticker if isinstance(selected_ticker, str) else selected_tickers[0]
    return {
        "ticker": first_ticker,
        "tickers": selected_tickers if isinstance(selected_ticker, list) else [selected_ticker],
        "company_name": ticker_to_name.get(first_ticker, ""),
        "asset_type": asset_type.value,
        "analysis_date": analysis_date,
        "analysts": selected_analysts,
        "research_depth": selected_research_depth,
        "llm_provider": selected_llm_provider.lower(),
        "backend_url": backend_url,
        "shallow_thinker": selected_shallow_thinker,
        "deep_thinker": selected_deep_thinker,
        "google_thinking_level": thinking_level,
        "openai_reasoning_effort": reasoning_effort,
        "anthropic_effort": anthropic_effort,
        "output_language": output_language,
    }


# Intentionally shadows cli.utils.get_analysis_date (pulled in by the star
# import above): the CLI flow uses this prompt-based variant, and tests patch
# it by this module-level name.
def get_analysis_date():  # type: ignore[no-redef]
    """Get the analysis date from user input."""
    while True:
        date_str = typer.prompt("", default=datetime.datetime.now().strftime("%Y-%m-%d"))
        try:
            # Validate date format and ensure it's not in the future
            analysis_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            if analysis_date.date() > datetime.datetime.now().date():
                console.print("[red]Error: Analysis date cannot be in the future[/red]")
                continue
            return date_str
        except ValueError:
            console.print("[red]Error: Invalid date format. Please use YYYY-MM-DD[/red]")


def _parse_tickers_input(raw: str) -> list[str]:
    """Parse comma-separated ticker input into a clean list.

    Auto-appends .SS/.SZ/.BJ for 6-digit Chinese A-share numeric codes.
    """
    from tradingagents.ticker_resolver import _append_a_share_suffix

    result = []
    for t in raw.split(","):
        t = t.strip()
        if not t:
            continue
        # Already has an exchange suffix -> pass through
        if "." in t:
            result.append(t.upper())
            continue
        # Pure numeric -> treat as A-share code and append suffix
        if t.isdigit():
            try:
                result.append(_append_a_share_suffix(t).upper())
                continue
            except ValueError:
                # Unrecognised prefix — keep as-is and let downstream fail gracefully
                pass
        result.append(t.upper())
    return result


def ask_mode() -> str:
    """Ask user to choose between batch watchlist scan or custom ticker query."""
    choice = questionary.select(
        "Select run mode:",
        choices=[
            questionary.Choice("查询自选股票（支持单只或多只，逗号分隔）", "single"),
            questionary.Choice("批量扫描 Watchlist", "batch"),
        ],
        style=questionary.Style(
            [
                ("selected", "fg:green noinherit"),
                ("highlighted", "fg:green noinherit"),
                ("pointer", "fg:green noinherit"),
            ]
        ),
    ).ask()
    if choice is None:
        console.print("[red]No mode selected. Exiting...[/red]")
        exit(1)
    return choice


def select_watchlist_interactive() -> tuple[str, list[str]]:
    """Let user pick a saved watchlist or import from file. Returns (name, tickers)."""
    existing = list_watchlists()
    choices = []
    for name in existing:
        try:
            tickers = load_watchlist(name)
            display = f"{name}  ({', '.join(tickers[:5])}{'...' if len(tickers) > 5 else ''})"
            choices.append(questionary.Choice(display, value=(name, tickers)))
        except Exception:
            choices.append(questionary.Choice(name, value=(name, [])))
    choices.append(questionary.Choice("Import from file...", value=("__import__", [])))

    choice = questionary.select(
        "Select watchlist:",
        choices=choices,
        style=questionary.Style(
            [
                ("selected", "fg:yellow noinherit"),
                ("highlighted", "fg:yellow noinherit"),
                ("pointer", "fg:yellow noinherit"),
            ]
        ),
    ).ask()

    if choice is None:
        console.print("[red]No watchlist selected. Exiting...[/red]")
        exit(1)

    name, tickers = choice
    if name == "__import__":
        file_path = (
            questionary.text(
                "Enter watchlist file path:",
                validate=lambda x: len(x.strip()) > 0 or "Please enter a valid path.",
            )
            .ask()
            .strip()
        )
        from cli.watchlists import parse_watchlist_content

        tickers = parse_watchlist_content(Path(file_path).read_text(encoding="utf-8"))
        name = Path(file_path).stem
    return name, tickers


def select_profile_interactive() -> dict | None:
    """Let user pick a saved profile or create a new one. Returns profile config dict."""
    existing = list_profiles()
    if existing:
        choices = []
        for name in existing:
            try:
                prof = load_profile(name)
                cfg = prof.get("config", {})
                summary = f"({cfg.get('llm_provider', '?')}, {cfg.get('deep_thinker', '?')}, {len(cfg.get('analysts', []))} analysts, {cfg.get('output_language', '?')})"
                choices.append(questionary.Choice(f"{name}  {summary}", value=name))
            except Exception:
                choices.append(questionary.Choice(name, value=name))
        choices.append(questionary.Choice("Create new profile...", value="__new__"))
        choice = questionary.select(
            "Select profile:",
            choices=choices,
            style=questionary.Style(
                [
                    ("selected", "fg:magenta noinherit"),
                    ("highlighted", "fg:magenta noinherit"),
                    ("pointer", "fg:magenta noinherit"),
                ]
            ),
        ).ask()
        if choice is None:
            console.print("[red]No profile selected. Exiting...[/red]")
            exit(1)
        if choice != "__new__":
            return load_profile(choice)["config"]
    # Fall through to create new profile
    return None


def save_report_to_disk(final_state, ticker: str, save_path: Path):
    """Save the complete analysis report to disk (shared CLI/API writer)."""
    return write_report_tree(final_state, ticker, save_path)


def _split_translation_chunks(text: str, max_chunk_size: int = 8000) -> list[str]:
    """Split markdown text into translation-safe chunks.

    Optimized for DeepSeek V3.1 (8K output tokens):
    - 8K output ≈ 12K Chinese chars ≈ ~10K English chars of source text.
    - We use 8K as the target to leave headroom for the prompt + safety margin.

    Splits at H2/H3 headers when possible, then at paragraph boundaries.
    Never breaks inside triple-backtick code blocks or markdown tables.
    """
    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    current_chunk_lines: list[str] = []
    current_size = 0
    in_code_block = False
    in_table = False

    def flush_chunk() -> None:
        nonlocal current_chunk_lines, current_size
        if current_chunk_lines:
            chunks.append("".join(current_chunk_lines).rstrip("\n"))
            current_chunk_lines = []
            current_size = 0

    def is_header(line: str) -> bool:
        stripped = line.lstrip()
        return stripped.startswith("## ") or stripped.startswith("### ")

    def is_table_row(line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith("|") and stripped.endswith("|")

    def is_table_separator(line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith("|") and "---" in stripped

    for line in lines:
        line_size = len(line)
        stripped = line.strip()

        # Code block gate
        if stripped.startswith("```"):
            in_code_block = not in_code_block

        # Table gate: table starts with a row containing |...| and continues
        # until a blank line or non-table line.
        if not in_code_block:
            if is_table_row(line) or is_table_separator(line):
                in_table = True
            elif in_table and stripped != "":
                # Non-empty, non-table line ends the table
                in_table = False
            # Blank lines inside tables are allowed (multi-row tables)

        # Flush before a new header if we're near the limit
        if is_header(line) and current_size > 0 and current_size + line_size > max_chunk_size:
            flush_chunk()

        current_chunk_lines.append(line)
        current_size += line_size

        # Flush at paragraph boundary only if we're outside special blocks
        can_split = not in_code_block and not in_table
        if can_split and stripped == "" and current_size >= max_chunk_size:
            flush_chunk()

        # Hard safety: force flush at 1.2x limit even inside blocks,
        # to prevent runaway growth on pathological input.
        if current_size >= int(max_chunk_size * 1.2):
            flush_chunk()
            in_table = False

    flush_chunk()
    return chunks


def _translate_chunk(llm, chunk: str, is_first: bool = False, context: str = "") -> str:
    """Translate a single chunk."""
    from langchain_core.messages import HumanMessage

    if is_first:
        prompt = (
            "请将以下金融分析报告从英文翻译成简体中文。\n"
            "要求：\n"
            "1. 保留所有 markdown 格式（标题层级、列表、表格、代码块、引用等）\n"
            "2. 保留所有专业金融术语的准确性\n"
            "3. 保留所有数字、符号、日期和货币单位不变\n"
            "4. 不要添加任何额外解释、总结或评论\n"
            "5. 直接返回翻译后的正文，不要包裹在代码块中\n\n"
            f"{chunk}"
        )
    else:
        # Provide the tail of the previous chunk so the model can keep
        # heading styles and terminology consistent across boundaries.
        ctx = f"前文末尾：\n{context}\n\n" if context else ""
        prompt = f"{ctx}继续翻译以下报告内容（与前文衔接，保持格式、术语和语气一致）：\n\n{chunk}"
    messages = [HumanMessage(content=prompt)]
    response = llm.invoke(messages)
    return str(response.content)


def _translate_content(llm, content: str) -> str:
    """Translate report content from English to Simplified Chinese using LLM.

    Large files are split into chunks at markdown headers / paragraph
    boundaries to avoid model output-token truncation.
    """
    chunks = _split_translation_chunks(content)
    if len(chunks) <= 1:
        return _translate_chunk(llm, content, is_first=True)

    translated_parts: list[str] = []
    prev_tail = ""
    for idx, chunk in enumerate(chunks):
        # Provide the last few lines of the previous chunk as context so the
        # model can maintain consistent heading style and narrative flow.
        context = prev_tail[-300:] if prev_tail else ""
        try:
            translated = _translate_chunk(llm, chunk, is_first=(idx == 0), context=context)
        except Exception:
            # If a single chunk fails, mark it and continue so the user gets
            # a partially-translated file rather than total loss.
            translated = f"\n\n<!-- 翻译中断：第 {idx + 1}/{len(chunks)} 块调用失败，保留原文 -->\n\n{chunk}"
        translated_parts.append(translated)
        prev_tail = translated

    return "\n\n".join(translated_parts)


def run_translation_pipeline(save_path: Path, config: dict) -> None:
    """Translate merged report files to Simplified Chinese with _CN suffix."""
    from tradingagents.llm_clients.factory import create_llm_client

    provider = config.get("llm_provider", "openai")
    model = config.get("quick_think_llm") or config.get("deep_think_llm")
    base_url = config.get("backend_url")

    if not model:
        console.print("[yellow]Warning: No LLM model configured for translation, skipping.[/yellow]")
        return

    try:
        client = create_llm_client(provider, model, base_url)
        llm = client.get_llm()
    except Exception as e:
        console.print(f"[yellow]Warning: Failed to initialize translation LLM: {e}[/yellow]")
        return

    complete_report = save_path / "complete_report.md"
    if not complete_report.exists():
        return

    console.print("[cyan]Translating complete report to Chinese...[/cyan]")
    try:
        content = complete_report.read_text(encoding="utf-8")
        chunks = _split_translation_chunks(content)
        chunk_info = f" ({len(chunks)} chunks)" if len(chunks) > 1 else ""
        translated = _translate_content(llm, content)
        output_path = complete_report.with_suffix("").with_name(complete_report.stem + "_CN.md")
        output_path.write_text(translated, encoding="utf-8")
        console.print(f"  [green]✓[/green] [dim]{output_path.name}{chunk_info}[/dim]")
    except Exception as e:
        console.print(f"[yellow]Warning: Failed to translate complete report: {e}[/yellow]")


def display_complete_report(final_state):
    """Display the complete analysis report sequentially (avoids truncation)."""
    console.print()
    console.print(Rule("Complete Analysis Report", style="bold green"))

    # I. Analyst Team Reports
    analysts = []
    if final_state.get("market_report"):
        analysts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts.append(("Sentiment Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if final_state.get("governance_report"):
        analysts.append(("Governance Analyst", final_state["governance_report"]))
    if analysts:
        console.print(Panel("[bold]I. Analyst Team Reports[/bold]", border_style="cyan"))
        for title, content in analysts:
            console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

    # II. Research Team Reports
    if final_state.get("investment_debate_state"):
        debate = final_state["investment_debate_state"]
        research = []
        if debate.get("bull_history"):
            research.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research.append(("Research Manager", debate["judge_decision"]))
        if research:
            console.print(Panel("[bold]II. Research Team Decision[/bold]", border_style="magenta"))
            for title, content in research:
                console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

    # III. Trading Team
    if final_state.get("trader_investment_plan"):
        console.print(Panel("[bold]III. Trading Team Plan[/bold]", border_style="yellow"))
        console.print(
            Panel(Markdown(final_state["trader_investment_plan"]), title="Trader", border_style="blue", padding=(1, 2))
        )

    # IV. Risk Management Team
    if final_state.get("risk_debate_state"):
        risk = final_state["risk_debate_state"]
        risk_reports = []
        if risk.get("aggressive_history"):
            risk_reports.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_reports.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_reports.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_reports:
            console.print(Panel("[bold]IV. Risk Management Team Decision[/bold]", border_style="red"))
            for title, content in risk_reports:
                console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

        # V. Portfolio Manager Decision
        if risk.get("judge_decision"):
            console.print(Panel("[bold]V. Portfolio Manager Decision[/bold]", border_style="green"))
            console.print(
                Panel(Markdown(risk["judge_decision"]), title="Portfolio Manager", border_style="blue", padding=(1, 2))
            )


def _sync_portfolio_for_my_list() -> None:
    """Sync holdings and transactions from Google Sheet for the 'my' watchlist.

    Uses the default portfolio configuration (``portfolio.sheet_id`` and
    ``portfolio.transaction_sheet_id``). The merged ``Portfolio`` is persisted
    to the local cache so the batch runner can use it as a reference.
    """
    from tradingagents.portfolio import (
        Portfolio,
        PortfolioRepository,
        PortfolioSyncService,
        TransactionSyncService,
    )

    portfolio_cfg = DEFAULT_CONFIG.get("portfolio", {})
    sheet_id = portfolio_cfg.get("sheet_id")
    worksheet = portfolio_cfg.get("worksheet", "total")
    transaction_sheet_id = portfolio_cfg.get("transaction_sheet_id")
    transaction_worksheet = portfolio_cfg.get("transaction_worksheet", "stock transitions")

    if not sheet_id and not transaction_sheet_id:
        console.print(
            "[yellow]\u26a0 portfolio.sheet_id / transaction_sheet_id 都未配置，"
            "跳过 'my' watchlist 的 Google Sheet 同步[/yellow]"
        )
        return

    repo = PortfolioRepository()
    # Start from the existing local cache so a failed sync doesn't wipe data.
    try:
        portfolio = repo.load() if repo.exists() else Portfolio()
    except Exception:
        portfolio = Portfolio()

    # Track whether every configured sync source succeeded. If any source
    # fails we keep the old local cache intact rather than persisting a
    # partially-updated (and therefore inconsistent) portfolio.
    all_syncs_ok = True

    if sheet_id:
        try:
            sync_service = PortfolioSyncService(sheet_id=sheet_id, worksheet=worksheet)
            synced = sync_service.sync()
            # Merge into the existing portfolio so previously cached
            # transactions (or transactions from a separate sheet) are not
            # accidentally wiped when the holdings sheet is refreshed.
            portfolio.holdings = synced.holdings
            portfolio.metadata = synced.metadata
            portfolio.summary = synced.summary
            console.print(
                f"[green]\u2713 已从 Google Sheet 同步 {len(portfolio.holdings)} 条持仓[/green]"
            )
        except Exception as exc:
            all_syncs_ok = False
            console.print(f"[yellow]\u26a0 同步持仓失败：{exc}[/yellow]")

    if transaction_sheet_id:
        try:
            tx_sync_service = TransactionSyncService(
                sheet_id=transaction_sheet_id, worksheet=transaction_worksheet
            )
            portfolio.transactions = tx_sync_service.sync()
            console.print(
                f"[green]\u2713 已从 Google Sheet 同步 {len(portfolio.transactions)} 条交易记录[/green]"
            )
        except Exception as exc:
            all_syncs_ok = False
            console.print(f"[yellow]\u26a0 同步交易记录失败：{exc}[/yellow]")

    if not all_syncs_ok:
        console.print(
            "[yellow]\u26a0 \u90e8\u5206\u540c\u6b65\u5931\u8d25\uff0c\u672c\u6b21\u4e0d\u4f1a"
            "\u66f4\u65b0\u672c\u5730\u7f13\u5b58\u4ee5\u514d\u4fdd\u5b58\u4e0d\u4e00\u81f4\u7684\u6570\u636e[/yellow]"
        )
        return

    try:
        repo.save(portfolio)
        console.print(f"[green]\u2713 已保存到本地缓存：{repo.path}[/green]")
    except Exception as exc:
        console.print(f"[yellow]\u26a0 保存本地缓存失败：{exc}[/yellow]")


def _resolve_holdings(
    holdings_sheet: str | None,
    holdings_worksheet: str,
    sync_holdings: bool,
) -> dict | None:
    """Resolve holdings from local cache, optionally syncing from Google Sheet first.

    Returns a flat dict compatible with the existing holdings_context format:
    {ticker: {"shares": float, "avg_cost": float, ...}}
    """
    from tradingagents.portfolio import PortfolioRepository, PortfolioSyncService

    repo = PortfolioRepository()
    needs_sync = sync_holdings or holdings_sheet is not None

    if needs_sync:
        sheet_id = holdings_sheet or DEFAULT_CONFIG.get("portfolio", {}).get("sheet_id")
        if not sheet_id:
            console.print(
                "[red]No sheet ID configured. Use --holdings-sheet or set portfolio.sheet_id in config.[/red]"
            )
            return None

        worksheet = holdings_worksheet or DEFAULT_CONFIG.get("portfolio", {}).get("worksheet", "total")
        try:
            sync_service = PortfolioSyncService(sheet_id=sheet_id, worksheet=worksheet)
            portfolio = sync_service.sync()
            repo.save(portfolio)
            console.print(
                f"[green]✓ Synced {len(portfolio.holdings)} holdings to local cache[/green] ([dim]{repo.path}[/dim])"
            )
        except Exception as exc:
            console.print(f"[yellow]⚠ Sync failed: {exc}[/yellow]")
            if not repo.exists():
                console.print("[red]No local cache available. Run with --holdings-sheet first.[/red]")
                return None
            console.print("[dim]Using last known local cache[/dim]")

    if repo.exists():
        try:
            portfolio = repo.load()
            console.print(
                f"[green]✓ Loaded {len(portfolio.holdings)} holdings from local cache[/green] "
                f"([dim]{portfolio.metadata.updated_at or 'unknown'}[/dim])"
            )
            # Convert to legacy flat dict for backward compatibility
            return {
                ticker: {
                    "ticker": ticker,
                    "shares": h.shares,
                    "avg_cost": h.avg_cost,
                    "market_price": h.market_price,
                    "pnl_pct": h.pnl_pct,
                    "weight": h.weight,
                    "grid_strategy": h.grid_strategy,
                    "name": h.name,
                }
                for ticker, h in portfolio.holdings.items()
            }
        except Exception as exc:
            console.print(f"[yellow]⚠ Failed to load local holdings: {exc}[/yellow]")

    return None




def run_analysis(checkpoint: bool = False, selections: dict | None = None, holdings: dict | None = None):
    # First get all user selections (if not provided)
    if selections is None:
        selections = get_user_selections()
    if selections is None:
        return

    # Create config with selected research depth
    config = default_config()
    config["max_debate_rounds"] = selections["research_depth"]
    config["max_risk_discuss_rounds"] = selections["research_depth"]
    config["quick_think_llm"] = selections["shallow_thinker"]
    config["deep_think_llm"] = selections["deep_thinker"]
    config["backend_url"] = selections["backend_url"]
    config["llm_provider"] = selections["llm_provider"].lower()
    config["google_thinking_level"] = selections.get("google_thinking_level")
    config["openai_reasoning_effort"] = selections.get("openai_reasoning_effort")
    config["anthropic_effort"] = selections.get("anthropic_effort")
    config["output_language"] = selections.get("output_language", "English")
    config["checkpoint_enabled"] = checkpoint

    stats_handler = StatsCallbackHandler()

    selected_set = {analyst.value for analyst in selections["analysts"]}
    selected_analyst_keys = [a[1].value for a in ANALYST_ORDER if a[1].value in selected_set]
    analyst_execution_plan = build_analyst_execution_plan(selected_analyst_keys)
    analyst_wall_time_tracker = AnalystWallTimeTracker(analyst_execution_plan)

    graph = TradingAgentsGraph(
        selected_analyst_keys,
        config=config,
        debug=True,
        callbacks=[stats_handler],
    )

    dashboard = AnalysisDashboard()
    dashboard.init_for_analysis(selected_analyst_keys)

    start_time = time.time()

    # Create result directory (named: 中文名称_股票代码)
    company_name = selections.get("company_name", "")
    ticker_dir_name = BatchRunner._build_ticker_dir_name(selections["ticker"], company_name)
    results_dir = Path(config["results_dir"]) / ticker_dir_name / selections["analysis_date"]
    results_dir.mkdir(parents=True, exist_ok=True)
    report_dir = results_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    log_file = results_dir / "message_tool.log"
    log_file.touch(exist_ok=True)

    # Prompt to skip if a complete report already exists for this ticker+date
    force_regenerate = selections.get("force_regenerate", False)
    existing_report = results_dir / "complete_report.md"
    if not force_regenerate and existing_report.exists() and existing_report.stat().st_size > 0:
        report_date = BatchRunner._parse_report_analysis_date(existing_report)
        if report_date == selections["analysis_date"]:
            import questionary

            choice = questionary.select(
                f"检测到 {selections['ticker']} 在 {selections['analysis_date']} 已有完整分析报告，请选择：",
                choices=[
                    questionary.Choice("跳过，查看已有报告", value="skip"),
                    questionary.Choice("强制重新生成", value="regenerate"),
                ],
                default="skip",
            ).ask()
            if choice == "skip":
                console.print(f"\n[green]加载已有报告: {existing_report.resolve()}[/green]\n")
                report_content = existing_report.read_text(encoding="utf-8")
                console.print(Markdown(report_content))
                return
            # choice == "regenerate" → fall through to regenerate

    # Phase 2: Pre-market / after-hours fallback — reuse last trading day's
    # post-close report if the market hasn't opened since.
    if BatchRunner._is_outside_trading_hours():
        last_close = BatchRunner._get_last_trading_day()
        last_date = last_close.strftime("%Y-%m-%d")
        if last_date != selections["analysis_date"]:
            last_report_dir = Path(config["results_dir"]) / ticker_dir_name / last_date
            last_report = last_report_dir / "complete_report.md"
            if last_report.exists() and last_report.stat().st_size > 0:
                report_date = BatchRunner._parse_report_analysis_date(last_report)
                if report_date == last_date and last_report.stat().st_mtime >= last_close.timestamp():
                    import questionary

                    choice = questionary.select(
                        f"检测到 {selections['ticker']} 在 {last_date} 收盘后已有分析报告，请选择：",
                        choices=[
                            questionary.Choice("跳过，查看已有报告", value="skip"),
                            questionary.Choice("强制重新生成", value="regenerate"),
                        ],
                        default="skip",
                    ).ask()
                    if choice == "skip":
                        report_content = last_report.read_text(encoding="utf-8")
                        console.print(Markdown(report_content))
                        return
                    # choice == "regenerate" → fall through to regenerate

    def save_message_decorator(obj, func_name):
        func = getattr(obj, func_name)

        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, message_type, content = obj.messages[-1]
            content = content.replace("\n", " ")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [{message_type}] {content}\n")

        return wrapper

    def save_tool_call_decorator(obj, func_name):
        func = getattr(obj, func_name)

        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, tool_name, args = obj.tool_calls[-1]
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [Tool Call] {tool_name}({args_str})\n")

        return wrapper

    def save_report_section_decorator(obj, func_name):
        func = getattr(obj, func_name)

        @wraps(func)
        def wrapper(section_name, content):
            func(section_name, content)
            if section_name in obj.report_sections and obj.report_sections[section_name] is not None:
                content = obj.report_sections[section_name]
                if content:
                    file_name = f"{section_name}.md"
                    text = "\n".join(str(item) for item in content) if isinstance(content, list) else content
                    with open(report_dir / file_name, "w", encoding="utf-8") as f:
                        f.write(text)

        return wrapper

    dashboard.add_message = save_message_decorator(dashboard, "add_message")  # type: ignore[method-assign]
    dashboard.add_tool_call = save_tool_call_decorator(dashboard, "add_tool_call")  # type: ignore[method-assign]
    dashboard.update_report_section = save_report_section_decorator(dashboard, "update_report_section")  # type: ignore[method-assign]

    # Now start the display layout
    layout = create_dashboard_layout()
    ticker = selections["ticker"]
    date = selections["analysis_date"]

    with Live(layout, refresh_per_second=4):
        # Initial display
        update_dashboard_display(layout, dashboard, ticker=ticker, stats_handler=stats_handler, start_time=start_time)

        # Add initial messages
        dashboard.add_message("System", f"Analyzing {ticker} on {date}...")
        if selections["asset_type"] != "stock":
            dashboard.add_message("System", f"Detected asset type: {selections['asset_type']}")
        dashboard.add_message("System", f"Selected analysts: {', '.join(analyst.value for analyst in selections['analysts'])}")
        update_dashboard_display(layout, dashboard, ticker=ticker, stats_handler=stats_handler, start_time=start_time)

        # Update agent status to in_progress for the first analyst
        first_analyst = get_initial_analyst_node(analyst_execution_plan)
        dashboard.update_agent_status(first_analyst, "in_progress")
        analyst_wall_time_tracker.mark_started(selected_analyst_keys[0])
        update_dashboard_display(layout, dashboard, ticker=ticker, stats_handler=stats_handler, start_time=start_time)

        # Initialize state and get graph args with callbacks.
        # Resolve the instrument identity once here so all agents anchor to
        # the real company (#814); the CLI builds state directly rather than
        # going through propagate(), so this must happen on the CLI path too.
        # Pass user-confirmed company name so akshare-resolved identity
        # overrides yfinance when they disagree (#814 follow-up).
        instrument_context = graph.resolve_instrument_context(
            ticker,
            selections["asset_type"],
            confirmed_name=selections.get("company_name"),
        )
        init_agent_state = graph.propagator.create_initial_state(
            ticker,
            date,
            asset_type=selections["asset_type"],
            instrument_context=instrument_context,
        )
        if holdings:
            init_agent_state["holdings_context"] = holdings
        # Inject transaction history if available
        try:
            from tradingagents.portfolio import PortfolioRepository

            repo = PortfolioRepository()
            if repo.exists():
                portfolio = repo.load()
                if portfolio.transactions:
                    init_agent_state["transactions_context"] = [t.to_dict() for t in portfolio.transactions]
        except Exception:
            pass
        # Pass callbacks to graph config for tool execution tracking
        # (LLM tracking is handled separately via LLM constructor)
        args = graph.propagator.get_graph_args(callbacks=[stats_handler])

        # Stream the analysis
        trace = []
        processed_ids: set = set()
        max_debate = config.get("max_debate_rounds", 1)
        max_risk = config.get("max_risk_discuss_rounds", 1)
        try:
            with collect_route_diagnostics() as route_diagnostics:
                for chunk in graph.graph.stream(init_agent_state, **args):
                    sync_analyst_tracker_from_chunk(analyst_wall_time_tracker, chunk)

                    processed_ids = process_stream_chunk(
                        dashboard,
                        chunk,
                        max_debate_rounds=max_debate,
                        max_risk_rounds=max_risk,
                        processed_ids=processed_ids,
                    )
                    update_dashboard_display(layout, dashboard, ticker=ticker, stats_handler=stats_handler, start_time=start_time)
                    trace.append(chunk)

        except Exception as exc:
            from openai import APIConnectionError, APITimeoutError, RateLimitError

            if isinstance(exc, RateLimitError):
                friendly = "⚠️ API 限流：当前请求过于频繁，请稍后重试。"
            elif isinstance(exc, APITimeoutError):
                friendly = "⚠️ API 响应超时，请检查网络连接后重试。"
            elif isinstance(exc, APIConnectionError):
                friendly = "⚠️ API 连接失败，请检查网络连接后重试。"
            else:
                friendly = f"⚠️ 分析过程中出现异常 ({type(exc).__name__})，请稍后重试。"
            console.print(f"\n[red]{friendly}[/red]")
            dashboard.add_message("System", friendly)

        # Streamed chunks are per-node deltas, not full state. Merge them
        # so every report field populated across the run is present.
        final_state = {}
        for chunk in trace:
            final_state.update(chunk)
        final_state["data_coverage"] = [asdict(item) for item in route_diagnostics]
        graph.process_signal(final_state.get("final_trade_decision", "hold"))

        # Update all agent statuses to completed
        for agent in dashboard.agent_status:
            dashboard.update_agent_status(agent, "completed")

        dashboard.add_message("System", f"Completed analysis for {date}")
        dashboard.add_message("System", analyst_wall_time_tracker.format_summary())

        # Update final report sections
        for section in dashboard.report_sections:
            if section in final_state:
                dashboard.update_report_section(section, final_state[section])

        update_dashboard_display(layout, dashboard, ticker=ticker, stats_handler=stats_handler, start_time=start_time)

    # Post-analysis prompts (outside Live context for clean interaction)
    console.print("\n[bold cyan]Analysis Complete![/bold cyan]\n")
    console.print(f"[dim]{analyst_wall_time_tracker.format_summary()}[/dim]")

    # Always save to results_dir so future runs can detect and skip
    with contextlib.suppress(Exception):
        save_report_to_disk(final_state, ticker, results_dir)
    console.print("[green]✓ 报告已保存[/green]")

    # Single prompt: display report
    display_choice = typer.prompt("\n查看完整报告？", default="Y").strip().upper()
    if display_choice in ("Y", "YES", ""):
        display_complete_report(final_state)


def run_batch_analysis(
    tickers: list[str],
    profile_config: dict,
    checkpoint: bool = False,
    output_dir: Path | None = None,
    watchlist_name: str | None = None,
    holdings: dict | None = None,
    workers: int = 1,
    headless: bool = False,
    force: bool = False,
):
    """Run unattended batch analysis for multiple tickers.

    When *headless* is True, all interactive prompts are skipped so the
    function can run in CI/CD pipelines (e.g. GitHub Actions).
    When *force* is True, existing reports are re-generated even if they exist.
    """
    # For the special 'my' watchlist, always pull the latest holdings and
    # transactions from Google Sheet so the batch runs against fresh data.
    # This intentionally overrides any holdings dict already resolved from the
    # stale local cache; callers that need a custom holdings sheet should use
    # another watchlist.
    if watchlist_name == "my":
        console.print(
            "[bold cyan]\u68c0\u6d4b\u5230 'my' watchlist\uff0c\u6b63\u5728\u4ece Google Sheet "
            "\u540c\u6b65\u6700\u65b0\u6301\u4ed3\u548c\u4ea4\u6613\u8bb0\u5f55...[/bold cyan]"
        )
        _sync_portfolio_for_my_list()
        # Force the runner to reload holdings from the freshly synced cache.
        holdings = None

    date_stamp = __import__("datetime").datetime.now().strftime("%Y%m%d")
    timestamp = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    if output_dir is None:
        suffix = watchlist_name if watchlist_name else "custom"
        output_dir = Path.cwd() / "reports" / f"{date_stamp}_batch_{suffix}"
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    runner = BatchRunner(
        tickers=tickers,
        profile_config=profile_config,
        output_dir=output_dir,
        checkpoint=checkpoint,
        holdings=holdings,
        workers=workers,
        force=force,
    )
    runner._headless = headless
    runner.run()

    # Generate summary
    summary_path = runner.generate_summary()
    console.print("\n[bold cyan]Batch Complete![/bold cyan]\n")
    console.print(
        f"Total: {len(tickers)}  |  Success: {len(runner.completed_tickers) - len(runner.failures)}  |  Failed: {len(runner.failures)}"
    )
    console.print(f"[green]Reports:[/green] {output_dir.resolve()}")
    console.print(f"[green]Summary:[/green] {summary_path.name}")

    # 刷新评级准确率回测库（静默、失败容忍）：让 rating_outcomes 随每次 batch
    # 自动积累，后续的准确率检查点 (如看空校准提示的效果度量) 无需手动跑脚本。
    try:
        import subprocess
        import sys

        backtest_script = Path(__file__).resolve().parent.parent / "scripts" / "rating_backtest.py"
        if backtest_script.exists():
            result = subprocess.run(
                [sys.executable, str(backtest_script), "--quiet"],
                capture_output=True, text=True, timeout=180,
            )
            if result.returncode == 0:
                console.print("[dim]评级准确率回测库已刷新 (reports/rating_accuracy_baseline.md)[/dim]")
    except Exception:  # noqa: BLE001 — 回测刷新是附带动作，绝不影响 batch 主流程
        pass

    # Print summary table
    from rich.table import Table

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Ticker", style="cyan")
    table.add_column("Company", style="green")
    table.add_column("Rating", style="yellow")
    table.add_column("Entry", style="white")
    table.add_column("Stop", style="white")
    table.add_column("Size", style="white")
    table.add_column("Status", style="green")

    for ticker in tickers:
        if ticker in runner.failures:
            table.add_row(ticker, "—", "—", "—", "—", "—", f"❌ {runner.failures[ticker]}")
        else:
            s = runner.summaries.get(ticker, {})
            table.add_row(
                ticker,
                s.get("company", ticker),
                s.get("rating", "—"),
                s.get("entry", "—"),
                s.get("stop", "—"),
                s.get("size", "—"),
                "✅",
            )
    console.print(table)

    if runner.batch_stats.get("tokens_in", 0) > 0:
        tin = runner.batch_stats["tokens_in"]
        tout = runner.batch_stats["tokens_out"]
        tin_str = f"{tin / 1000:.1f}k" if tin >= 1000 else str(tin)
        tout_str = f"{tout / 1000:.1f}k" if tout >= 1000 else str(tout)
        cost_by_model = runner.batch_stats.get("cost_by_model", {})
        calls_by_model = runner.batch_stats.get("calls_by_model", {}) or {}
        total_cost = sum(cost_by_model.values())
        total_calls = int(runner.batch_stats.get("llm_calls", 0) or 0)
        cny_rate = get_usd_to_cny_rate()
        console.print("\n[bold]Batch Cost Summary[/bold]")
        console.print(f"  Tokens: {tin_str}\u2191 {tout_str}\u2193")
        console.print(f"  LLM calls: {total_calls}")
        if cost_by_model:
            console.print(f"  Total Cost: [yellow]${total_cost:.4f}[/yellow] (\u00a5{total_cost * cny_rate:.2f})")
            console.print("  By Model:")
            for model, cost in sorted(cost_by_model.items(), key=lambda kv: -kv[1]):
                model_calls = int(calls_by_model.get(model, 0) or 0)
                call_part = f" ({model_calls} calls)" if model_calls else ""
                console.print(f"    {model}: ${cost:.4f} (\u00a5{cost * cny_rate:.2f}){call_part}")
        else:
            console.print("  Total Cost: \u2014 (no priced models in this batch)")
            if calls_by_model:
                console.print("  By Model (calls only):")
                for model, count in sorted(calls_by_model.items(), key=lambda kv: -kv[1]):
                    console.print(f"    {model}: {int(count)} calls")

    # For single-ticker runs, offer to display the complete report
    if not headless and len(tickers) == 1:
        ticker = tickers[0]
        s = runner.summaries.get(ticker, {})
        company = s.get("company", ticker)
        ticker_dir_name = BatchRunner._build_ticker_dir_name(ticker, company)
        report_path = output_dir / ticker_dir_name / "complete_report.md"
        if report_path.exists() and report_path.stat().st_size > 0:
            import questionary

            show_report = questionary.confirm(
                f"查看 {ticker} 完整分析报告？",
                default=True,
            ).ask()
            if show_report:
                from rich.markdown import Markdown

                report_content = report_path.read_text(encoding="utf-8")
                console.print()
                console.print(Rule("Complete Analysis Report", style="bold green"))
                console.print(Markdown(report_content))

    # Prompt to save as watchlist if not from one (skip in headless mode)
    if not headless:
        save_wl = typer.prompt("Save ticker list as watchlist?", default="N").strip().upper()
        if save_wl in ("Y", "YES"):
            wl_name = typer.prompt("Watchlist name", default=f"batch_{timestamp}").strip()
            save_watchlist(wl_name, tickers)
            console.print(f"[green]✓ Watchlist saved:[/green] {wl_name}")


@app.command()
def analyze(
    checkpoint: bool = typer.Option(
        True,
        "--checkpoint",
        help="Enable checkpoint/resume: save state after each node so a crashed run can resume.",
    ),
    clear_checkpoints: bool = typer.Option(
        False,
        "--clear-checkpoints",
        help="Delete all saved checkpoints before running (force fresh start).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Regenerate reports even when matching reports already exist.",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Use a saved profile for analysis configuration.",
    ),
    watchlist: str | None = typer.Option(
        None,
        "--watchlist",
        help="Run batch analysis using a saved watchlist (by name or file path).",
    ),
    tickers: str | None = typer.Option(
        None,
        "--tickers",
        help="Comma-separated tickers for batch analysis (e.g. AAPL,MSFT,GOOGL).",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        help="Path to a JSON config file for headless batch mode (GitHub Actions).",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output-dir",
        help="Custom output directory for reports (default: ./reports).",
    ),
    holdings_sheet: str | None = typer.Option(
        None,
        "--holdings-sheet",
        help="Google Sheet ID to load current holdings for position-aware analysis.",
    ),
    holdings_worksheet: str = typer.Option(
        "total",
        "--holdings-worksheet",
        help="Worksheet/tab name inside the holdings Google Sheet.",
    ),
    sync_holdings: bool = typer.Option(
        False,
        "--sync-holdings",
        help="Sync holdings from Google Sheet to local cache before analysis.",
    ),
    workers: int = typer.Option(
        1,
        "--workers",
        "-w",
        help="Number of concurrent workers for batch analysis (default: 1). Values > 1 degrade the dashboard to a batch summary view.",
    ),
):
    # Declared up front: headless/direct branches assign a dict, while the
    # interactive branch may hold None until a profile is selected or created.
    profile_config: dict | None

    if clear_checkpoints:
        from tradingagents.graph.checkpointer import clear_all_checkpoints

        n = clear_all_checkpoints(DEFAULT_CONFIG["data_cache_dir"])
        console.print(f"[yellow]Cleared {n} checkpoint(s).[/yellow]")

    # Resolve holdings: sync if requested, then read from local cache
    holdings = _resolve_holdings(
        holdings_sheet=holdings_sheet,
        holdings_worksheet=holdings_worksheet,
        sync_holdings=sync_holdings,
    )

    # Headless batch mode via JSON config file (--config)
    if config:
        import json as _json

        config_path = Path(config)
        if not config_path.exists():
            console.print(f"[red]Config file not found: {config}[/red]")
            raise typer.Exit(1)
        try:
            cfg = _json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as e:
            console.print(f"[red]Failed to parse config file: {e}[/red]")
            raise typer.Exit(1) from None

        ticker_list = cfg.get("tickers", [])
        if not ticker_list:
            console.print("[red]Config file must contain a 'tickers' list.[/red]")
            raise typer.Exit(1)

        # Merge config file settings with defaults
        headless_config = default_config()
        headless_config.update({k: v for k, v in cfg.get("config", {}).items() if v is not None})

        run_batch_analysis(
            ticker_list,
            headless_config,
            checkpoint=cfg.get("checkpoint", checkpoint),
            output_dir=Path(cfg["output_dir"]) if cfg.get("output_dir") else (Path(output_dir) if output_dir else None),
            holdings=holdings,
            workers=cfg.get("workers", workers),
            headless=True,
            force=cfg.get("force", force),
        )
        return

    # Direct batch mode via CLI args
    if profile or watchlist or tickers:
        # Load profile
        if profile:
            try:
                prof = load_profile(profile)
                profile_config = prof["config"]
            except Exception as e:
                console.print(f"[red]Failed to load profile '{profile}': {e}[/red]")
                raise typer.Exit(1) from None
        else:
            profile_config = default_config()
            profile_config["analysts"] = ["market"]

        # Load tickers
        if tickers:
            ticker_list = _parse_tickers_input(tickers)
        elif watchlist:
            try:
                ticker_list = load_watchlist(watchlist)
            except Exception:
                # Try as file path
                from cli.watchlists import parse_watchlist_content

                ticker_list = parse_watchlist_content(Path(watchlist).read_text(encoding="utf-8"))
        else:
            console.print("[red]Batch mode requires --tickers or --watchlist.[/red]")
            raise typer.Exit(1)

        if not ticker_list:
            console.print("[red]No tickers to analyze.[/red]")
            raise typer.Exit(1)

        run_batch_analysis(
            ticker_list,
            profile_config,
            checkpoint=checkpoint,
            output_dir=Path(output_dir) if output_dir else None,
            watchlist_name=watchlist,
            holdings=holdings,
            workers=workers,
            headless=True,
            force=force,
        )
        return

    # Interactive mode
    if not holdings and not holdings_sheet and not sync_holdings:
        holdings = _prompt_sync_holdings_interactive()
    mode = ask_mode()
    if mode == "batch":
        watchlist_name, ticker_list = select_watchlist_interactive()
        profile_config = select_profile_interactive()
        if profile_config is None:
            # User chose to create new profile — run the normal selection flow,
            # but pass through the watchlist tickers so we don't ask again.
            selections = get_user_selections(preselected_tickers=ticker_list)
            if selections is None:
                return
            profile_config = {
                "analysts": [a.value for a in selections["analysts"]],
                "research_depth": selections["research_depth"],
                "llm_provider": selections["llm_provider"],
                "backend_url": selections["backend_url"],
                "shallow_thinker": selections["shallow_thinker"],
                "deep_thinker": selections["deep_thinker"],
                "google_thinking_level": selections.get("google_thinking_level"),
                "openai_reasoning_effort": selections.get("openai_reasoning_effort"),
                "anthropic_effort": selections.get("anthropic_effort"),
                "output_language": selections.get("output_language", "English"),
            }
            save_prof = typer.prompt("Save this configuration as a profile?", default="Y").strip().upper()
            if save_prof in ("Y", "YES", ""):
                prof_name = typer.prompt("Profile name", default="default").strip()
                save_profile(prof_name, profile_config)
                console.print(f"[green]✓ Profile saved:[/green] {prof_name}")

        if len(ticker_list) == 1:
            # Fall back to single-stock flow for one ticker
            if profile_config is None:
                # User just created a new profile via get_user_selections — pass selections through
                run_analysis(checkpoint=checkpoint, selections=selections, holdings=holdings)
            else:
                # User selected an existing profile — use batch flow with a single ticker
                run_batch_analysis(
                    [ticker_list[0]],
                    profile_config,
                    checkpoint=checkpoint,
                    output_dir=Path(output_dir) if output_dir else None,
                    watchlist_name=watchlist_name,
                    holdings=holdings,
                    workers=workers,
                )
        else:
            # Ask for worker count in interactive mode (CLI --workers defaults to 1)
            if workers <= 1:
                workers = ask_workers()
            run_batch_analysis(
                ticker_list,
                profile_config,
                checkpoint=checkpoint,
                output_dir=Path(output_dir) if output_dir else None,
                watchlist_name=watchlist_name,
                holdings=holdings,
                workers=workers,
            )
    else:
        # Single / custom mode
        import questionary

        use_profile = questionary.confirm(
            "使用保存的配置快速开始？（跳过 LLM/分析师等配置）",
            default=True,
        ).ask()
        if use_profile:
            profile_config = select_profile_interactive()
            if profile_config:
                from tradingagents.ticker_resolver import resolve_ticker

                console.print("\n[bold cyan]Step 1: Ticker Symbol[/bold cyan]")
                console.print("[dim]Enter ticker symbol(s) to analyze[/dim]")
                raw_tickers = get_ticker()
                parsed_tickers = _parse_tickers_input(raw_tickers)
                for pt in parsed_tickers:
                    r = resolve_ticker(pt)
                    name = r.get("company_name", "")
                    console.print(f"[green]  ✓ {r['ticker']}[/green] {name}")
                default_date = datetime.datetime.now().strftime("%Y-%m-%d")
                console.print("\n[bold cyan]Step 2: Analysis Date[/bold cyan]")
                console.print(f"[dim]Using default date: {default_date}[/dim]")
                tickers_list = parsed_tickers
                if len(tickers_list) > 1:
                    if workers <= 1:
                        workers = ask_workers()
                    run_batch_analysis(
                        tickers_list,
                        profile_config,
                        checkpoint=checkpoint,
                        output_dir=Path(output_dir) if output_dir else None,
                        holdings=holdings,
                        workers=workers,
                    )
                else:
                    run_batch_analysis(
                        tickers_list,
                        profile_config,
                        checkpoint=checkpoint,
                        output_dir=Path(output_dir) if output_dir else None,
                        holdings=holdings,
                        workers=workers,
                    )
                return
        # Fall back to full interactive flow
        selections = get_user_selections()
        if selections is None:
            return
        tickers_list = selections.get("tickers", [selections["ticker"]])
        if len(tickers_list) > 1:
            # Batch mode for multiple custom tickers
            profile_config = {
                "analysts": [a.value for a in selections["analysts"]],
                "research_depth": selections["research_depth"],
                "llm_provider": selections["llm_provider"],
                "backend_url": selections["backend_url"],
                "shallow_thinker": selections["shallow_thinker"],
                "deep_thinker": selections["deep_thinker"],
                "google_thinking_level": selections.get("google_thinking_level"),
                "openai_reasoning_effort": selections.get("openai_reasoning_effort"),
                "anthropic_effort": selections.get("anthropic_effort"),
                "output_language": selections.get("output_language", "English"),
            }
            # Ask for worker count in interactive mode (CLI --workers defaults to 1)
            if workers <= 1:
                workers = ask_workers()
            run_batch_analysis(
                tickers_list,
                profile_config,
                checkpoint=checkpoint,
                output_dir=Path(output_dir) if output_dir else None,
                holdings=holdings,
                workers=workers,
            )
        else:
            run_analysis(checkpoint=checkpoint, selections=selections, holdings=holdings)


def _prompt_sync_holdings_interactive() -> dict | None:
    """在交互模式下询问用户是否要同步自选股，返回 holdings dict 或 None。

    仅在配置了 PORTFOLIO_SHEET_ID 时弹出询问，否则静默跳过。
    """
    sheet_id = DEFAULT_CONFIG.get("portfolio", {}).get("sheet_id")
    if not sheet_id:
        return None

    import questionary

    do_sync = questionary.confirm(
        "是否同步自选股数据（持仓 & 交易记录）？",
        default=False,
        style=questionary.Style(
            [
                ("qmark", "fg:cyan bold"),
                ("question", "fg:yellow bold"),
                ("answer", "fg:green"),
                ("pointer", "fg:cyan"),
                ("highlighted", "fg:cyan"),
            ]
        ),
    ).ask()
    if do_sync is None:
        return None

    if do_sync:
        try:
            _do_sync_holdings(sheet_id, DEFAULT_CONFIG.get("portfolio", {}).get("worksheet", "total"))
        except typer.Exit:
            return None
        except SystemExit:
            return None

    from tradingagents.portfolio import PortfolioRepository

    repo = PortfolioRepository()
    if repo.exists():
        try:
            portfolio = repo.load()
            return {
                ticker: {
                    "ticker": ticker,
                    "shares": h.shares,
                    "avg_cost": h.avg_cost,
                    "market_price": h.market_price,
                    "pnl_pct": h.pnl_pct,
                    "weight": h.weight,
                    "grid_strategy": h.grid_strategy,
                    "name": h.name,
                }
                for ticker, h in portfolio.holdings.items()
            }
        except Exception:
            pass
    return None


def _do_sync_holdings(sheet_id: str | None, worksheet: str):
    """Core sync logic shared by sync-holdings and sh commands."""
    from tradingagents.portfolio import PortfolioRepository, PortfolioSyncService

    _sheet_id = sheet_id or DEFAULT_CONFIG.get("portfolio", {}).get("sheet_id")
    if not _sheet_id:
        console.print("[red]No sheet ID configured. Use --sheet-id or set PORTFOLIO_SHEET_ID in .env[/red]")
        raise typer.Exit(1)

    _worksheet = worksheet or DEFAULT_CONFIG.get("portfolio", {}).get("worksheet", "total")

    try:
        sync_service = PortfolioSyncService(sheet_id=_sheet_id, worksheet=_worksheet)
        portfolio = sync_service.sync()

        repo = PortfolioRepository()
        repo.save(portfolio)

        console.print(f"[green]✓ Synced {len(portfolio.holdings)} holdings[/green]")
        console.print(f"  Local cache: [dim]{repo.path}[/dim]")
        console.print(f"  Updated at:  [dim]{portfolio.metadata.updated_at}[/dim]")

        # Show summary table
        from rich.table import Table

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Ticker", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Shares", justify="right")
        table.add_column("Avg Cost", justify="right")
        table.add_column("P&L%", justify="right")

        for ticker, h in sorted(portfolio.holdings.items()):
            pnl_str = "N/A"
            pnl_style = "white"
            if h.pnl_pct is not None:
                sign = "+" if h.pnl_pct >= 0 else ""
                pnl_str = f"{sign}{h.pnl_pct * 100:.2f}%"
                pnl_style = "red" if h.pnl_pct >= 0 else "green"
            table.add_row(
                ticker,
                h.name or "—",
                f"{h.shares:,.0f}",
                f"{h.avg_cost:.3f}",
                pnl_str,
                style=pnl_style,
            )
        console.print(table)

        if portfolio.summary:
            console.print(
                f"\n[bold]Summary:[/bold] {portfolio.summary.get('total_holdings', 0)} holdings, "
                f"invested {_format_money(portfolio.summary.get('total_invested', 0))}, "
                f"value {_format_money(portfolio.summary.get('total_market_value', 0))}"
            )
    except Exception as exc:
        console.print(f"[red]Sync failed: {exc}[/red]")
        raise typer.Exit(1) from None


@app.command(name="sync-holdings")
def sync_holdings_command(
    sheet_id: str | None = typer.Option(
        None,
        "--sheet-id",
        help="Google Sheet ID (defaults to portfolio.sheet_id in config).",
    ),
    worksheet: str = typer.Option(
        "total",
        "--worksheet",
        help="Worksheet/tab name inside the Google Sheet.",
    ),
):
    """Sync holdings from Google Sheet to local JSON cache."""
    _do_sync_holdings(sheet_id, worksheet)


@app.command(name="sh")
def sync_holdings_short_command(
    sheet_id: str | None = typer.Option(
        None,
        "--sheet-id",
        help="Google Sheet ID (defaults to portfolio.sheet_id in config).",
    ),
    worksheet: str = typer.Option(
        "total",
        "--worksheet",
        help="Worksheet/tab name inside the Google Sheet.",
    ),
):
    """Shortcut for sync-holdings."""
    _do_sync_holdings(sheet_id, worksheet)


def _do_sync_transactions(sheet_id: str | None, worksheet: str):
    """Core sync logic for transaction history."""
    from tradingagents.portfolio import (
        Portfolio,
        PortfolioRepository,
        TransactionSyncService,
    )

    _sheet_id = sheet_id or DEFAULT_CONFIG.get("portfolio", {}).get("transaction_sheet_id")
    if not _sheet_id:
        console.print(
            "[red]No transaction sheet ID configured. Use --sheet-id or set TRANSACTION_SHEET_ID in .env[/red]"
        )
        raise typer.Exit(1)

    _worksheet = worksheet or DEFAULT_CONFIG.get("portfolio", {}).get("transaction_worksheet", "stock transitions")

    try:
        sync_service = TransactionSyncService(sheet_id=_sheet_id, worksheet=_worksheet)
        transactions = sync_service.sync()

        repo = PortfolioRepository()
        portfolio = repo.load() if repo.exists() else Portfolio()

        portfolio.transactions = transactions
        repo.save(portfolio)

        console.print(f"[green]✓ Synced {len(transactions)} transactions[/green]")
        console.print(f"  Local cache: [dim]{repo.path}[/dim]")
    except Exception as exc:
        console.print(f"[red]Sync failed: {exc}[/red]")
        raise typer.Exit(1) from None


@app.command(name="sync-transactions")
def sync_transactions_command(
    sheet_id: str | None = typer.Option(
        None,
        "--sheet-id",
        help="Google Sheet ID for transaction history (defaults to config).",
    ),
    worksheet: str = typer.Option(
        "stock transitions",
        "--worksheet",
        help="Worksheet/tab name for transaction history.",
    ),
):
    """Sync transaction history from Google Sheet to local JSON cache."""
    _do_sync_transactions(sheet_id, worksheet)


@app.command(name="st")
def sync_transactions_short_command(
    sheet_id: str | None = typer.Option(
        None,
        "--sheet-id",
        help="Google Sheet ID for transaction history (defaults to config).",
    ),
    worksheet: str = typer.Option(
        "stock transitions",
        "--worksheet",
        help="Worksheet/tab name for transaction history.",
    ),
):
    """Shortcut for sync-transactions."""
    _do_sync_transactions(sheet_id, worksheet)


@app.command(name="show-holdings")
def show_holdings_command(
    history: bool = typer.Option(
        False,
        "--history",
        help="Show recent transaction history for each holding.",
    ),
):
    """Display holdings from local cache (no network call)."""
    from tradingagents.portfolio import PortfolioRepository

    repo = PortfolioRepository()
    if not repo.exists():
        console.print("[yellow]No local holdings found. Run 'uv run tradingagents sync-holdings' first.[/yellow]")
        raise typer.Exit(1)

    try:
        portfolio = repo.load()
        console.print(f"[green] Holdings from local cache[/green] ([dim]{repo.path}[/dim])")
        console.print(f"  Last updated: [dim]{portfolio.metadata.updated_at or 'unknown'}[/dim]\n")

        from rich.table import Table
        from rich.text import Text

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Ticker", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Shares", justify="right")
        table.add_column("Avg Cost", justify="right")
        table.add_column("Market", justify="right")
        table.add_column("P&L%", justify="right")
        table.add_column("Weight", justify="right")

        for ticker, h in sorted(portfolio.holdings.items()):
            pnl_text = Text("N/A")
            if h.pnl_pct is not None:
                sign = "+" if h.pnl_pct >= 0 else ""
                pnl_str = f"{sign}{h.pnl_pct * 100:.2f}%"
                pnl_color = "red" if h.pnl_pct >= 0 else "green"
                pnl_text = Text(pnl_str, style=pnl_color)
            weight_str = "N/A"
            if h.weight is not None:
                weight_str = f"{h.weight * 100:.2f}%"
            table.add_row(
                ticker,
                h.name or "—",
                f"{h.shares:,.0f}",
                f"{h.avg_cost:.3f}",
                f"{h.market_price:.3f}" if h.market_price else "N/A",
                pnl_text,
                weight_str,
            )
        console.print(table)

        if history and portfolio.transactions:
            console.print("\n[bold]近期交易记录[/bold] (各标的最近 10 笔)")

            # Normalise ticker for cross-source matching (holdings may have
            # .SS/.SZ/.BJ suffix while transaction sheet stores bare codes).
            def _normalise_tx_ticker(tx_ticker: str) -> str:
                return tx_ticker.split(".")[0] if "." in tx_ticker else tx_ticker

            for ticker, h in sorted(portfolio.holdings.items()):
                ticker_base = _normalise_tx_ticker(ticker)
                txs = [t for t in portfolio.transactions if _normalise_tx_ticker(t.ticker) == ticker_base]
                if not txs:
                    continue
                txs_sorted = sorted(txs, key=lambda t: t.date, reverse=True)
                total = len(txs_sorted)
                showing = min(total, 10)
                omitted = f"  [dim]... 还有 {total - showing} 笔 ...[/dim]" if total > 10 else ""
                console.print(f"\n[cyan]{ticker}[/cyan] {h.name or ''}  [dim]({total} 笔)[/dim]")
                for t in txs_sorted[:10]:
                    fee_str = f" 手续费 {t.fee:.2f}" if t.fee else ""
                    tag_str = f" [{t.tag}]" if t.tag else ""
                    console.print(
                        f"  [dim]{t.date}[/dim] {t.action} {abs(t.shares):,.0f} 股 @ {t.price:.3f}{fee_str}{tag_str}"
                    )
                if omitted:
                    console.print(omitted)
    except Exception as exc:
        console.print(f"[red]Failed to load holdings: {exc}[/red]")
        raise typer.Exit(1) from None


def _format_money(value: float) -> str:
    """Format a monetary value with Chinese-friendly units."""
    if value is None:
        return "N/A"
    abs_v = abs(value)
    if abs_v >= 1e8:
        return f"{value / 1e8:.2f}亿"
    if abs_v >= 1e4:
        return f"{value / 1e4:.2f}万"
    return f"{value:,.2f}"


@app.callback(invoke_without_command=True)
def default(ctx: typer.Context):
    """Default to interactive analyze mode when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        # Typer Option defaults are OptionInfo objects; we must pass real
        # Python values when calling the command function directly.
        analyze(
            checkpoint=True,
            clear_checkpoints=False,
            config=None,
            profile=None,
            watchlist=None,
            tickers=None,
            output_dir=None,
            holdings_sheet=None,
            holdings_worksheet="total",
            sync_holdings=False,
            workers=1,
        )


if __name__ == "__main__":
    app()

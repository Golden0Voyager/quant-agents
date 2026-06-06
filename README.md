<p align="center">
  <img src="assets/TauricResearch.png" style="width: 60%; height: auto;">
</p>

# Trading-Agents-A-Share: Optimized Multi-Agent LLM Financial Trading Framework for China A-Share Market

[![A-Share Optimized](https://img.shields.io/badge/A--Share-Optimized-10B981?style=for-the-badge&logo=chinanet&logoColor=white)](#-key-a-share--production-enhancements)
[![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph-6366F1?style=for-the-badge&logo=chainlink&logoColor=white)](https://github.com/langchain-ai/langgraph)
[![arXiv](https://img.shields.io/badge/arXiv-2412.20138-B31B1B?style=for-the-badge&logo=arxiv)](https://arxiv.org/abs/2412.20138)
[![License](https://img.shields.io/badge/License-MIT-374151?style=for-the-badge)](LICENSE)

`Trading-Agents-A-Share` is a highly optimized, production-grade customized fork of **TradingAgents** (the state-of-the-art multi-agent financial trading framework originally published by Tauric Research on NeurIPS/arXiv).

This fork is specifically tailored to address the unique market dynamics, data feeds, and local execution challenges of the **China A-Share (沪深京) Stock Market**, making it a robust platform for domestic quantitative AI research.

---

## 🚀 Key A-Share & Production Enhancements
*Below are the core engineering enhancements and optimizations introduced in this fork to adapt the original framework for domestic A-Share trading:*

## News
- [2026-05] **TradingAgents v0.2.5** released with the grounded Sentiment Analyst, GPT-5.5 etc. model coverage, Qwen/GLM/MiniMax dual-region support, `TRADINGAGENTS_*` env-var configurability with API-key auto-detection, remote Ollama support, non-US alpha benchmarks, and ticker path-traversal hardening. See [CHANGELOG.md](CHANGELOG.md) for the full list.
- [2026-04] **TradingAgents v0.2.4** released with structured-output agents (Research Manager, Trader, Portfolio Manager), LangGraph checkpoint resume, persistent decision log, DeepSeek/Qwen/GLM/Azure provider support, Docker, and a Windows UTF-8 encoding fix.
- [2026-03] **TradingAgents v0.2.3** released with multi-language support, GPT-5.4 family models, unified model catalog, backtesting date fidelity, and proxy support.
- [2026-03] **TradingAgents v0.2.2** released with GPT-5.4/Gemini 3.1/Claude 4.6 model coverage, five-tier rating scale, OpenAI Responses API, Anthropic effort control, and cross-platform stability.
- [2026-02] **TradingAgents v0.2.0** released with multi-provider LLM support (GPT-5.x, Gemini 3.x, Claude 4.x, Grok 4.x) and improved system architecture.
- [2026-01] **Trading-R1** [Technical Report](https://arxiv.org/abs/2509.11420) released, with [Terminal](https://github.com/TauricResearch/Trading-R1) expected to land soon.

### 1. 📊 A-Share Institutional Fund Flow Integration (`get_fund_flow` Routing)
* **The Optimization**: Integrated custom routing logic to ingest domestic institutional "Smart Money" (主力资金、北向资金、大单流向) flow feeds.
* **Why it matters**: In the China A-Share market, retail-heavy sentiment and institutional capital tracking are extremely strong price-driving signals. This integration adds a crucial metric to the News/Sentiment Analyst agents.

### 2. ⚡ Actionable Trading Signal Enforcement (Entry/Stop/Size Constraints)
* **The Optimization**: Enforced a strict structured schema in the **Trader Agent**'s decision module, requiring it to emit precise values for **Entry Price (建仓点)**, **Stop-Loss (止损点)**, and **Position Size (仓位比例)** in every proposed trade.
* **Why it matters**: Moves the multi-agent system from abstract, qualitative investment debates ("bullish/bearish") into concrete, actionable, and testable trade execution orders.

### 3. 🛡️ SOE & Local Anti-Hallucination Context Sanitizer
* **The Optimization**: Implemented global company-name context injection and post-execution regex filtering across critical analyst roles (Industry Analyst, Governance Analyst).
* **Why it matters**: LLMs frequently hallucinate complex Chinese State-Owned Enterprise (SOE) titles, stock symbols, and localized acronyms. This sanitizer ensures 100% data sanity before any report is written to disk.

### 4. 🇨🇳 Local LLM Native Validation & Standardized Clients
* **The Optimization**: Hardened validation clients for cost-efficient Chinese domestic LLMs, specifically supporting **Qwen (Alibaba DashScope)**, **GLM (Zhipu)**, and **DeepSeek-R1** endpoints.
* **Why it matters**: Standardizes the routing and parsing rules for domestic reasoning models, enabling high-performance local inference at a fraction of the cost of western APIs.

### 5. ⏳ TUI & Batch Checkpoint Resume
* **The Optimization**: Optimized command-line shell script behaviors to support bulk TUI backtests and robust state recovery through a unified SQLite cache.
* **Why it matters**: Long backtesting sessions over multiple A-share stocks are vulnerable to API failures. This recovery pipeline ensures interrupted tasks resume seamlessly from the last successful step.

---

## 🏛️ TradingAgents Framework Overview

The core architecture mimics the hierarchy of professional asset management firms. Under a unified state machine managed by **LangGraph**, specialized LLM agents debate, critique, and authorize trading decisions:

```
                  ┌────────────────────────┐
                  │   Fundamental Analyst  │
                  └───────────┬────────────┘
                              ▼
┌──────────────┐  ┌────────────────────────┐  ┌──────────────┐
│ Technical    ├─►│    Research Managers   │◄─┤ News & Fund  │
│ Analyst      │  │   (Bull & Bear Debate) │  │ Flow Analyst │
└──────────────┘  └───────────┬────────────┘  └──────────────┘
                              ▼
                  ┌────────────────────────┐
                  │      Trader Agent      │
                  │ (Concrete Price/Size)  │
                  └───────────┬────────────┘
                              ▼
                  ┌────────────────────────┐
                  │     Risk & Portfolio   │
                  │         Manager        │
                  └────────────────────────┘
```

* **Analyst Team**: Fundamental Analyst (evaluates balance sheets), Technical Analyst (MACD, RSI indicators), News & Fund Flow Analyst (monitors domestic news and主力资金 flow).
* **Research Team**: Bullish and Bearish Research Managers who debate the analysts' outputs to balance upside potential against inherent localized market risks.
* **Trader Agent**: Combines the synthesized reports to formulate trade proposals (Entry, Stop-Loss, and Sizing).
* **Risk & Portfolio Manager**: Executes risk checks against overall portfolio volatility and approves/rejects the final transaction before writing it to the simulated exchange.

Our framework decomposes complex trading tasks into specialized roles.

### Analyst Team
- Fundamentals Analyst: Evaluates company financials and performance metrics, identifying intrinsic values and potential red flags.
- Sentiment Analyst: Aggregates news headlines, StockTwits, and Reddit chatter into a single sentiment read to gauge short-term market mood.
- News Analyst: Monitors global news and macroeconomic indicators, interpreting the impact of events on market conditions.
- Technical Analyst: Utilizes technical indicators (like MACD and RSI) to detect trading patterns and forecast price movements.

<p align="center">
  <img src="assets/analyst.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

### Researcher Team
- Comprises both bullish and bearish researchers who critically assess the insights provided by the Analyst Team. Through structured debates, they balance potential gains against inherent risks.

<p align="center">
  <img src="assets/researcher.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

### Trader Agent
- Composes reports from the analysts and researchers to make informed trading decisions, determining the timing and magnitude of trades.

<p align="center">
  <img src="assets/trader.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

### Risk Management and Portfolio Manager
- Continuously evaluates portfolio risk by assessing market volatility, liquidity, and other risk factors. The risk management team evaluates and adjusts trading strategies, providing assessment reports to the Portfolio Manager for final decision.
- The Portfolio Manager approves/rejects the transaction proposal. If approved, the order will be sent to the simulated exchange and executed.

<p align="center">
  <img src="assets/risk.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

---

## ⚡ Installation & CLI

### Installation

Clone the repository:
```bash
git clone https://github.com/Golden0Voyager/Trading-Agents-A-Share.git
cd Trading-Agents-A-Share
```

Create a virtual environment:
```bash
conda create -n tradingagents-env python=3.13
conda activate tradingagents-env
```

Install the package in editable mode:
```bash
pip install -e .
```

### Config Environment Variables

Alternatively, run with Docker:
```bash
cp .env.example .env  # add your API keys
docker compose run --rm tradingagents
```

For local models with Ollama:
```bash
docker compose --profile ollama run --rm tradingagents-ollama
```

### Required APIs

TradingAgents supports multiple LLM providers. Set the API key for your chosen provider:

```bash
export OPENAI_API_KEY=...          # OpenAI (GPT)
export GOOGLE_API_KEY=...          # Google (Gemini)
export ANTHROPIC_API_KEY=...       # Anthropic (Claude)
export XAI_API_KEY=...             # xAI (Grok)
export DEEPSEEK_API_KEY=...        # DeepSeek
export DASHSCOPE_API_KEY=...       # Qwen — International (dashscope-intl.aliyuncs.com)
export DASHSCOPE_CN_API_KEY=...    # Qwen — China (dashscope.aliyuncs.com)
export ZHIPU_API_KEY=...           # GLM via Z.AI (international)
export ZHIPU_CN_API_KEY=...        # GLM via BigModel (China, open.bigmodel.cn)
export MINIMAX_API_KEY=...         # MiniMax — Global (api.minimax.io, M2.x, 204K ctx)
export MINIMAX_CN_API_KEY=...      # MiniMax — China (api.minimaxi.com, M2.x, 204K ctx)
export OPENROUTER_API_KEY=...      # OpenRouter
export ALPHA_VANTAGE_API_KEY=...   # Alpha Vantage

# A-Share domestic providers
export SENSENOVA_API_KEY=...       # SenseNova (DeepSeek-R1)
export KIMI_API_KEY=...            # Kimi (Moonshot)
```

For enterprise providers (e.g. Azure OpenAI, AWS Bedrock), copy `.env.enterprise.example` to `.env.enterprise` and fill in your credentials.

For local models, configure Ollama with `llm_provider: "ollama"`. The default endpoint is `http://localhost:11434/v1`; set `OLLAMA_BASE_URL` to point at a remote `ollama-serve`. Pull models with `ollama pull <name>`, and pick "Custom model ID" in the CLI for any model not listed by default.

Copy `.env.example` to `.env` and fill in your keys:
```bash
cp .env.example .env
```

Support for major domestic and international providers:
```bash
export DASHSCOPE_API_KEY=...       # Qwen (Alibaba DashScope)
export ZHIPU_API_KEY=...           # GLM (Zhipu)
export DEEPSEEK_API_KEY=...        # DeepSeek
export SENSENOVA_API_KEY=...       # SenseNova (DeepSeek-R1)
export OPENAI_API_KEY=...          # OpenAI (GPT-4o)
export ANTHROPIC_API_KEY=...       # Anthropic (Claude)
```

Run the CLI:
```bash
tradingagents          # installed command
python -m cli.main     # alternative: run directly from source
```
You will see a screen where you can select your desired tickers, analysis date, LLM provider, research depth, and more.

### Markets and tickers

TradingAgents works with any market Yahoo Finance covers, using the exchange-suffixed ticker. Company identity and the alpha benchmark resolve automatically per market.

- US: `AAPL`, `SPY`
- Hong Kong: `0700.HK` · Tokyo: `7203.T` · London: `AZN.L`
- India: `RELIANCE.NS`, `.BO` · Canada: `.TO` · Australia: `.AX`
- China A-shares: Shanghai `.SS`, Shenzhen `.SZ` (e.g. `600519.SS` for Kweichow Moutai)
- Crypto: `BTC-USD`, `ETH-USD`

<p align="center">
  <img src="assets/cli/cli_init.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

An interface will appear showing results as they load, letting you track the agent's progress as it runs.

<p align="center">
  <img src="assets/cli/cli_news.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

<p align="center">
  <img src="assets/cli/cli_transaction.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

## TradingAgents Package

### Implementation Details

We built TradingAgents with LangGraph to ensure flexibility and modularity. The framework supports multiple LLM providers: OpenAI, Google, Anthropic, xAI, DeepSeek, Qwen (Alibaba DashScope, international and China endpoints), GLM (Zhipu), MiniMax (global + China), OpenRouter, Ollama for local models, and Azure OpenAI for enterprise.

### Python Usage

To use TradingAgents inside your code, you can import the `tradingagents` module and initialize a `TradingAgentsGraph()` object. The `.propagate()` function will return a decision. You can run `main.py`, here's also a quick example:

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

ta = TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG.copy())

# forward propagate
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"        # openai, google, anthropic, xai, deepseek, qwen, qwen-cn, glm, glm-cn, minimax, minimax-cn, openrouter, ollama, azure
config["deep_think_llm"] = "gpt-5.5"     # Model for complex reasoning
config["quick_think_llm"] = "gpt-5.4-mini" # Model for quick tasks
config["max_debate_rounds"] = 2

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

See `tradingagents/default_config.py` for all configuration options.

## Persistence and Recovery

TradingAgents persists two kinds of state across runs.

### Decision log

The decision log is always on. Each completed run appends its decision to `~/.tradingagents/memory/trading_memory.md`. On the next run for the same ticker, TradingAgents fetches the realised return (raw and alpha vs SPY), generates a one-paragraph reflection, and injects the most recent same-ticker decisions plus recent cross-ticker lessons into the Portfolio Manager prompt, so each analysis carries forward what worked and what didn't.

Override the path with `TRADINGAGENTS_MEMORY_LOG_PATH`.

### Checkpoint resume

Checkpoint resume is opt-in via `--checkpoint`. When enabled, LangGraph saves state after each node so a crashed or interrupted run resumes from the last successful step instead of starting over. On a resume run you will see `Resuming from step N for <TICKER> on <date>` in the logs; on a new run you will see `Starting fresh`. Checkpoints are cleared automatically on successful completion.

Per-ticker SQLite databases live at `~/.tradingagents/cache/checkpoints/<TICKER>.db` (override the base with `TRADINGAGENTS_CACHE_DIR`). Use `--clear-checkpoints` to reset all of them before a run.

---

## 📈 CLI Usage & Backtesting

Launch the interactive Terminal User Interface (TUI):
```bash
tradingagents
# Or run directly from source:
python -m cli.main
```

### Batch Mode & Checkpoint Resume

To run high-volume backtests with automated SQLite session persistence:
```bash
# Run with active checkpoint tracking
tradingagents analyze --checkpoint

# Reset cached states before starting
tradingagents analyze --clear-checkpoints
```

Decisions are persistently logged into `~/.tradingagents/memory/trading_memory.md`, which the Portfolio Manager automatically reviews on subsequent runs for historical reflection.

## Reproducibility

TradingAgents is LLM-driven, so two runs of the same ticker and date can differ. This is expected for a research tool built on language models, not a defect. The variation comes from a few distinct sources, and it helps to separate them.

Language model sampling is non-deterministic. Even at a fixed temperature, providers do not guarantee byte-identical output across calls, and reasoning models (the default GPT-5.x family, and any thinking-mode model) vary the most because their internal reasoning is itself sampled.

Live data moves. News, StockTwits, and Reddit return different content as time passes, so a run today sees different inputs than a run last week even for the same historical trade date. Pin the analysis date to hold the price and indicator window fixed, but the social and news sources still reflect "now".

To reduce variation you can lower the sampling temperature. Set `temperature` in your config (or `TRADINGAGENTS_TEMPERATURE` in `.env`); lower values make models that honor it more repeatable. Reasoning models largely ignore temperature, so for tighter reproducibility pair a low temperature with a non-reasoning model such as `gpt-4.1`.

```python
config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"
config["deep_think_llm"] = "gpt-4.1"      # non-reasoning model honors temperature
config["quick_think_llm"] = "gpt-4.1"
config["temperature"] = 0.0
```

What does not vary anymore: the analyzed company identity is resolved deterministically from the ticker before any agent runs, and the market analyst grounds exact price and indicator claims in a verified data snapshot. Earlier reports of "different companies" or fabricated price levels across runs are addressed by these two mechanisms.

Backtest results are not guaranteed to match any published figure. Returns depend on the model, the temperature, the date range, data quality, and the sampling above. Treat the framework as a research scaffold for studying multi-agent analysis, not as a strategy with a fixed, replicable return.

## Contributing

Contributions are welcome: bug fixes, documentation, and feature ideas; past contributions are credited per release in [`CHANGELOG.md`](CHANGELOG.md).
We also welcome contributions to further enhance A-Share localizations (e.g., adding local technical indicators, refining DeepSeek reasoning prompts, or writing new data ingestion connectors).

---

## 📄 Academic Citation
If you find this framework useful in your financial AI or quantitative research, please cite the original foundational work:

```bibtex
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework},
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138},
}
```

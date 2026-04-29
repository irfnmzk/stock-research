# stock-research

An [AgentSkill](https://agentskills.io) for IDX (Indonesian Stock Exchange) research. Designed for LLM agents to fetch market data, compute technical indicators, track whale/institutional activity, screen for entry signals, and generate daily analyst reports.

Built on top of the Stockbit API. The Python layer handles data and computation, the LLM layer interprets results into narrative reports.

## What it does

- Daily price data, foreign flow, broker activity for top 300 IDX stocks
- Technical indicators: EMA, RSI, MACD, Bollinger Bands, ATR, volume ratio
- Bandarmology: track smart broker accumulation/distribution patterns
- Support/resistance detection with breakout confirmation
- Sector rotation ranking and momentum scoring
- Composable screener rules (define filters in YAML, no code needed)
- Signal scoring engine that combines all signals into actionable scores
- Macro regime assessment (USD/IDR, US 10Y, BI Rate, aggregate foreign flow)
- Portfolio tracking with scaling-in (tranche) support
- Candlestick chart rendering with indicator overlays

## Quick setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
# Clone
git clone git@github.com:irfnmzk/stock-research.git
cd stock-research

# Install dependencies
uv sync

# Set up credentials
mkdir -p data
echo "STOCKBIT_JWT=<your-token>" > data/.env

# Initialize database
uv run python main.py fetch-companies
uv run python main.py refresh-pool
uv run python main.py fetch-pool

# Verify
uv run python main.py screen --pool
```

The Stockbit JWT must be manually extracted from browser localStorage after logging into stockbit.com.

## For agents

Read `SKILL.md` for the full command reference and setup guide. Reference docs in `references/` cover the database schema, pipeline architecture, scoring algorithm, config options, and API quirks.

## License

MIT

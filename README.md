# algo-desk — AI Multi-Agent Trading Desk

An open-source, multi-agent algorithmic trading platform built on the **Claude Agent SDK**,
with a **Streamlit GUI**. Agents run **autonomously against paper money**; any **live** order
requires **human approval** in the GUI. Real live execution is intentionally unwired.

## Architecture

```
Streamlit GUI  ──reads runs/──  Orchestrator (scheduler)
                                     │  per symbol:
   analysts (parallel) ─► bull/bear debate ─► risk gate (veto) ─► portfolio manager ─► Order
        │                                                                                │
        └──────── market-data MCP (ccxt + yfinance, massive fallback) ───────────────────┘
                                     │
                          Executor (deterministic) ─► PaperBroker  (LiveBroker = approval-gated)
```

Agents speak in typed pydantic contracts (`Signal → debate → RiskVerdict → Order → Fill`).
Deterministic math (indicators, P&L, ATR risk sizing, fills) stays in Python; agents only reason.

## Setup

```bash
cd algo-desk
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
# .env must contain ANTHROPIC_API_KEY (see .env.example)
```

## Run

```bash
# One decision cycle for one symbol (prints the full pipeline):
.venv/bin/python -m src.decision_core "BTC/USD"

# One autonomous cycle over the universe (decide + execute + persist):
.venv/bin/python -m src.orchestrator

# Continuous autonomous loop (paper), every settings.execution.schedule_minutes:
.venv/bin/python -c "import anyio; from src.orchestrator import run_forever; anyio.run(run_forever)"

# The GUI (dashboard + approval queue):
.venv/bin/python -m streamlit run app/streamlit_app.py

# Backtest the baseline strategy (the bar agents must beat):
.venv/bin/python -m src.backtest.engine
```

## Safety
- **Paper-only autonomy.** Live orders park in an approval queue; `LiveBroker` refuses to trade.
- **Kill-switch:** create `runs/KILL_SWITCH` to halt all execution instantly.
- **Risk gate:** ATR-based 1%/trade sizing, max-position cap, and a max-drawdown halt — enforced
  in Python, not by the LLM.
- **Idempotency:** every order has an id; never filled twice.
- **Audit trail:** every decision → `runs/decision_*.json`; desk state → `runs/desk_state.json`.

## Documentation
- [`docs/AGENTS.md`](docs/AGENTS.md) — every agent: role, tools, model, input/output contracts.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the non-agent half: data, indicators,
  backtester, risk, execution, persistence, and the key design decisions.
- Each `src/**.py` has a top-of-module docstring explaining the *why*.

## Config
Edit `config/settings.yaml`: universe, crypto exchange, model routing (analysts vs. decision),
risk limits, execution mode + schedule.

## Going live (later, deliberately)
Implement a `ccxt`/Alpaca adapter with the `place_order(order, ref_price)` signature in
`src/mcp_servers/broker.py`. Nothing else changes.
```
```

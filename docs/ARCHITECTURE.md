# Architecture — algo-desk (the non-agent half)

Companion to [`AGENTS.md`](AGENTS.md). That document covers the *brains* (the LLM agents); this
one covers everything else: how data gets in, how indicators and backtests are computed, how risk
and portfolio accounting work, and how orders actually execute and persist. **Design rule of the
whole system: deterministic math and irreversible actions live in Python; agents only reason.**

---

## System layers (bottom to top)

```
┌──────────────────────────── GUI (app/streamlit_app.py) ─────────────────────────────┐
│  reads runs/* · controls: run cycle, paper/live, kill-switch, approve/reject orders   │
└───────────────────────────────────────┬───────────────────────────────────────────────┘
┌──────── Orchestration ────────────────┴───────────────────────────────────────────────┐
│  orchestrator.py (schedule + loop)  →  decision_core.py (wire agents)  →  state.py      │
└───────────────────────────────────────┬───────────────────────────────────────────────┘
┌──────── Execution ────────────────────┴───────────────────────────────────────────────┐
│  agents/execution.py (Executor: safety rules)  →  mcp_servers/broker.py (Paper/Live)    │
└───────────────────────────────────────┬───────────────────────────────────────────────┘
┌──────── Quant core ───────────────────┴───────────────────────────────────────────────┐
│  layer/indicators.py · strategy.py · backtest/engine.py + metrics.py                    │
│  layer/portfolio.py (accounting) · layer/risk.py (sizing + limits)                      │
└───────────────────────────────────────┬───────────────────────────────────────────────┘
┌──────── Data ─────────────────────────┴───────────────────────────────────────────────┐
│  layer/datafeed.py (ccxt + yfinance, parquet cache)  +  mcp_servers/market_data.py      │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Data layer

### `layer/datafeed.py`
The single source of market data. Hides two very different vendors behind one interface:
- crypto (symbol contains `/`) → **ccxt** (default exchange **kraken**; Binance is geo-blocked in the US)
- equities/ETFs/FX → **yfinance**

| Function | Returns | Notes |
|---|---|---|
| `get_quote(symbol)` | `Quote` | Latest price; source-tagged |
| `get_bars(symbol, timeframe, start, end)` | DataFrame | **Canonical OHLCV**: UTC index, `[open,high,low,close,volume]`, identical shape for crypto & equities |

**Parquet cache** (`data/*.parquet`): historical bars are immutable, so a covered request is served
from disk — fast, rate-limit-safe, and **reproducible** (the same query always returns the same
bars, which is part of avoiding look-ahead bias). Crypto fetches paginate via ccxt `fetch_ohlcv`;
equities use `yfinance.history`.

### `mcp_servers/market_data.py`
Wraps the datafeed as the **agent-facing tool surface**. Two design rules: tools return **compact
summaries** (not raw candle dumps — agents can't reason over 178 rows and it wastes tokens), and
heavy computation stays in Python.

Tools: `get_quote`, `get_recent_bars`, `get_fundamentals`, `get_news`.
Scope lists: `TECHNICAL_TOOLS`, `FUNDAMENTAL_TOOLS`, `SENTIMENT_TOOLS` (each analyst gets a subset).

---

## 2. Quant core

### `layer/indicators.py`
Pure, dependency-free technical indicators (hand-rolled because `pandas-ta` breaks on pandas 3.0):
`sma, ema, rsi, macd, atr, bollinger`, and `add_indicators(bars)` which appends the standard
columns. NaNs in the warm-up window (first `n-1` bars) are correct, not bugs. ATR is the key one —
it drives risk-based position sizing later.

### `strategy.py`
`crossover_strategy(bars, fast, slow)` — the deterministic **baseline** (long when fast MA > slow
MA, else flat). Long-only, pure function, no look-ahead. This is the **benchmark every agent must
beat**. Returns a frame with `position` (0/1) and discrete `signal` (buy/sell/hold) columns.

### `backtest/engine.py` + `backtest/metrics.py`  — the referee
`run_backtest(bars, position, ...)` consumes a **target-position series** and simulates an account.
Three correctness guarantees:
1. **No look-ahead** — `position.shift(1)`; you act on bar N's signal at bar N+1.
2. **Real costs** — fees + slippage (bps) charged on every change in position.
3. **Same engine for everyone** — the baseline rule and (future) the agent desk both produce a
   position series and run through identical code, so comparisons are fair.

`metrics.py` scores the resulting equity curve: `total_return, cagr, sharpe, sortino,
max_drawdown, win_rate, exposure`. `BacktestResult.report()` prints the scorecard;
`buy_and_hold(bars)` is the reference benchmark.

### `layer/portfolio.py`  — accounting
`Portfolio` tracks `cash`, `positions{symbol: qty}`, and `peak_equity`. Pure bookkeeping:
`equity(prices)`, `drawdown(prices)`, `apply_fill(fill)`, `snapshot(prices)`. Agents never mutate
it — only the executor applies fills. `peak_equity` exists so the max-drawdown halt can fire.

### `layer/risk.py`  — sizing + hard limits
Deterministic guardrails the LLM cannot override.
- `RiskLimits.from_settings()` — loads `max_position_pct`, `per_trade_risk_pct`, `max_drawdown_halt_pct`.
- `size_position(equity, price, atr, limits)` — **risk-based sizing**:
  `risk_$ = equity × per_trade_risk_pct`; `stop_distance = 2 × ATR`;
  `qty = risk_$ / stop_distance`, then capped so position value ≤ `equity × max_position_pct`.
  → every trade risks the same dollar amount; jumpy assets get smaller positions.
- `drawdown_halt(current_drawdown, limits)` — True once losses hit the halt threshold.

---

## 3. Execution

### `mcp_servers/broker.py`
Adapters with one shared `place_order(order, ref_price) -> Fill` interface:
- **`PaperBroker`** — simulates a fill against the reference price with slippage + fees. No real money.
- **`LiveBroker`** — stub that **refuses to trade without an approval token** and is intentionally
  *not* wired to a real exchange. Going live = implement this one class.
- `make_broker(mode, ...)` picks the adapter from `execution.mode`.

### `agents/execution.py`  — the Executor (deterministic, not an LLM)
Runs an `Order` through three safety rules in order:
1. **Kill-switch** — if `runs/KILL_SWITCH` exists, nothing executes.
2. **Idempotency** — an `order.id` already processed is never filled again (safe retries/restarts).
3. **Approval gate** — `requires_approval` orders (live mode) are **parked in the pending queue**,
   not filled; paper orders auto-fill.

Successful fills update the `Portfolio` and are recorded to state. `approve(order_id, price)` /
`reject(order_id)` are the methods the GUI's buttons call (approve currently fills a **simulated**
paper order, clearly flagged, since live is unwired).

---

## 4. Orchestration & persistence

### `decision_core.py`
`decide(symbol, portfolio)` is the glue that runs the full pipeline for one symbol:
fetch bars + ATR → `run_all_analysts` → `run_debate` → confidence-weighted `_proposed_direction`
→ `run_risk_check` → `run_portfolio_manager` → `Order`. It logs a complete
`runs/decision_<symbol>_<ts>.json` (signals, debate, risk verdict, order) for audit/replay and the GUI.

### `orchestrator.py`
- `run_cycle(symbols, state)` — one pass over the universe: decide + execute each symbol, mark the
  portfolio to market, append an equity point, persist state.
- `run_forever()` — repeats `run_cycle` every `execution.schedule_minutes`. Autonomous in paper
  mode; parks orders for approval in live mode.

### `state.py`  — persistence
`DeskState` is the one JSON file (`runs/desk_state.json`) the loop and GUI share: `portfolio`,
`processed_order_ids` (idempotency), `pending_approvals`, `equity_history`, `fills`.
`DeskState.load()/save()` survive restarts. `kill_switch_active()` checks for `runs/KILL_SWITCH`.

### `settings.py`
`load_settings()` (cached) reads `config/settings.yaml`; `set_execution_mode(mode)` flips
paper/live from the GUI.

---

## 5. GUI — `app/streamlit_app.py`
A viewer over `runs/` plus the control surface. Streamlit reruns the whole script on every
interaction, so it just re-reads the JSON each time (no long-lived in-memory state to corrupt).
Panels: equity curve (Plotly), per-symbol decision drilldown (signals + bull/bear + risk verdict),
recent fills. Controls: Run-cycle, paper/live toggle, kill-switch, and the **Approve/Reject queue**
(the human-in-the-loop centerpiece, wired to `Executor.approve/reject`).

---

## The `runs/` directory (everything observable)

| File | Written by | Contains |
|---|---|---|
| `decision_<symbol>_<ts>.json` | `decision_core.decide()` | One full decision (signals, debate, risk, order) |
| `desk_state.json` | `state.DeskState.save()` | Portfolio, processed ids, pending approvals, equity history, fills |
| `KILL_SWITCH` (presence) | you / GUI | Halts all execution |

---

## Key design decisions (and why)

| Decision | Why |
|---|---|
| One canonical OHLCV frame for all assets | Nothing downstream branches on asset class |
| Agent tools return summaries, not raw bars | Token cost + agents can't reason over big tables |
| Indicators/P&L/risk in Python, not the LLM | Deterministic, free, instant, testable, un-hallucinatable |
| Backtester consumes a position series | The agent desk reuses the *same* engine → fair comparison |
| Sizing fixed in Python; agent picks direction | A model can't blow up the account by over-sizing |
| Execution is deterministic | Never improvise the irreversible act |
| Live needs human approval + unwired broker | Two independent barriers before real money moves |
| Everything logged to `runs/` | Auditable, replayable, and the GUI's data source |

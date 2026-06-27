# Agent Catalog — algo-desk

The authoritative reference for every agent in the trading desk: what it does, what it reads,
what it produces, and where its code lives. Read this alongside each file's top-of-module
docstring (the "why") and the data contracts in [`src/schemas.py`](../src/schemas.py).

> **Mental model.** An "agent" here = a Claude Agent SDK call with (a) a **system prompt** that
> defines its role, (b) a **scoped set of tools** it may call, and (c) a **structured output**
> it must produce (usually by *calling* a `submit_*` tool whose schema is a pydantic model).
> Deterministic math and the irreversible act of placing an order are **not** agents.

---

## The pipeline at a glance

```
 Technical  ┐
 Fundamental├─(parallel)→ Bull ⇄ Bear debate → Risk manager (veto) → Portfolio manager → Order
 Sentiment  ┘                                                                              │
                                                                   Executor (deterministic) → Fill
```

Data flows as typed contracts: `Signal` → debate text → `RiskVerdict` → `Order` → `Fill`
(all defined in [`src/schemas.py`](../src/schemas.py)).

---

## 1. Analyst agents (parallel specialists)

**File:** [`src/agents/analyst.py`](../src/agents/analyst.py) · **Model:** Sonnet 4.6
**Runner:** `run_analyst(...)` (one reusable function) · **Fan-out:** `run_all_analysts(symbol)`
**Output contract:** `Signal { symbol, direction(buy/sell/hold), confidence(0..1), rationale, analyst }`

All three analysts are the *same code* with a different system prompt + tool scope. Each delivers
its verdict by **calling `submit_signal`** (a tool whose input schema is the Signal), so structure
is guaranteed — no JSON parsing.

| Agent | Lens | Tools it can call | Judges on |
|---|---|---|---|
| **Technical analyst** | Price action | `get_quote`, `get_recent_bars` | Trend, momentum, recent moves |
| **Fundamental analyst** | Valuation | `get_fundamentals`, `get_quote` | P/E, margins, growth, 52-week range |
| **Sentiment analyst** | News tone | `get_news` | Bullish/bearish/mixed headlines |

**Why scoped tools?** The technical analyst literally cannot see news; the sentiment analyst
cannot see price. So when they agree, it is genuine corroboration from independent evidence —
the whole point of an ensemble. Config: `ANALYSTS` dict in the same file.

---

## 2. Researcher agents (bull vs. bear debate)

**File:** [`src/agents/researcher_bull_bear.py`](../src/agents/researcher_bull_bear.py) · **Model:** Sonnet 4.6
**Runner:** `run_debate(symbol, signals, rounds=1)`
**Output:** `{ "bull": <text>, "bear": <text> }` (plain prose, not a contract — the PM judges it)

| Agent | Role |
|---|---|
| **Bull researcher** | Argues the strongest case to BUY/hold, grounded only in the analyst signals; must name its biggest risk |
| **Bear researcher** | Argues the strongest case to SELL/avoid and **directly rebuts the bull** |

They have **no data tools** — they must reason from the evidence already on the table. With
`rounds=2`, each side gets a rebuttal turn (≈2× the cost). **Why debate?** A single "weigh the
evidence" prompt anchors on the loudest signal; splitting into adversaries forces both sides of
the trade into the open before a decision is made.

---

## 3. Risk manager (the veto gate)

**File:** [`src/agents/risk_manager.py`](../src/agents/risk_manager.py) · **Model:** Opus 4.8
**Runner:** `run_risk_check(symbol, proposed_direction, debate, equity, price, atr, current_drawdown, limits)`
**Output contract:** `RiskVerdict { approved, reason, max_position_value }` + `SizingResult`
**Submit tool:** `submit_risk_verdict(decision, reason)`

Authority, hard → soft:
1. **Python drawdown halt** — if the account is past the halt threshold, REJECT *before the agent
   is even called*. The LLM cannot un-halt the desk.
2. **Python ATR sizing** — position size and its max value are computed deterministically
   (`src/layer/risk.size_position`). The agent never inflates it.
3. **Agent judgment** — given the proposed direction, the sized trade, and the bull/bear debate,
   the agent APPROVES or REJECTS with a reason. It may reject; it may not add risk.

**Why Opus here?** This is a high-stakes go/no-go gate, so it gets the stronger model (see model
routing in [`config/settings.yaml`](../config/settings.yaml)).

---

## 4. Portfolio manager (final decider)

**File:** [`src/agents/portfolio_manager.py`](../src/agents/portfolio_manager.py) · **Model:** Opus 4.8
**Runner:** `run_portfolio_manager(symbol, signals, debate, risk_verdict, sizing, current_position_qty, price, mode)`
**Output contract:** `Order { id, symbol, side(buy/sell), quantity, rationale, requires_approval }` or `None` (hold)
**Submit tool:** `submit_decision(action, rationale)`

The judge of the whole desk. Sees the analyst signals, the debate, and the risk verdict, then
makes ONE call: buy, sell, or hold. Enforced **in code** after the agent decides:
- a risk-REJECTED trade can never become a buy (downgraded to hold);
- **quantity comes from the deterministic ATR sizing** (buys) or the current position (exits) —
  the agent decides *direction*, Python decides *size*;
- `requires_approval=True` in live mode, so the executor parks it for human sign-off.

---

## 5. Hello agent (Phase-0 scaffold)

**File:** [`src/agents/hello_agent.py`](../src/agents/hello_agent.py) · **Model:** Sonnet 4.6
A standalone demo proving the toolchain (env + API key + data libs + tool-calling loop). Not part
of the live pipeline; keep it as the minimal example to copy when building a new agent.

---

## Deliberately NOT agents (deterministic Python)

| Component | File | Why no LLM |
|---|---|---|
| **Executor** | [`src/agents/execution.py`](../src/agents/execution.py) | Order placement must be exact, idempotent, kill-switch-aware. Never let a model improvise the irreversible act. |
| **Orchestrator** | [`src/orchestrator.py`](../src/orchestrator.py) | Schedules the loop, runs the pipeline per symbol, persists state. Pure control flow. |
| **Decision core** | [`src/decision_core.py`](../src/decision_core.py) | `decide()` wires analysts → debate → risk → PM and logs the result. The glue, not a brain. |

---

## The data contracts (what agents pass each other)

Defined in [`src/schemas.py`](../src/schemas.py):

| Contract | Produced by | Consumed by |
|---|---|---|
| `Signal` | analysts | researchers, risk manager, PM, vote |
| `RiskVerdict` | risk manager | portfolio manager |
| `Order` | portfolio manager | executor |
| `Fill` | broker/executor | portfolio accounting, GUI |

---

## How to add a new agent (e.g. a macro or on-chain analyst)

1. **Add a data tool** (if needed) in [`src/mcp_servers/market_data.py`](../src/mcp_servers/market_data.py)
   and add it to a `*_TOOLS` scope list.
2. **Add a config entry** to the `ANALYSTS` dict in [`src/agents/analyst.py`](../src/agents/analyst.py)
   with a system prompt + tool scope — that's a whole new analyst, no new runner code.
3. It is automatically picked up by `run_all_analysts()` and flows into the debate/vote.

For a non-analyst agent, copy the `submit_*` tool pattern from `risk_manager.py` (define a
per-call tool whose schema is your output contract, capture it in a closure).

---

## Where the rest of the documentation lives

- **Per-module "why":** the docstring at the top of every file in `src/`.
- **How to run / architecture:** [`../README.md`](../README.md).
- **Config & model routing:** [`../config/settings.yaml`](../config/settings.yaml).
- **Full build rationale, phase by phase:** the plan file at
  `~/.claude/plans/please-consider-yourself-as-glittery-wirth.md`.

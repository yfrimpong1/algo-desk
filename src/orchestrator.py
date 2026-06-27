"""Orchestrator — the autonomous desk loop.

Each cycle: for every symbol in the universe, run the decision core, then run the resulting
order through the executor; afterwards, mark the portfolio to market, append an equity point,
and persist state. `run_forever` repeats this on the configured schedule.

In paper mode this is fully autonomous. In live mode the executor parks orders for approval,
so the loop keeps running but no real trade happens without a human in the GUI.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import anyio
from dotenv import load_dotenv

from src.agents.execution import Executor
from src.decision_core import decide
from src.layer.datafeed import get_quote
from src.settings import load_settings
from src.state import DeskState

load_dotenv()


def _universe(settings: dict) -> list[str]:
    u = settings["universe"]
    return list(u.get("crypto", [])) + list(u.get("equities", []))


async def run_cycle(symbols: list[str] | None = None, state: DeskState | None = None) -> DeskState:
    settings = load_settings()
    symbols = symbols or _universe(settings)
    state = state or DeskState.load(settings["risk"]["starting_cash"])
    executor = Executor(state, settings)

    print(f"\n=== CYCLE {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%SZ} "
          f"| mode={settings['execution']['mode']} | symbols={symbols} ===")

    cycle_prices: dict[str, float] = {}
    for sym in symbols:
        dec = await decide(sym, state.portfolio)
        cycle_prices[sym] = dec.price
        action = dec.order.side.value.upper() if dec.order else "HOLD"
        print(f"\n[{sym}] proposed={dec.proposed_direction.upper()} "
              f"risk={'OK' if dec.risk_approved else 'REJECT'} -> {action}")
        executor.execute(dec.order, dec.price)

    # Mark to market across everything we hold (fetch quotes for held symbols not in this cycle).
    prices = dict(cycle_prices)
    for sym in state.portfolio.positions:
        if sym not in prices:
            prices[sym] = get_quote(sym).price
    equity = state.portfolio.update_peak(prices)
    state.equity_history.append(
        {"t": datetime.now(timezone.utc).isoformat(), "equity": round(equity, 2)}
    )
    state.save()

    snap = state.portfolio.snapshot(prices)
    print(f"\n--- end of cycle: equity ${snap['equity']:,.2f} "
          f"cash ${snap['cash']:,.2f} drawdown {snap['drawdown']:.2%} "
          f"positions={snap['positions']} | pending_approvals={len(state.pending_approvals)} ---")
    return state


async def run_forever(symbols: list[str] | None = None) -> None:
    settings = load_settings()
    interval = float(settings["execution"]["schedule_minutes"]) * 60.0
    print(f"Starting autonomous desk loop every {interval/60:.0f} min. Ctrl-C to stop. "
          f"(Create runs/KILL_SWITCH to halt execution without stopping the loop.)")
    while True:
        await run_cycle(symbols)
        await anyio.sleep(interval)


if __name__ == "__main__":
    import sys

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set — check algo-desk/.env")

    args = sys.argv[1:]
    seed = "--seed" in args
    symbols = [a for a in args if not a.startswith("--")] or None

    async def main():
        settings = load_settings()
        state = DeskState.load(settings["risk"]["starting_cash"])
        if seed:
            # Demo helper: pretend we already hold some BTC so a bearish cycle EXITS it,
            # exercising the executor's fill path.
            qty = 0.2
            ref = get_quote("BTC/USD").price
            state.portfolio.cash -= qty * ref
            state.portfolio.positions["BTC/USD"] = qty
            print(f"[seed] injected {qty} BTC/USD @ ${ref:,.2f} for the demo")
        await run_cycle(symbols, state)

    anyio.run(main)

"""Baseline strategy: moving-average crossover (the benchmark).

This is the simple, deterministic rule every AI agent must beat. It is intentionally
plain: if a fast moving average is above a slow one, the trend is up, so we hold the
asset (long); otherwise we go flat (cash). Long-only — no shorting — to keep the
benchmark and its accounting easy to reason about.

The function is PURE: bars in -> a decisions frame out, no side effects, no look-ahead.
Look-ahead safety: the `position` we will actually trade is shifted by one bar in the
backtester (Phase 3), because you can only act on a crossover AFTER the bar that formed
it has closed. Here we expose both the raw position and the discrete signal so the GUI
can show entries/exits.
"""

from __future__ import annotations

import pandas as pd

from src.layer.indicators import sma
from src.schemas import Direction


def crossover_strategy(
    bars: pd.DataFrame, fast: int = 20, slow: int = 50
) -> pd.DataFrame:
    """Compute the MA-crossover decisions for a bars frame.

    Returns a DataFrame aligned to `bars.index` with columns:
      * sma_fast, sma_slow : the two moving averages
      * position           : 1 = hold the asset, 0 = in cash (long-only)
      * signal             : Direction value on the bar where position changes
                             ('buy' when entering, 'sell' when exiting, else 'hold')
    """
    out = pd.DataFrame(index=bars.index)
    out["sma_fast"] = sma(bars["close"], fast)
    out["sma_slow"] = sma(bars["close"], slow)

    # Long when the fast average is above the slow average; flat otherwise.
    out["position"] = (out["sma_fast"] > out["sma_slow"]).astype(int)
    # Before both averages exist, we cannot have a position.
    warmup = out["sma_slow"].isna()
    out.loc[warmup, "position"] = 0

    # Discrete signal = the bar where position flips (entry/exit).
    delta = out["position"].diff().fillna(0)
    out["signal"] = Direction.HOLD.value
    out.loc[delta == 1, "signal"] = Direction.BUY.value
    out.loc[delta == -1, "signal"] = Direction.SELL.value
    return out


if __name__ == "__main__":
    # Smoke test on cached BTC daily bars: show every entry/exit.
    from src.layer.datafeed import get_bars

    bars = get_bars("BTC/USD", "1d", start="2025-06-01")
    dec = crossover_strategy(bars, fast=20, slow=50)
    trades = dec[dec["signal"] != Direction.HOLD.value]
    print(f"BTC/USD 20/50 crossover — {len(bars)} bars, {len(trades)} signal flips:\n")
    for ts, row in trades.iterrows():
        close = bars.loc[ts, "close"]
        print(f"  {ts.date()}  {row['signal'].upper():4s}  close={close:,.2f}")
    print(f"\nCurrently {'HOLDING' if dec['position'].iloc[-1] else 'IN CASH'}.")

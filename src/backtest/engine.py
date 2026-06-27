"""Event-driven (returns-based) backtester — the referee.

It consumes a TARGET-POSITION series (0..1 long-only here; fractional later) aligned to a
bars frame, and simulates what an account would have done.

Three correctness guarantees:
  1. NO LOOK-AHEAD: the position decided from bar N's close is applied to bar N+1's return
     (we `shift(1)`). You cannot trade on information you didn't have yet.
  2. COSTS ARE REAL: every change in position pays fees + slippage (in basis points) on the
     traded fraction, so churny strategies are penalized like in real life.
  3. SAME ENGINE FOR EVERYONE: the baseline rule and (Phase 5) the agent desk both produce a
     position series and run through this identical code, so comparisons are fair.

Output: a BacktestResult with the equity curve, per-bar returns, round-trip trades, and the
metrics scorecard from metrics.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.backtest import metrics as M


# Annualization factor per timeframe + asset class.
def periods_per_year(timeframe: str, is_crypto: bool) -> float:
    base = 365.0 if is_crypto else 252.0
    if timeframe.endswith("h"):
        hours = int(timeframe[:-1] or 1)
        return base * (24.0 / hours)
    return base  # daily


@dataclass
class BacktestResult:
    equity: pd.Series
    returns: pd.Series
    position: pd.Series
    trades: list[float]  # round-trip returns, for win rate
    stats: dict = field(default_factory=dict)

    def report(self) -> str:
        s = self.stats
        return (
            f"  Total return : {s['total_return']:+.2%}\n"
            f"  CAGR         : {s['cagr']:+.2%}\n"
            f"  Sharpe       : {s['sharpe']:.2f}\n"
            f"  Sortino      : {s['sortino']:.2f}\n"
            f"  Max drawdown : {s['max_drawdown']:.2%}  "
            f"({s['mdd_peak']} -> {s['mdd_trough']})\n"
            f"  Volatility   : {s['ann_volatility']:.2%} annualized\n"
            f"  Win rate     : {s['win_rate']:.0%} of {s['n_trades']} trades\n"
            f"  Exposure     : {s['exposure']:.0%} of time in market\n"
            f"  Final equity : {self.equity.iloc[-1]:,.2f} "
            f"(from {self.equity.iloc[0]:,.2f})"
        )


def _round_trip_returns(position_traded: pd.Series, bar_returns: pd.Series) -> list[float]:
    """Reconstruct per-trade returns from the traded position for win-rate stats.

    A 'trade' is a contiguous stretch where we held a position (>0). Its return is the
    compounded bar return over that stretch.
    """
    trades: list[float] = []
    in_trade = False
    cum = 1.0
    for pos, ret in zip(position_traded, bar_returns):
        if pos > 0:
            cum *= 1.0 + ret
            in_trade = True
        elif in_trade:  # just exited
            trades.append(cum - 1.0)
            cum = 1.0
            in_trade = False
    if in_trade:  # still holding at the end
        trades.append(cum - 1.0)
    return trades


def run_backtest(
    bars: pd.DataFrame,
    position: pd.Series,
    *,
    starting_cash: float = 100_000.0,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    timeframe: str = "1d",
    is_crypto: bool = True,
) -> BacktestResult:
    """Simulate `position` over `bars` and score it.

    fee_bps + slippage_bps are charged on the traded fraction whenever the position changes
    (10 bps = 0.10%). The defaults are deliberately conservative.
    """
    close = bars["close"]
    bar_returns = close.pct_change().fillna(0.0)

    # (1) No look-ahead: act on the *next* bar after the signal.
    pos_traded = position.shift(1).fillna(0.0).clip(0.0, 1.0)

    # (2) Costs on turnover (how much the position changed this bar).
    turnover = pos_traded.diff().abs().fillna(pos_traded.abs())
    cost_rate = (fee_bps + slippage_bps) / 10_000.0
    costs = turnover * cost_rate

    # Net per-bar strategy return, then compound into an equity curve.
    strat_returns = pos_traded * bar_returns - costs
    equity = (1.0 + strat_returns).cumprod() * starting_cash

    trades = _round_trip_returns(pos_traded, bar_returns)
    ppy = periods_per_year(timeframe, is_crypto)
    dd = M.max_drawdown(equity)

    stats = {
        "total_return": M.total_return(equity),
        "cagr": M.cagr(equity, ppy),
        "sharpe": M.sharpe(strat_returns, ppy),
        "sortino": M.sortino(strat_returns, ppy),
        "max_drawdown": dd["mdd"],
        "mdd_peak": getattr(dd["peak_date"], "date", lambda: dd["peak_date"])(),
        "mdd_trough": getattr(dd["trough_date"], "date", lambda: dd["trough_date"])(),
        "ann_volatility": M.ann_volatility(strat_returns, ppy),
        "win_rate": M.win_rate(trades),
        "n_trades": len(trades),
        "exposure": M.exposure(pos_traded),
    }
    return BacktestResult(equity=equity, returns=strat_returns, position=pos_traded,
                          trades=trades, stats=stats)


def buy_and_hold(bars: pd.DataFrame, **kwargs) -> BacktestResult:
    """Benchmark-of-the-benchmark: just hold the asset the whole time."""
    pos = pd.Series(1.0, index=bars.index)
    return run_backtest(bars, pos, **kwargs)


if __name__ == "__main__":
    # Score the baseline crossover vs. buy-and-hold on BTC.
    from src.layer.datafeed import get_bars
    from src.strategy import crossover_strategy

    bars = get_bars("BTC/USD", "1d", start="2025-06-01")
    dec = crossover_strategy(bars, fast=20, slow=50)

    strat = run_backtest(bars, dec["position"], is_crypto=True, timeframe="1d")
    hold = buy_and_hold(bars, is_crypto=True, timeframe="1d")

    print("=== Baseline: 20/50 MA crossover (BTC/USD daily) ===")
    print(strat.report())
    print("\n=== Reference: buy & hold ===")
    print(hold.report())

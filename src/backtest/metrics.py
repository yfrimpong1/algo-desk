"""Performance metrics — pure functions over an equity curve / return series.

These turn an equity curve into the standard scorecard. Every strategy and every agent
variant is scored with these same functions, so comparisons are fair.

`periods_per_year` annualizes the metrics and depends on the bar timeframe and asset:
  * crypto daily  -> 365 (trades every day)
  * equity daily  -> 252 (trading days only)
  * hourly        -> 24 * (365 or 252)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def total_return(equity: pd.Series) -> float:
    """Overall return from first to last equity value (e.g. 0.25 = +25%)."""
    if len(equity) < 2:
        return 0.0
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def cagr(equity: pd.Series, periods_per_year: float) -> float:
    """Compound annual growth rate — total return annualized by elapsed time."""
    if len(equity) < 2:
        return 0.0
    years = len(equity) / periods_per_year
    if years <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0)


def ann_volatility(returns: pd.Series, periods_per_year: float) -> float:
    """Annualized standard deviation of per-bar returns (how bumpy the ride is)."""
    return float(returns.std(ddof=0) * np.sqrt(periods_per_year))


def sharpe(returns: pd.Series, periods_per_year: float, risk_free: float = 0.0) -> float:
    """Annualized Sharpe ratio = excess return / volatility. Higher is better."""
    excess = returns - risk_free / periods_per_year
    sd = excess.std(ddof=0)
    if sd == 0 or np.isnan(sd):
        return 0.0
    return float(excess.mean() / sd * np.sqrt(periods_per_year))


def sortino(returns: pd.Series, periods_per_year: float, risk_free: float = 0.0) -> float:
    """Like Sharpe but only downside deviation counts as 'risk'."""
    excess = returns - risk_free / periods_per_year
    downside = excess[excess < 0]
    dd = downside.std(ddof=0)
    if dd == 0 or np.isnan(dd):
        return 0.0
    return float(excess.mean() / dd * np.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series) -> dict:
    """Worst peak-to-trough decline. Returns {mdd, peak_date, trough_date}.

    mdd is negative (e.g. -0.35 = the account fell 35% from a high before recovering).
    """
    running_peak = equity.cummax()
    drawdown = equity / running_peak - 1.0
    trough = drawdown.idxmin()
    mdd = float(drawdown.min())
    # Peak is the last time we were at a high before the trough.
    peak = equity.loc[:trough].idxmax() if mdd < 0 else trough
    return {"mdd": mdd, "peak_date": peak, "trough_date": trough}


def win_rate(trade_returns: list[float]) -> float:
    """Fraction of round-trip trades that were profitable (0..1)."""
    if not trade_returns:
        return 0.0
    wins = sum(1 for r in trade_returns if r > 0)
    return wins / len(trade_returns)


def exposure(position: pd.Series) -> float:
    """Fraction of bars spent holding a position (capital at work vs. idle)."""
    if len(position) == 0:
        return 0.0
    return float((position != 0).mean())
